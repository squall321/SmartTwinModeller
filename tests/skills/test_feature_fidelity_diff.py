"""feature_fidelity_diff — unit tests for the headline RE metric.

The skill works on plain catalog dicts; no real geometry is required
beyond a tiny build123d box passed as the (unused) ``body`` argument so
the ``body_present`` post-condition is satisfied.

Behavior pinned here (read from the source, not guessed):

    - identical catalogs            → overall_match_ratio == 1.0
    - both catalogs empty           → ratio == 1.0 (union_total == 0 branch)
    - dropped / invented entries    → denominator is sum-over-kinds of
                                      max(len_a, len_b) ("union"), so missing
                                      AND extra entries penalize equally
    - dim drift gate (_greedy_pair) → holes use _TOL_DIM_FRAC_BY_KIND["holes"]
                                      == 0.08; rejection is strict (> tol)
    - xyz gate (_adaptive_xyz_tol)  → max(5.0 floor, 0.5% of the larger
                                      initial_bbox_mm diagonal); rejection is
                                      strict (d > tol), so d == tol pairs
    - frame_translation_mm on catalog_b remaps box-local coords back to
      world before pairing (b_world = b_local - stashed shift)
    - _xyz_of prefers entry_origin over axis_origin (pass-23 contract)
"""
from __future__ import annotations

import copy

import pytest
from build123d import Box as B3dBox

from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
    _TOL_DIM_FRAC_BY_KIND,
    _TOL_XYZ_BBOX_FRAC,
    _TOL_XYZ_MM_FLOOR,
    FeatureFidelityDiff,
    _adaptive_xyz_tol,
    _greedy_pair,
    _xyz_of,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers


def _tiny_body():
    """Body is irrelevant to the diff — only body_present must hold."""
    return B3dBox(1.0, 1.0, 1.0)


def _hole(x, y, z, d=6.0, depth=4.0, **extra):
    e = {
        "entry_origin": [x, y, z],
        "diameters_mm": [d],
        "diameter_mm": d,
        "depth_mm": depth,
    }
    e.update(extra)
    return e


def _pocket(x, y, z, depth=3.0, width=10.0):
    return {"centroid": [x, y, z], "depth_mm": depth, "width_mm": width}


def _catalog(holes=(), pockets=(), bbox=None, **extra):
    cat: dict = {"holes": list(holes), "pockets": list(pockets)}
    if bbox is not None:
        cat["initial_bbox_mm"] = list(bbox)
    cat.update(extra)
    return cat


def _diff(ca, cb):
    res = FeatureFidelityDiff().apply(
        _tiny_body(), {"catalog_a": ca, "catalog_b": cb},
    )
    return res.extras["feature_fidelity"]


# ──────────────────────────────────────────────────────────────────────────────
# Identical / empty catalogs


def test_identical_catalogs_match_ratio_1():
    ca = _catalog(
        holes=[_hole(-8, 0, 0, d=3.0), _hole(8, 0, 0, d=6.0), _hole(0, 8, 0, d=12.0)],
        pockets=[_pocket(0, -8, 0)],
    )
    cb = copy.deepcopy(ca)
    rep = _diff(ca, cb)

    assert rep["overall_match_ratio"] == 1.0
    assert rep["missing_in_b"] == []
    assert rep["extra_in_b"] == []
    assert rep["by_kind"]["holes"] == {"a": 3, "b": 3, "diff": 0, "matched": 3}
    assert rep["by_kind"]["pockets"] == {"a": 1, "b": 1, "diff": 0, "matched": 1}
    # Pairs exist and every shared *_mm key is identical → drift is 0.0,
    # not None (None is reserved for "no pairs at all").
    assert rep["avg_dim_drift_pct"] == 0.0


