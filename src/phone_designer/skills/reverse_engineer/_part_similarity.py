"""_part_similarity — composite similarity + classification (phase-2, 2026-06-14).

Pure helpers for compare_parts. Two registered parts (A, B) yield a stack of
signals — feature match_ratio, registration rmsd (normalised by bbox diagonal),
volume / surface-area deltas, topology counts, and the matched-dimension ratio
spread — that fold into:

  1. ``similarity_score`` in [0, 1] — a composite scalar.
  2. ``classification`` — a label + supporting evidence:

       identical          : sim > 0.98 AND hausdorff < hausdorff_tol_mm
       parametric_variant : high feature correspondence AND the matched
                            dimensions share a consistent global scale (the
                            per-pair B/A dimension ratios cluster tightly) ->
                            scale_factor estimated by least squares
       same_family_resized: high feature correspondence + a global size change
                            but the dimension ratios are NOT uniform (different
                            axes scaled differently)
       design_change      : some features added / removed but the core feature
                            set is shared (match_ratio still substantial)
       unrelated          : little feature correspondence

result_grade = 'estimate' on the classifier (heuristic thresholds).
"""
from __future__ import annotations

import math
from typing import Any

# Reused (NOT forked): the same per-kind matching + position extraction the
# rest of the RE stack uses, so similarity reads pairs the same way.
from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
    _KINDS,
    _greedy_pair,
)

# ── classification thresholds (heuristic — result_grade='estimate') ─────────
_SIM_IDENTICAL = 0.98
_MATCH_RATIO_VARIANT = 0.75   # high feature correspondence
_MATCH_RATIO_DESIGN = 0.40    # core feature set still shared
_SCALE_CV_UNIFORM = 0.05      # dim-ratio coefficient-of-variation for "uniform"
_SCALE_NOOP_BAND = 0.02       # |scale-1| below this => no real resize


def _bbox_diag(catalog: dict | None) -> float | None:
    if not isinstance(catalog, dict):
        return None
    bb = catalog.get("initial_bbox_mm")
    if not isinstance(bb, (list, tuple)) or len(bb) < 6:
        return None
    try:
        return math.sqrt(
            (float(bb[3]) - float(bb[0])) ** 2
            + (float(bb[4]) - float(bb[1])) ** 2
            + (float(bb[5]) - float(bb[2])) ** 2
        )
    except Exception:
        return None


def _topology_counts(catalog: dict | None) -> dict[str, int]:
    out: dict[str, int] = {}
    if not isinstance(catalog, dict):
        return out
    for k in _KINDS:
        v = catalog.get(k)
        out[k] = len(v) if isinstance(v, list) else 0
    return out


def topology_match(cat_a: dict, cat_b: dict) -> float:
    """Scale-INVARIANT structural fingerprint: per-kind feature-count overlap.

    For each kind, ``min(Na, Nb) / max(Na, Nb)`` (1.0 when equal, 0 when one
    side is empty), aggregated as a count-weighted mean over the kinds present
    on either side. Unlike ``feature_match_ratio`` this never reads positions
    or dimensions, so a uniformly-scaled part — whose features sit at scaled
    coordinates and carry scaled diameters and therefore FAIL the absolute
    spatial + dim-drift gates of ``_greedy_pair`` — still scores ~1.0 here. It
    is the primary tell that B is the SAME part resized, not an unrelated one.
    """
    ca = _topology_counts(cat_a)
    cb = _topology_counts(cat_b)
    num = 0.0
    den = 0.0
    for k in _KINDS:
        na = ca.get(k, 0)
        nb = cb.get(k, 0)
        hi = max(na, nb)
        if hi == 0:
            continue
        num += min(na, nb)        # count-weighted: kinds with more features
        den += hi                 # dominate the mean
    return float(num / den) if den > 0 else 1.0


def feature_match_ratio(cat_a: dict, cat_b: dict) -> tuple[float, int, int, int]:
    """Greedy per-kind correspondence between two catalogs (B assumed already
    in A's frame). Returns (match_ratio, matched, total_a, total_b).

    ``match_ratio = matched / max(union, 1)`` where union = max(total_a,
    total_b) per kind summed — both MISSING and INVENTED features penalise,
    mirroring feature_fidelity_diff's overall_match_ratio.
    """
    matched = 0
    total_a = 0
    total_b = 0
    union = 0
    for kind in _KINDS:
        a_list = cat_a.get(kind) or []
        b_list = cat_b.get(kind) or []
        na = len(a_list) if isinstance(a_list, list) else 0
        nb = len(b_list) if isinstance(b_list, list) else 0
        total_a += na
        total_b += nb
        union += max(na, nb)
        if not na or not nb:
            continue
        pairs, _ua, _ub = _greedy_pair(a_list, b_list, kind)
        matched += len(pairs)
    ratio = 1.0 if union == 0 else matched / union
    return float(ratio), matched, total_a, total_b


