"""feature_change_classify — unit tests. (phase-1, 2026-06-14)

Ground truth is vary_feature_catalog (USE it per task spec): synth B from
A's catalog with a known mutation, then assert the classifier reports exactly
the change. Imports the skill module directly (not via registry) so it passes
before the orchestrator registers the @skill in the manifest.

Pinned behavior (read from the source, not guessed):
  - matched pairs use feature_fidelity_diff._greedy_pair (same gates)
  - dim crosses dim_tol_pct -> 'resized'; centroid shift > move_tol_mm ->
    'moved'; both -> 'moved+resized'; neither -> 'unchanged'
  - A-only -> 'removed', B-only -> 'added'
  - transform_b applies a rigid 4x4 to B's positional vectors first
"""
from __future__ import annotations

import copy

from build123d import Box as B3dBox

from phone_designer.skills.reverse_engineer.feature_change_classify import (
    FeatureChangeClassify,
    _flatten_4x4,
)
from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
    vary_catalog,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers


def _tiny_body():
    return B3dBox(1.0, 1.0, 1.0)


def _hole(x, y, z, d=6.0, depth=4.0):
    return {
        "entry_origin": [x, y, z],
        "diameters_mm": [d],
        "diameter_mm": d,
        "depth_mm": depth,
    }


def _pocket(x, y, z, depth=3.0, width=10.0, length=12.0, top_d=10.0):
    # top_d_mm is the pocket PRIMARY dim (feature_fidelity_diff._primary_dim);
    # keeping it stable lets a depth-only change stay PAIRED (so it reads as a
    # resize) instead of failing the primary-dim pairing gate.
    return {
        "centroid": [x, y, z],
        "top_d_mm": top_d,
        "depth_mm": depth,
        "width_mm": width,
        "length_mm": length,
    }


def _boss(x, y, z, d=5.0, h=4.0):
    return {"centroid": [x, y, z], "diameter_mm": d, "height_mm": h}


def _catalog(holes=(), pockets=(), bosses=(), bbox=None):
    cat: dict = {
        "holes": list(holes),
        "pockets": list(pockets),
        "bosses": list(bosses),
    }
    if bbox is not None:
        cat["initial_bbox_mm"] = list(bbox)
    return cat


def _run(ca, cb, **kw):
    res = FeatureChangeClassify().apply(
        _tiny_body(), {"catalog_a": ca, "catalog_b": cb, **kw},
    )
    return res.extras["feature_changes"]


# ──────────────────────────────────────────────────────────────────────────────
# Identical catalogs -> everything unchanged


def test_identical_catalogs_all_unchanged():
    ca = _catalog(
        holes=[_hole(-8, 0, 0, d=3.0), _hole(8, 0, 0, d=6.0)],
        pockets=[_pocket(0, -8, 0)],
        bosses=[_boss(0, 8, 0)],
    )
    cb = copy.deepcopy(ca)
    rep = _run(ca, cb)

    c = rep["summary_counts"]
    assert c["unchanged"] == 4
    assert c["moved"] == 0 and c["resized"] == 0
    assert c["moved+resized"] == 0
    assert c["removed"] == 0 and c["added"] == 0
    # Every record carries a zero centroid shift and zero-delta dims.
    for kind_records in rep["by_kind"].values():
        for r in kind_records:
            assert r["change"] == "unchanged"
            if r["centroid_shift_mm"] is not None:
                assert r["centroid_shift_mm"] < 1e-9


# ──────────────────────────────────────────────────────────────────────────────
# THE GROUND-TRUTH SCENARIO (task spec item 3): vary one pocket depth +2mm and
# remove one hole -> exactly 1 'resized' (delta≈2.0±0.05), 1 'removed', rest
# 'unchanged'.


def test_pocket_depth_plus2_and_one_hole_removed():
    ca = _catalog(
        holes=[_hole(-8, 0, 0, d=3.0), _hole(8, 0, 0, d=6.0)],
        pockets=[_pocket(0, -8, 0, depth=3.0), _pocket(0, 12, 0, depth=5.0)],
    )
    # Synthesize B from A via vary_feature_catalog: pocket[0] depth 3 -> 5
    # (absolute override = +2mm) and drop the LAST hole.
    cb = vary_catalog(ca, absolute_overrides={"pockets.0.depth_mm": 5.0})
    cb["holes"] = cb["holes"][:1]  # remove hole index 1

    rep = _run(ca, cb)
    c = rep["summary_counts"]

    assert c["resized"] == 1
    assert c["removed"] == 1
    assert c["moved"] == 0
    assert c["moved+resized"] == 0
    assert c["added"] == 0
    # Remaining: hole[0] unchanged + pocket[1] unchanged = 2.
    assert c["unchanged"] == 2

    # The single resized pocket reports depth delta ≈ +2.0.
    resized = [
        r for recs in rep["by_kind"].values() for r in recs
        if r["change"] == "resized"
    ]
    assert len(resized) == 1
    depth_delta = next(
        d for d in resized[0]["dim_deltas"] if d["name"] == "depth_mm")
    assert abs(depth_delta["delta"] - 2.0) <= 0.05
    assert abs(depth_delta["pct"] - (2.0 / 3.0 * 100.0)) <= 0.5

    # The removed hole carries A's dims, b_entry None.
    removed = [
        r for recs in rep["by_kind"].values() for r in recs
        if r["change"] == "removed"
    ]
    assert len(removed) == 1
    assert removed[0]["b_entry"] is None
    assert removed[0]["a_entry"] is not None


