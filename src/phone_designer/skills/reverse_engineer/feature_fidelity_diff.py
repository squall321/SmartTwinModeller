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
)

_TOL_XYZ_MM_FLOOR = 5.0   # minimum spatial tolerance (phone-scale)
_TOL_XYZ_BBOX_FRAC = 0.005  # 0.5% of bbox diagonal (industrial-scale)
_TOL_DIM_FRAC = 0.15  # +/- 15% primary-dim drift considered "same feature"

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


def _counts(catalog: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in _KINDS:
        v = catalog.get(k)
        out[k] = len(v) if isinstance(v, list) else 0
    return out


def _xyz_of(entry: dict) -> tuple[float, float, float] | None:
    """First available 3-tuple position from common catalog keys."""
    for key in ("axis_origin", "centroid", "center", "position", "origin"):
        v = entry.get(key)
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            try:
                return (float(v[0]), float(v[1]), float(v[2]))
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
    tol_dim_frac: float = _TOL_DIM_FRAC,
) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    """For each a, find closest unused b within tolerance. Greedy.

    Returns (pairs, unmatched_a_idxs, unmatched_b_idxs). When no xyz/dim
    info is available on either side, the gates are no-ops and the pairing
    collapses to first-fit (count-overlap), preserving prior behavior on
    symmetry/pattern catalogs.
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
                bxyz = _xyz_of(b)
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
    for k in ("pockets", "holes", "bosses", "revolve_features"):
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

        for k in _KINDS:
            a = counts_a[k]
            b = counts_b[k]
            a_list = ca.get(k) or []
            b_list = cb.get(k) or []
            pairs, unmatched_a, unmatched_b = _greedy_pair(
                a_list, b_list, k, tol_xyz_mm=tol_xyz,
            )
            all_pairs[k] = pairs
            by_kind[k] = {"a": a, "b": b, "diff": b - a, "matched": len(pairs)}
            matched_total += len(pairs)
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
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"feature_fidelity": report},
        )