def _matched_dim_ratios(cat_a: dict, cat_b: dict) -> list[float]:
    """Per matched-pair B/A ratios over every shared positive ``*_mm`` scalar
    dimension. The spread of these ratios distinguishes a UNIFORM rescale
    (all ratios equal -> parametric_variant) from a non-uniform family resize.
    B is assumed already mapped into A's frame (so positional pairing is sane);
    scalar dimensions are frame-invariant so the ratios are read directly.
    """
    ratios: list[float] = []
    for kind in _KINDS:
        a_list = cat_a.get(kind) or []
        b_list = cat_b.get(kind) or []
        if not (isinstance(a_list, list) and isinstance(b_list, list)):
            continue
        if not a_list or not b_list:
            continue
        pairs, _ua, _ub = _greedy_pair(a_list, b_list, kind)
        for ai, bi, _cost in pairs:
            a = a_list[ai] if ai < len(a_list) else None
            b = b_list[bi] if bi < len(b_list) else None
            if not (isinstance(a, dict) and isinstance(b, dict)):
                continue
            for key in a:
                if not key.endswith("_mm") or key not in b:
                    continue
                try:
                    av = float(a[key])
                    bv = float(b[key])
                except (TypeError, ValueError):
                    continue
                if av > 1e-6 and bv > 1e-6:
                    ratios.append(bv / av)
    return ratios


def _scale_stats(ratios: list[float]) -> tuple[float | None, float | None]:
    """Least-squares-consistent global scale (mean ratio) + coefficient of
    variation (std/mean) of the per-dimension B/A ratios. Returns (None, None)
    when there are too few ratios to be meaningful.
    """
    if len(ratios) < 2:
        if len(ratios) == 1:
            return float(ratios[0]), 0.0
        return None, None
    mean = sum(ratios) / len(ratios)
    if mean <= 1e-9:
        return None, None
    var = sum((r - mean) ** 2 for r in ratios) / len(ratios)
    cv = math.sqrt(var) / mean
    return float(mean), float(cv)


def _sorted_dim_ratios(cat_a: dict, cat_b: dict) -> list[float]:
    """Scale-aware B/A dimension ratios WITHOUT positional pairing.

    For each kind with equal feature counts on both sides, sort each side's
    primary ``*_mm`` dimensions ascending and zip — a uniform scale preserves
    the rank order of feature sizes, so the i-th smallest hole in A maps to the
    i-th smallest in B even though their absolute positions differ by the scale
    (which defeats ``_greedy_pair``'s spatial gate). This recovers the scale of
    a parametric variant the positional matcher cannot.
    """
    ratios: list[float] = []
    for kind in _KINDS:
        a_list = cat_a.get(kind) or []
        b_list = cat_b.get(kind) or []
        if not (isinstance(a_list, list) and isinstance(b_list, list)):
            continue
        if len(a_list) == 0 or len(a_list) != len(b_list):
            continue
        for key in ("diameter_mm", "top_d_mm", "depth_mm", "width_mm",
                    "length_mm", "height_mm", "radius_mm"):
            av = sorted(
                v for v in (_as_float_field(e, key) for e in a_list)
                if v is not None and v > 1e-6)
            bv = sorted(
                v for v in (_as_float_field(e, key) for e in b_list)
                if v is not None and v > 1e-6)
            if len(av) == len(bv) and av:
                for a, b in zip(av, bv):
                    ratios.append(b / a)
    return ratios


def _as_float_field(entry: Any, key: str) -> float | None:
    if not isinstance(entry, dict):
        return None
    v = entry.get(key)
    # diameters_mm is a list — read its min as the primary diameter.
    if v is None and key == "diameter_mm":
        diams = entry.get("diameters_mm")
        if isinstance(diams, list) and diams:
            try:
                return float(min(diams))
            except (TypeError, ValueError):
                return None
    try:
        return float(v) if v is not None and not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return None


def _volume_scale_factor(vol_delta_pct: float | None) -> float | None:
    """Cube-root of the volume ratio -> implied uniform linear scale. A pure
    geometric corroboration of the dimension-ratio scale (volume grows as the
    cube of a uniform linear scale)."""
    if vol_delta_pct is None:
        return None
    ratio = 1.0 + float(vol_delta_pct) / 100.0
    if ratio <= 0.0:
        return None
    return float(ratio ** (1.0 / 3.0))