def test_empty_catalogs_ratio_is_1_and_drift_none():
    rep = _diff({}, {})
    assert rep["overall_match_ratio"] == 1.0  # union_total == 0 branch
    assert rep["avg_dim_drift_pct"] is None
    assert rep["missing_in_b"] == []
    assert rep["extra_in_b"] == []
    # No bbox info on either side → xyz tol collapses to the 5 mm floor.
    assert rep["xyz_tol_mm"] == pytest.approx(_TOL_XYZ_MM_FLOOR)


# ──────────────────────────────────────────────────────────────────────────────
# Union denominator — missing and invented entries penalize equally


def test_dropped_hole_ratio_reflects_union_denominator():
    holes = [_hole(-8, 0, 0, d=3.0), _hole(8, 0, 0, d=6.0), _hole(0, 8, 0, d=12.0)]
    ca = _catalog(holes=holes)
    cb = _catalog(holes=copy.deepcopy(holes[:2]))  # drop the LAST hole

    rep = _diff(ca, cb)
    # union = max(3, 2) = 3, matched = 2
    assert rep["overall_match_ratio"] == round(2 / 3, 4)
    assert rep["by_kind"]["holes"] == {"a": 3, "b": 2, "diff": -1, "matched": 2}
    assert rep["missing_in_b"] == [("holes", 2)]
    assert rep["extra_in_b"] == []


def test_invented_hole_penalizes_like_a_missing_one():
    holes = [_hole(-8, 0, 0, d=3.0), _hole(8, 0, 0, d=6.0), _hole(0, 8, 0, d=12.0)]
    ca = _catalog(holes=holes)
    cb = _catalog(holes=copy.deepcopy(holes) + [_hole(0, -8, 0, d=2.0)])

    rep = _diff(ca, cb)
    # union = max(3, 4) = 4, matched = 3 — INVENTED entries hit the same
    # denominator as missing ones.
    assert rep["overall_match_ratio"] == round(3 / 4, 4)
    assert rep["by_kind"]["holes"] == {"a": 3, "b": 4, "diff": 1, "matched": 3}
    assert rep["missing_in_b"] == []
    assert rep["extra_in_b"] == [("holes", 3)]


# ──────────────────────────────────────────────────────────────────────────────
# Dimension-drift gate (kind-specific tolerance, strict > rejection)


def test_holes_dim_tolerance_is_8_percent():
    # Pin the constant the gate reads — M3 vs M4 must stay distinct.
    assert _TOL_DIM_FRAC_BY_KIND["holes"] == 0.08


def test_dim_drift_under_tolerance_still_pairs():
    ca = _catalog(holes=[_hole(0, 0, 0, d=10.0)])
    cb = _catalog(holes=[_hole(0, 0, 0, d=10.7)])  # 7% < 8% gate

    rep = _diff(ca, cb)
    assert rep["overall_match_ratio"] == 1.0
    assert rep["by_kind"]["holes"]["matched"] == 1
    # diameter_mm drifted 7%, depth_mm 0% → mean over the two shared keys.
    assert rep["avg_dim_drift_pct"] == pytest.approx(3.5, abs=0.01)
    # Breakdown surfaces WHICH dim drifted.
    bd = rep["drift_breakdown"]["holes"]["diameter_mm"]
    assert bd["pair_count"] == 1
    assert bd["mean_pct"] == pytest.approx(7.0, abs=0.01)


def test_dim_drift_over_tolerance_splits_the_pair():
    ca = _catalog(holes=[_hole(0, 0, 0, d=10.0)])
    cb = _catalog(holes=[_hole(0, 0, 0, d=11.0)])  # 10% > 8% gate

    rep = _diff(ca, cb)
    assert rep["overall_match_ratio"] == 0.0
    assert rep["by_kind"]["holes"]["matched"] == 0
    assert rep["missing_in_b"] == [("holes", 0)]
    assert rep["extra_in_b"] == [("holes", 0)]


