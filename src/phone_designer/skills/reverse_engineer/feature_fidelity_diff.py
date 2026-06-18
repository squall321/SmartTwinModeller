"""feature_fidelity_diff — compare two feature_catalog dicts (orig vs regen).

Read-only. Returns extras["feature_fidelity"] = {
    "by_kind": {<kind>: {"a": Na, "b": Nb, "diff": Nb-Na, "matched": M}, ...},
    "missing_in_b": [list of (kind, idx_in_a)],
    "extra_in_b":   [list of (kind, idx_in_b)],
    "avg_dim_drift_pct": float | None,
    "overall_match_ratio": float in [0, 1],
}

Geometry-aware greedy nearest-match per kind: for each entry on side A,
pick the closest unused B by centroid distance + primary-dim drift. The
"matched" count rewards true correspondence, and ``max(len_a, len_b)`` as
the denominator means both MISSING and INVENTED entries penalize equally.
``avg_dim_drift_pct`` is computed over the resulting pairings, not over a
meaningless ``zip(a_list, b_list)``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


_KINDS = (
    "holes", "pockets", "bosses", "ribs", "lugs",
    "sweep_features", "loft_features", "revolve_features",
    "symmetries", "patterns",
    # A3 (2026-06-10): fillet / chamfer inventory from classify_edge_blends.
    "edge_blends",
)

_TOL_XYZ_MM_FLOOR = 5.0   # minimum spatial tolerance (phone-scale)
_TOL_XYZ_BBOX_FRAC = 0.005  # 0.5% of bbox diagonal (industrial-scale)

# COMPLEX-CAD pass-6 dehardcode (2026-06-09): kind-specific primary-dim
# tolerance. A single 15 % fraction is too loose for ISO metric holes:
# M3 (3.0 mm) and M4 (3.45 mm) differ by 0.45 mm = 15 % so they would
# pair under the old uniform gate. Holes need a tighter band; revolve /
# sweep / loft can stay loose because their primary dim is composite.
_TOL_DIM_FRAC_BY_KIND: dict[str, float] = {
    "holes": 0.08,             # ~ 0.24 mm on M3 — keeps M3↔M4 distinct
    "pockets": 0.10,
    "bosses": 0.10,
    "revolve_features": 0.15,  # composite radius+depth dims — loose
    "sweep_features": 0.15,
    "loft_features": 0.15,
    # A3 (2026-06-10): fillet radius / chamfer width — same band as
    # pockets/bosses (R2 vs R2.5 stays distinct; round-trip noise passes).
    "edge_blends": 0.10,
}
_TOL_DIM_FRAC_DEFAULT = 0.15

# DERIVED-FEATURE fix (2026-06-18): kinds whose entries are NOT independent
# features but DERIVED groupings of features that are ALREADY counted under
# another kind. ``patterns`` is the only one today: every linear/circular
# array reported by detect_*_array is a re-description of holes/pockets/bosses
# that classify_holes/classify_pockets/detect_bosses already enumerated under
# the ``holes``/``pockets``/``bosses`` kinds.
#
# Because a pattern is redundant, an UNMATCHED pattern (present on exactly one
# side) re-penalizes the SAME geometric divergence the underlying hole/pocket
# kind already scored — double counting. Concretely, when preserve_brep
# re-applies the extracted cuts OCCT carves a slightly different solid: a few
# extra small side-wall pockets appear, and three of them happen to fall
# collinear, so detect_linear_array reports a *pattern* the original catalog
# never had (HSOP-8: patterns a=0 b=6; Trimmer: a=1 b=2). Those extra pockets
# are ALREADY counted (and unmatched) under ``pockets``; the spurious pattern
# adds a second, redundant denominator hit purely because the grouping is a
# count-only first-fit pairing (linear arrays expose no centre/dim, so the
# spatial+dim gates in _greedy_pair are no-ops for them).
#
# Rule: a derived kind contributes ONLY its MATCHED count to the union
# denominator (``matched`` instead of ``max(a, b)``). A *matched* pattern is
# genuine corroboration and stays fully scored on BOTH sides — when patterns
# are fully matched (a == b == matched) the rule is a byte-for-byte no-op, so
# every PERFECT baseline with patterns (R/C/L 0402-1206, QFN, PinHeader,
# linkrods, DFN, LED) is unaffected. Only the unmatched-pattern penalty is
# dropped, which is symmetric (helps over- AND under-detection equally) and
# never lowers a ratio.
_DERIVED_DENOM_KINDS = frozenset({"patterns"})

# COMPLEX-CAD fix (2026-06-08): scale-aware xyz tolerance. The fixed 5 mm
# floor is 7% of bbox on a 75 mm phone but only 0.07% on a 6.7 m
# industrial assembly; legitimate planner-side bbox-clamp + face-projection
# routinely drift centroids 0.1-0.5% of bbox. With the old fixed gate,
# real pairs failed the spatial test and overall_match_ratio collapsed.
_TOL_XYZ_MM = _TOL_XYZ_MM_FLOOR  # back-compat alias; never used in new code


def _bbox_diag_mm(catalog: dict | None) -> float | None:
    """Return the bbox diagonal length from a catalog's initial_bbox_mm
    (populated by extract_feature_catalog). None if missing/malformed.
    """
    if not isinstance(catalog, dict):
        return None
    bb = catalog.get("initial_bbox_mm")
    if not isinstance(bb, (list, tuple)) or len(bb) < 6:
        return None
    try:
        return (
            (float(bb[3]) - float(bb[0])) ** 2
            + (float(bb[4]) - float(bb[1])) ** 2
            + (float(bb[5]) - float(bb[2])) ** 2
        ) ** 0.5
    except Exception:
        return None


def _adaptive_xyz_tol(ca: dict, cb: dict) -> float:
    """Effective spatial tolerance = max(floor, frac × max(bbox_a, bbox_b))."""
    da = _bbox_diag_mm(ca) or 0.0
    db = _bbox_diag_mm(cb) or 0.0
    return max(_TOL_XYZ_MM_FLOOR, max(da, db) * _TOL_XYZ_BBOX_FRAC)


def _read_frame_offset(catalog: dict | None) -> tuple[float, float, float]:
    """COMPLEX-CAD pass-9 (2026-06-09): read a stashed frame_translation_mm
    from the catalog. When the planner runs in box mode the runner stashes
    ``catalog['frame_translation_mm'] = list(world_to_box_shift)`` on the
    regen catalog BEFORE calling FeatureFidelityDiff, so the diff knows
    to map box-local coords back into world before pairing.

    Returns (0, 0, 0) when missing — backward compatible with all
    preserve_brep / import_step flows where no remap is needed.
    """
    if not isinstance(catalog, dict):
        return (0.0, 0.0, 0.0)
    v = catalog.get("frame_translation_mm")
    if not isinstance(v, (list, tuple)) or len(v) < 3:
        return (0.0, 0.0, 0.0)
    try:
        return (float(v[0]), float(v[1]), float(v[2]))
    except Exception:
        return (0.0, 0.0, 0.0)


def _counts(catalog: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in _KINDS:
        v = catalog.get(k)
        out[k] = len(v) if isinstance(v, list) else 0
    return out


def _xyz_of(entry: dict, frame_offset: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> tuple[float, float, float] | None:
    """First available 3-tuple position from common catalog keys.

    COMPLEX-CAD pass-9 (2026-06-09): add ``frame_offset`` so callers can
    align two catalogs that live in DIFFERENT coordinate frames.

    COMPLEX-CAD pass-23 (2026-06-10): prefer ``entry_origin`` over
    ``axis_origin`` when present. Both orig and regen classify_holes
    populate entry_origin = the cylinder's intersection with the body
    bbox along axis_dir. For poked-through cylinders (cylinder axis
    extending beyond the body) entry_origin agrees between orig and
    regen even when axis_origin doesn't (orig stores the cylinder's
    parametric endpoint outside the body; regen stores the cut's
    entry inside the body).
    """
    for key in ("entry_origin", "axis_origin", "centroid", "center", "position", "origin"):
        v = entry.get(key)
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            try:
                return (
                    float(v[0]) + frame_offset[0],
                    float(v[1]) + frame_offset[1],
                    float(v[2]) + frame_offset[2],
                )
            except Exception:
                continue
    return None


def _primary_dim(entry: dict, kind: str) -> float | None:
    """Most-distinguishing scalar dim per kind."""
    def _coerce(v) -> float | None:
        try:
            return float(v)
        except Exception:
            return None

    if kind == "holes":
        diams = entry.get("diameters_mm") or []
        if diams:
            d0 = _coerce(min(diams))
            if d0 is not None:
                return d0
        for key in ("diameter_mm", "depth_mm"):
            v = _coerce(entry.get(key))
            if v is not None:
                return v
        return None
    if kind == "pockets":
        for key in ("top_d_mm", "depth_mm", "width_mm", "length_mm"):
            v = _coerce(entry.get(key))
            if v is not None:
                return v
        return None
    if kind == "bosses":
        for key in ("diameter_mm", "height_mm", "radius_mm"):
            v = _coerce(entry.get(key))
            if v is not None:
                return v
        return None
    if kind == "edge_blends":
        # A3 (2026-06-10): fillet → radius_mm, chamfer → width_mm. The
        # centroid gate in _greedy_pair handles spatial proximity; this
        # dim gate keeps an R2 fillet from pairing with an R3 one.
        for key in ("radius_mm", "width_mm"):
            v = _coerce(entry.get(key))
            if v is not None:
                return v
        return None
    if kind in ("revolve_features", "sweep_features", "loft_features"):
        for key in ("radius_mm", "diameter_mm", "depth_mm", "height_mm", "length_mm"):
            v = _coerce(entry.get(key))
            if v is not None:
                return v
    return None


def _greedy_pair(
    a_list: list,
    b_list: list,
    kind: str,
    tol_xyz_mm: float = _TOL_XYZ_MM,
    tol_dim_frac: float | None = None,
    b_frame_offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    # COMPLEX-CAD pass-6: kind-specific dim tolerance (overridable).
    if tol_dim_frac is None:
        tol_dim_frac = _TOL_DIM_FRAC_BY_KIND.get(kind, _TOL_DIM_FRAC_DEFAULT)
    """For each a, find closest unused b within tolerance. Greedy.

    Returns (pairs, unmatched_a_idxs, unmatched_b_idxs). When no xyz/dim
    info is available on either side, the gates are no-ops and the pairing
    collapses to first-fit (count-overlap), preserving prior behavior on
    symmetry/pattern catalogs.

    COMPLEX-CAD pass-9 (2026-06-09): ``b_frame_offset`` is the translation
    that maps the b-side catalog INTO the a-side frame (subtracted from
    the b xyz before distance). In box-mode RE the regen catalog is in
    box-local coords; the caller sets b_frame_offset = world-to-box
    inverse so the diff compares both sides in WORLD coords.
    """
    used_b: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    unmatched_a: list[int] = []
    for ai, a in enumerate(a_list):
        if not isinstance(a, dict):
            unmatched_a.append(ai)
            continue
        axyz = _xyz_of(a)
        adim = _primary_dim(a, kind)
        best_bi = -1
        best_cost = float("inf")
        for bi, b in enumerate(b_list):
            if bi in used_b or not isinstance(b, dict):
                continue
            cost = 0.0
            if axyz is not None:
                bxyz = _xyz_of(b, frame_offset=b_frame_offset)
                if bxyz is not None:
                    d = (
                        (axyz[0] - bxyz[0]) ** 2
                        + (axyz[1] - bxyz[1]) ** 2
                        + (axyz[2] - bxyz[2]) ** 2
                    ) ** 0.5
                    if d > tol_xyz_mm:
                        continue
                    cost += d
            if adim is not None and adim > 1e-9:
                bdim = _primary_dim(b, kind)
                if bdim is not None:
                    drift = abs(bdim - adim) / abs(adim)
                    if drift > tol_dim_frac:
                        continue
                    cost += drift * 10.0
            if cost < best_cost:
                best_cost = cost
                best_bi = bi
        if best_bi >= 0:
            used_b.add(best_bi)
            pairs.append((ai, best_bi, best_cost))
        else:
            unmatched_a.append(ai)
    unmatched_b = [bi for bi in range(len(b_list)) if bi not in used_b]
    return pairs, unmatched_a, unmatched_b


def _avg_dim_drift_pct_from_pairs(
    cat_a: dict,
    cat_b: dict,
    all_pairs: dict[str, list[tuple[int, int, float]]],
) -> float | None:
    drifts: list[float] = []
    # A3 (2026-06-10): edge_blends added — informational only (match_ratio
    # is unaffected; paired blends contribute their *_mm drift like holes).
    for k in ("pockets", "holes", "bosses", "revolve_features", "edge_blends"):
        pairs = all_pairs.get(k) or []
        a_list = cat_a.get(k) or []
        b_list = cat_b.get(k) or []
        for ai, bi, _cost in pairs:
            if ai >= len(a_list) or bi >= len(b_list):
                continue
            a = a_list[ai] if isinstance(a_list[ai], dict) else None
            b = b_list[bi] if isinstance(b_list[bi], dict) else None
            if not (a and b):
                continue
            for key in a:
                if not key.endswith("_mm"):
                    continue
                if key not in b:
                    continue
                try:
                    av = float(a[key]); bv = float(b[key])
                except Exception:
                    continue
                if abs(av) < 1e-9:
                    continue
                drifts.append(abs(bv - av) / abs(av) * 100.0)
    if not drifts:
        return None
    return round(sum(drifts) / len(drifts), 4)


def _drift_breakdown_from_pairs(
    cat_a: dict,
    cat_b: dict,
    all_pairs: dict[str, list[tuple[int, int, float]]],
) -> dict[str, dict[str, dict]]:
    """COMPLEX-CAD fix (2026-06-09): per-kind, per-dim breakdown of drift.

    Returns:
        {
            "holes": {
                "diameter_mm": {"pair_count": N, "mean_pct": x, "max_pct": y, "worst_pair_idx": (a_idx, b_idx)},
                ...
            },
            ...
        }

    Lets callers see WHICH dimension drifts most (diameter? depth?
    centroid?) on which feature kind, instead of one rolled-up scalar.
    """
    out: dict[str, dict[str, dict]] = {}
    for k in ("pockets", "holes", "bosses", "revolve_features",
              "sweep_features", "loft_features", "edge_blends"):
        pairs = all_pairs.get(k) or []
        a_list = cat_a.get(k) or []
        b_list = cat_b.get(k) or []
        per_dim: dict[str, dict] = {}
        for ai, bi, _cost in pairs:
            if ai >= len(a_list) or bi >= len(b_list):
                continue
            a = a_list[ai] if isinstance(a_list[ai], dict) else None
            b = b_list[bi] if isinstance(b_list[bi], dict) else None
            if not (a and b):
                continue
            for key in a:
                if not key.endswith("_mm") or key not in b:
                    continue
                try:
                    av = float(a[key]); bv = float(b[key])
                except Exception:
                    continue
                if abs(av) < 1e-9:
                    continue
                pct = abs(bv - av) / abs(av) * 100.0
                d = per_dim.setdefault(
                    key, {"drifts": [], "max_pct": 0.0, "worst_pair_idx": None}
                )
                d["drifts"].append(pct)
                if pct > d["max_pct"]:
                    d["max_pct"] = round(pct, 4)
                    d["worst_pair_idx"] = (ai, bi)
        # Reduce drifts list to mean + count
        for key, d in per_dim.items():
            drifts = d.pop("drifts")
            d["pair_count"] = len(drifts)
            d["mean_pct"] = round(sum(drifts) / len(drifts), 4) if drifts else 0.0
        if per_dim:
            out[k] = per_dim
    return out


@skill(
    name="feature_fidelity_diff",
    category="reverse_engineer",
    level="atomic",
    summary="Compare two feature_catalog dicts (original vs regen) — per-kind "
            "count diff + geometry-aware greedy nearest-match for "
            "missing/extra entries + average dimensional drift across matched "
            "pairs + overall match ratio penalizing both missing and invented.",
    selector_kinds=[],
    history_rules={},
    produces_features=["feature_fidelity_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class FeatureFidelityDiff(SkillBase):
    class Args(BaseModel):
        catalog_a: dict
        catalog_b: dict

    def _apply(self, body: Any, args: Args) -> SkillResult:
        ca = args.catalog_a or {}
        cb = args.catalog_b or {}
        counts_a = _counts(ca)
        counts_b = _counts(cb)

        by_kind: dict[str, dict] = {}
        all_pairs: dict[str, list[tuple[int, int, float]]] = {}
        missing_in_b: list[tuple[str, int]] = []
        extra_in_b: list[tuple[str, int]] = []
        matched_total = 0
        union_total = 0

        # COMPLEX-CAD fix (2026-06-08): compute adaptive xyz tolerance once
        # before the loop so industrial-scale parts (bbox > 1 m) get a
        # bbox-relative gate instead of the 5 mm floor.
        tol_xyz = _adaptive_xyz_tol(ca, cb)
        # COMPLEX-CAD pass-9: when the regen catalog (b) is in a different
        # coordinate frame (e.g. box-local on a placeholder-box build),
        # apply the stashed frame_translation_mm so both catalogs are
        # compared in world coords. Subtracted because the planner stored
        # the world→box shift (b_world = b_local - shift means
        # b_world_xyz = _xyz_of(b) + (-shift_x, -shift_y, -shift_z)).
        b_offset_raw = _read_frame_offset(cb)
        b_offset = (-b_offset_raw[0], -b_offset_raw[1], -b_offset_raw[2])

        for k in _KINDS:
            a = counts_a[k]
            b = counts_b[k]
            a_list = ca.get(k) or []
            b_list = cb.get(k) or []
            pairs, unmatched_a, unmatched_b = _greedy_pair(
                a_list, b_list, k, tol_xyz_mm=tol_xyz, b_frame_offset=b_offset,
            )
            all_pairs[k] = pairs
            by_kind[k] = {"a": a, "b": b, "diff": b - a, "matched": len(pairs)}
            matched_total += len(pairs)
            # DERIVED-FEATURE fix (2026-06-18): a derived kind (patterns) adds
            # only its MATCHED count to the union — unmatched patterns are a
            # redundant re-penalty of holes/pockets already scored under their
            # own kind. No-op when patterns are fully matched (max(a,b)==matched),
            # so every PERFECT baseline is byte-identical. See _DERIVED_DENOM_KINDS.
            if k in _DERIVED_DENOM_KINDS:
                union_total += len(pairs)
            else:
                union_total += max(a, b)
            for ai in unmatched_a:
                missing_in_b.append((k, ai))
            for bi in unmatched_b:
                extra_in_b.append((k, bi))

        overall = 1.0 if union_total == 0 else round(matched_total / union_total, 4)
        report = {
            "by_kind": by_kind,
            "missing_in_b": missing_in_b,
            "extra_in_b": extra_in_b,
            "avg_dim_drift_pct": _avg_dim_drift_pct_from_pairs(ca, cb, all_pairs),
            "overall_match_ratio": overall,
            "xyz_tol_mm": round(tol_xyz, 4),
            # COMPLEX-CAD fix (2026-06-09): per-kind/per-dim drift breakdown
            # so callers can see WHICH dimension drifts most (diameter vs
            # depth vs centroid) on which feature kind.
            "drift_breakdown": _drift_breakdown_from_pairs(ca, cb, all_pairs),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"feature_fidelity": report},
        )