# ──────────────────────────────────────────────────────────────────────────────
# Move one boss 5mm -> 'moved' with centroid_shift ≈ 5.0


def test_move_one_boss_5mm_is_moved():
    ca = _catalog(
        bosses=[_boss(0, 0, 0), _boss(20, 0, 0)],
    )
    cb = copy.deepcopy(ca)
    # Shift boss[0] +5mm in Y (within the 5mm xyz gate so it still pairs).
    cb["bosses"][0]["centroid"] = [0.0, 5.0, 0.0]

    rep = _run(ca, cb)
    c = rep["summary_counts"]

    assert c["moved"] == 1
    assert c["resized"] == 0
    assert c["unchanged"] == 1

    moved = [
        r for recs in rep["by_kind"].values() for r in recs
        if r["change"] == "moved"
    ]
    assert len(moved) == 1
    assert abs(moved[0]["centroid_shift_mm"] - 5.0) <= 0.05


# ──────────────────────────────────────────────────────────────────────────────
# Added feature (B-only)


def test_added_feature_in_b():
    ca = _catalog(holes=[_hole(0, 0, 0)])
    cb = _catalog(holes=[_hole(0, 0, 0), _hole(30, 0, 0)])
    rep = _run(ca, cb)
    c = rep["summary_counts"]
    assert c["added"] == 1
    assert c["unchanged"] == 1
    added = [
        r for recs in rep["by_kind"].values() for r in recs
        if r["change"] == "added"
    ]
    assert len(added) == 1
    assert added[0]["a_entry"] is None
    assert added[0]["b_entry"] is not None


# ──────────────────────────────────────────────────────────────────────────────
# moved+resized when both change at once


def test_moved_and_resized_simultaneously():
    ca = _catalog(pockets=[_pocket(0, 0, 0, depth=3.0)])
    cb = copy.deepcopy(ca)
    cb["pockets"][0]["centroid"] = [0.0, 4.0, 0.0]  # moved 4mm > 0.5 tol
    cb["pockets"][0]["depth_mm"] = 6.0              # +100% > 1% tol
    rep = _run(ca, cb)
    assert rep["summary_counts"]["moved+resized"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# transform_b: a raw (un-registered) B paired only after applying the 4x4


def test_transform_b_applies_registration_before_pairing():
    # B is the same part translated +100mm in X. Without the transform the
    # holes are 100mm apart and fail the 5mm gate -> removed+added. WITH the
    # inverse translation B lands back on A -> unchanged.
    ca = _catalog(holes=[_hole(0, 0, 0), _hole(10, 0, 0)])
    cb = _catalog(holes=[_hole(100, 0, 0), _hole(110, 0, 0)])

    # No transform: both removed (A) + both added (B).
    rep0 = _run(ca, cb)
    assert rep0["summary_counts"]["removed"] == 2
    assert rep0["summary_counts"]["added"] == 2
    assert rep0["transform_applied"] is False

    # transform_b = translate -100 in X -> B re-enters A's frame.
    T = [
        [1.0, 0.0, 0.0, -100.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    rep1 = _run(ca, cb, transform_b=T)
    assert rep1["transform_applied"] is True
    assert rep1["summary_counts"]["unchanged"] == 2
    assert rep1["summary_counts"]["removed"] == 0
    assert rep1["summary_counts"]["added"] == 0


def test_flatten_4x4_accepts_nested_and_flat():
    R, t = _flatten_4x4([
        [1, 0, 0, 5], [0, 1, 0, 6], [0, 0, 1, 7], [0, 0, 0, 1],
    ])
    assert list(t) == [5.0, 6.0, 7.0]
    assert R[0][0] == 1.0 and R[1][1] == 1.0 and R[2][2] == 1.0
    # Flat 12-float affine.
    R2, t2 = _flatten_4x4([1, 0, 0, 2, 0, 1, 0, 3, 0, 0, 1, 4])
    assert list(t2) == [2.0, 3.0, 4.0]


# ──────────────────────────────────────────────────────────────────────────────
# read-only body


def test_body_unchanged():
    body = _tiny_body()
    res = FeatureChangeClassify().apply(
        body, {"catalog_a": _catalog(), "catalog_b": _catalog()})
    assert res.body is body