def test_greedy_pair_dim_gate_rejection_is_strictly_greater():
    # _greedy_pair rejects on ``drift > tol_dim_frac`` — drift EXACTLY at
    # the tolerance pairs. Use an explicit tol override with binary-exact
    # numbers (8.0 → 10.0 is exactly 25%).
    a = [_hole(0, 0, 0, d=8.0)]
    b = [_hole(0, 0, 0, d=10.0)]
    pairs, ua, ub = _greedy_pair(a, b, "holes", tol_xyz_mm=5.0, tol_dim_frac=0.25)
    assert [(ai, bi) for ai, bi, _ in pairs] == [(0, 0)]
    assert ua == [] and ub == []

    pairs, ua, ub = _greedy_pair(a, b, "holes", tol_xyz_mm=5.0, tol_dim_frac=0.2499)
    assert pairs == []
    assert ua == [0] and ub == [0]


# ──────────────────────────────────────────────────────────────────────────────
# Adaptive xyz tolerance


def test_adaptive_xyz_tol_floor_and_bbox_scaling():
    # No bbox anywhere → the 5 mm phone-scale floor.
    assert _adaptive_xyz_tol({}, {}) == pytest.approx(_TOL_XYZ_MM_FLOOR)
    # 3-4-5 bbox triangle scaled to a 5000 mm diagonal → 0.5% = 25 mm,
    # which beats the floor. The LARGER of the two diagonals wins,
    # whichever side carries it.
    big = _catalog(bbox=[0, 0, 0, 3000, 4000, 0])
    expected = 5000.0 * _TOL_XYZ_BBOX_FRAC
    assert _adaptive_xyz_tol(big, {}) == pytest.approx(expected)
    assert _adaptive_xyz_tol({}, big) == pytest.approx(expected)
    # Malformed bbox (too short) is ignored → floor.
    assert _adaptive_xyz_tol({"initial_bbox_mm": [0, 0, 0]}, {}) == pytest.approx(
        _TOL_XYZ_MM_FLOOR
    )


def test_displacement_at_exact_floor_tolerance_pairs():
    # Gate is ``d > tol`` (strict) — a 5.0 mm displacement on a no-bbox
    # catalog sits EXACTLY on the floor and must still pair.
    ca = _catalog(holes=[_hole(0.0, 0.0, 0.0)])
    cb = _catalog(holes=[_hole(5.0, 0.0, 0.0)])
    rep = _diff(ca, cb)
    assert rep["xyz_tol_mm"] == pytest.approx(_TOL_XYZ_MM_FLOOR)
    assert rep["overall_match_ratio"] == 1.0


def test_displacement_over_floor_tolerance_fails_to_pair():
    ca = _catalog(holes=[_hole(0.0, 0.0, 0.0)])
    cb = _catalog(holes=[_hole(5.5, 0.0, 0.0)])
    rep = _diff(ca, cb)
    assert rep["overall_match_ratio"] == 0.0
    assert rep["missing_in_b"] == [("holes", 0)]


def test_large_bbox_widens_the_gate_for_the_same_displacement():
    # 20 mm displacement: fails on the 5 mm floor, pairs once the a-side
    # bbox diagonal (5000 mm → 25 mm tol) widens the gate.
    ca_small = _catalog(holes=[_hole(0, 0, 0)])
    cb = _catalog(holes=[_hole(20.0, 0, 0)])
    assert _diff(ca_small, cb)["overall_match_ratio"] == 0.0

    ca_big = _catalog(holes=[_hole(0, 0, 0)], bbox=[0, 0, 0, 3000, 4000, 0])
    rep = _diff(ca_big, cb)
    assert rep["xyz_tol_mm"] == pytest.approx(25.0)
    assert rep["overall_match_ratio"] == 1.0


# ──────────────────────────────────────────────────────────────────────────────
# frame_translation_mm remap (box-local regen catalog vs world original)