def _bbox_scale_factor(cat_a: dict, cat_b: dict) -> tuple[float | None, float | None]:
    """Per-axis B/A bbox-extent ratios -> (mean scale, cv) as a fallback when
    feature-dimension ratios are sparse. Uniform box growth (cv≈0) is the
    strongest tell of a uniform parametric scale even with few features.
    """
    ba = cat_a.get("initial_bbox_mm") if isinstance(cat_a, dict) else None
    bb = cat_b.get("initial_bbox_mm") if isinstance(cat_b, dict) else None
    if not (isinstance(ba, (list, tuple)) and len(ba) == 6):
        return None, None
    if not (isinstance(bb, (list, tuple)) and len(bb) == 6):
        return None, None
    ratios: list[float] = []
    for axis in range(3):
        try:
            ea = abs(float(ba[axis + 3]) - float(ba[axis]))
            eb = abs(float(bb[axis + 3]) - float(bb[axis]))
        except (TypeError, ValueError):
            continue
        if ea > 1e-6 and eb > 1e-6:
            ratios.append(eb / ea)
    return _scale_stats(ratios)


def similarity_and_classification(
    cat_a: dict,
    cat_b: dict,
    registration: dict | None,
    geometry_deviation: dict | None,
    mass_delta: dict | None,
    hausdorff_tol_mm: float = 0.5,
) -> dict[str, Any]:
    """Fold the comparison signals into similarity_score + classification.

    ``cat_b`` is expected to already be in A's frame (the macro pre-applies the
    registration transform to B's catalog) so the feature pairing + dim ratios
    are read in a common frame. Returns::

        {
          "similarity_score": float,        # [0, 1]
          "classification": str,            # label
          "scale_factor": float | None,     # estimated global scale (variant)
          "scale_uniformity_cv": float | None,
          "evidence": {feature_match_ratio, rmsd_norm, volume_delta_pct,
                       surface_area_delta_pct, hausdorff_mm,
                       topology_counts_a, topology_counts_b, n_dim_ratios},
          "result_grade": "estimate",
        }
    """
    cat_a = cat_a or {}
    cat_b = cat_b or {}

    match_ratio, matched, total_a, total_b = feature_match_ratio(cat_a, cat_b)
    # Scale-invariant structural correspondence — the parametric-variant tell
    # that survives the spatial/dim gates a uniform rescale defeats.
    topo_match = topology_match(cat_a, cat_b)

    # rmsd normalised by the larger bbox diagonal -> scale-free residual term.
    diag = max(_bbox_diag(cat_a) or 0.0, _bbox_diag(cat_b) or 0.0)
    rmsd = None
    if isinstance(registration, dict):
        rmsd = registration.get("rmsd_mm")
    rmsd_norm = None
    if rmsd is not None and diag > 1e-6:
        rmsd_norm = float(rmsd) / diag

    hausdorff = None
    vol_delta = None
    area_delta = None
    if isinstance(geometry_deviation, dict):
        hausdorff = geometry_deviation.get("hausdorff_mm")
        vol_delta = geometry_deviation.get("volume_delta_pct")
        area_delta = geometry_deviation.get("surface_area_delta_pct")
    if vol_delta is None and isinstance(mass_delta, dict):
        vol_delta = mass_delta.get("volume_delta_pct")

    # ── global scale estimate, layered most-precise first ─────────────────
    # (1) positionally-paired feature dim ratios (works at scale≈1, in-frame);
    # (2) rank-sorted feature dim ratios (scale-aware, position-free);
    # (3) bbox-extent ratios; (4) cube-root of the volume delta. Each yields a
    # (scale, cv) pair; we take the first that is populated. The cv (ratio
    # spread) decides uniform (-> parametric_variant) vs non-uniform
    # (-> same_family_resized).
    dim_ratios = _matched_dim_ratios(cat_a, cat_b)
    scale, scale_cv = _scale_stats(dim_ratios)
    n_dim_ratios = len(dim_ratios)
    scale_source = "paired_feature_dims" if scale is not None else None
    if scale is None:
        sorted_ratios = _sorted_dim_ratios(cat_a, cat_b)
        scale, scale_cv = _scale_stats(sorted_ratios)
        n_dim_ratios = len(sorted_ratios)
        if scale is not None:
            scale_source = "sorted_feature_dims"
    if scale is None:
        scale, scale_cv = _bbox_scale_factor(cat_a, cat_b)
        if scale is not None:
            scale_source = "bbox_extents"
    if scale is None:
        scale = _volume_scale_factor(vol_delta)
        scale_cv = 0.0 if scale is not None else None
        if scale is not None:
            scale_source = "volume_cube_root"

    # ── composite similarity score ────────────────────────────────────────
    # Weighted blend of (a) feature correspondence, (b) registration quality,
    # (c) geometric closeness. Each sub-term is in [0, 1]; missing terms drop
    # out and the weights renormalise so a PMI-less / featureless part still
    # scores honestly off whatever signal exists. The correspondence term is
    # the MAX of the positional match_ratio and the scale-invariant topology
    # match, so a uniformly-scaled variant (high topo, low positional match)
    # still scores as highly similar.
    correspondence = max(
        max(0.0, min(1.0, match_ratio)), max(0.0, min(1.0, topo_match)))
    terms: list[tuple[float, float]] = []  # (weight, value)
    terms.append((0.45, correspondence))
    if rmsd_norm is not None:
        terms.append((0.20, max(0.0, 1.0 - min(1.0, rmsd_norm / 0.02))))
    # Geometric term: shrink with hausdorff relative to bbox diagonal, and with
    # the absolute volume delta. A pure resize is geometrically FAR (large
    # hausdorff) yet still a high-similarity parametric variant, so the
    # geometric term is down-weighted and the feature term dominates.
    if hausdorff is not None and diag > 1e-6:
        terms.append((0.15, max(0.0, 1.0 - min(1.0, float(hausdorff) / (0.1 * diag)))))
    if vol_delta is not None:
        terms.append((0.20, max(0.0, 1.0 - min(1.0, abs(float(vol_delta)) / 100.0))))

    wsum = sum(w for w, _ in terms)
    similarity = (
        sum(w * v for w, v in terms) / wsum if wsum > 1e-9 else 0.0
    )
    similarity = float(max(0.0, min(1.0, similarity)))

    # ── classification ────────────────────────────────────────────────────
    # Correspondence gate uses the SCALE-INVARIANT topology match (the
    # positional match_ratio collapses under a uniform rescale and would
    # mislabel a clean scaled variant as 'unrelated'). match_ratio still gates
    # the strict in-place cases (identical / design_change) where positions
    # are expected to agree.
    haus_ok = hausdorff is not None and float(hausdorff) < hausdorff_tol_mm
    resized = scale is not None and abs(scale - 1.0) >= _SCALE_NOOP_BAND
    uniform = scale_cv is not None and scale_cv <= _SCALE_CV_UNIFORM
    high_corr = topo_match >= _MATCH_RATIO_VARIANT
    core_shared = topo_match >= _MATCH_RATIO_DESIGN

    scale_out: float | None = None
    if similarity > _SIM_IDENTICAL and haus_ok and not resized:
        classification = "identical"
        scale_out = 1.0 if (scale is not None) else None
    elif high_corr and resized and uniform:
        # Same structure, one consistent global scale across all dims.
        classification = "parametric_variant"
        scale_out = round(float(scale), 6)
    elif high_corr and resized and not uniform:
        # Same structure, but axes / features scaled by DIFFERENT factors.
        classification = "same_family_resized"
        scale_out = round(float(scale), 6)
    elif high_corr and not resized:
        # High structural correspondence, no global resize. Geometrically
        # identical -> 'identical'; otherwise an in-place design tweak.
        if haus_ok and total_a == total_b:
            classification = "identical"
            scale_out = 1.0
        else:
            classification = "design_change"
    elif core_shared:
        # Core feature set shared but some features added / removed.
        classification = "design_change"
        if resized:
            scale_out = round(float(scale), 6)
    else:
        classification = "unrelated"

    return {
        "similarity_score": round(similarity, 6),
        "classification": classification,
        "scale_factor": scale_out,
        "scale_uniformity_cv": (round(scale_cv, 6) if scale_cv is not None else None),
        "scale_source": scale_source,
        "evidence": {
            "feature_match_ratio": round(match_ratio, 6),
            "topology_match": round(topo_match, 6),
            "matched_features": matched,
            "total_features_a": total_a,
            "total_features_b": total_b,
            "rmsd_norm": (round(rmsd_norm, 6) if rmsd_norm is not None else None),
            "volume_delta_pct": vol_delta,
            "surface_area_delta_pct": area_delta,
            "hausdorff_mm": hausdorff,
            "bbox_diag_mm": round(diag, 4) if diag else None,
            "topology_counts_a": _topology_counts(cat_a),
            "topology_counts_b": _topology_counts(cat_b),
            "n_dim_ratios": n_dim_ratios,
        },
        "result_grade": "estimate",
    }