def test_frame_translation_remaps_box_local_catalog_into_world():
    # Original (a) in WORLD coords; regen (b) in box-local coords with the
    # runner-stashed world→box shift. b_world = b_local - shift, so
    # local (10, 5, 0) with shift (-90, -45, 0) lands on world (100, 50, 0).
    ca = _catalog(holes=[_hole(100.0, 50.0, 0.0)])
    cb = _catalog(holes=[_hole(10.0, 5.0, 0.0)])
    cb["frame_translation_mm"] = [-90.0, -45.0, 0.0]

    rep = _diff(ca, cb)
    assert rep["overall_match_ratio"] == 1.0
    assert rep["by_kind"]["holes"]["matched"] == 1


def test_without_frame_translation_the_same_catalogs_do_not_pair():
    ca = _catalog(holes=[_hole(100.0, 50.0, 0.0)])
    cb = _catalog(holes=[_hole(10.0, 5.0, 0.0)])  # no stash → ~100 mm apart
    rep = _diff(ca, cb)
    assert rep["overall_match_ratio"] == 0.0


def test_malformed_frame_translation_is_ignored():
    ca = _catalog(holes=[_hole(100.0, 50.0, 0.0)])
    cb = _catalog(holes=[_hole(10.0, 5.0, 0.0)])
    cb["frame_translation_mm"] = [-90.0]  # too short → treated as (0,0,0)
    rep = _diff(ca, cb)
    assert rep["overall_match_ratio"] == 0.0


# ──────────────────────────────────────────────────────────────────────────────
# _xyz_of key preference (pass-23: entry_origin over axis_origin)


def test_xyz_of_prefers_entry_origin_over_axis_origin():
    e = {"entry_origin": [1.0, 2.0, 3.0], "axis_origin": [9.0, 9.0, 9.0]}
    assert _xyz_of(e) == (1.0, 2.0, 3.0)
    # frame_offset is ADDED to the picked key.
    assert _xyz_of(e, frame_offset=(10.0, 20.0, 30.0)) == (11.0, 22.0, 33.0)


def test_xyz_of_falls_back_through_the_key_chain():
    assert _xyz_of({"axis_origin": [4.0, 5.0, 6.0]}) == (4.0, 5.0, 6.0)
    assert _xyz_of({"centroid": [7.0, 8.0, 9.0]}) == (7.0, 8.0, 9.0)
    # Non-numeric values in a preferred key fall through to the next one.
    assert _xyz_of({"entry_origin": ["x", "y", "z"], "centroid": [1.0, 1.0, 1.0]}) \
        == (1.0, 1.0, 1.0)
    assert _xyz_of({}) is None


def test_entry_origin_preference_drives_pairing():
    # Same entry_origin, wildly different axis_origins: pairs ONLY because
    # entry_origin wins the key chain.
    ca = _catalog(holes=[_hole(0, 0, 0, axis_origin=[50.0, 50.0, 50.0])])
    cb = _catalog(holes=[_hole(0, 0, 0, axis_origin=[-50.0, -50.0, -50.0])])
    rep = _diff(ca, cb)
    assert rep["overall_match_ratio"] == 1.0


# ──────────────────────────────────────────────────────────────────────────────
# _greedy_pair degenerate inputs


def test_greedy_pair_without_xyz_or_dims_collapses_to_first_fit():
    # Symmetry/pattern catalogs carry neither xyz nor *_mm dims — the gates
    # are no-ops and pairing is count-overlap (first-fit).
    a = [{"kind": "mirror"}, {"kind": "mirror"}]
    b = [{"kind": "mirror"}]
    pairs, ua, ub = _greedy_pair(a, b, "symmetries")
    assert [(ai, bi) for ai, bi, _ in pairs] == [(0, 0)]
    assert ua == [1]
    assert ub == []


def test_greedy_pair_non_dict_entries_are_unmatched():
    pairs, ua, ub = _greedy_pair(["not-a-dict"], [_hole(0, 0, 0)], "holes")
    assert pairs == []
    assert ua == [0]
    assert ub == [0]
