"""intersect + transform (move / mirror / scale / split) — Tier-1 general-CAD verbs.

Pins the base body-level operations a general parametric CAD toolkit needs, which
the library previously lacked: the third boolean (intersect/common) and the four
body transforms (move, mirror, scale, split). Volumes are exact analytic values.
Also pins the honest refusals — a non-overlapping intersect, an ambiguous scale
mode, a zero axis/normal — and that all five round-trip via generate_from_spec and
publish in the manifest.

Box alignment note: build123d Box(...) with align=(CENTER,CENTER,CENTER) is centred
on the origin; the create-skill `box` op uses (CENTER,CENTER,MIN) (sits on z=0).
Tests build their own centred boxes for exact, alignment-independent geometry.
"""
from __future__ import annotations

import pytest
from build123d import Align, Box
from pydantic import ValidationError

from phone_designer.skills.compose.intersect import Intersect
from phone_designer.skills.transform.mirror_body import MirrorBody
from phone_designer.skills.transform.move_body import MoveBody
from phone_designer.skills.transform.scale_body import ScaleBody
from phone_designer.skills.transform.split_body import SplitBody


def _vol(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(g.Mass())


def _box(l, w, h, pos=(0, 0, 0)):
    b = Box(l, w, h, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    b.position = pos
    return b


def _rv(res):  # result volume
    return _vol(res.body.wrapped)


# ── intersect (common) ───────────────────────────────────────────────────────

def test_intersect_overlap_is_common_volume():
    # a 20³ box ∩ an identical box shifted +10 in X → overlap 10×20×20 = 4000.
    r = Intersect().apply(_box(20, 20, 20), {"other": {
        "kind": "primitive_box", "length_mm": 20, "width_mm": 20,
        "height_mm": 20, "position": [10, 0, 0]}})
    assert _rv(r) == pytest.approx(4000.0, rel=1e-6)


def test_intersect_non_overlapping_refused():
    with pytest.raises(ValueError, match="no_intersection"):
        Intersect().apply(_box(20, 20, 20), {"other": {
            "kind": "primitive_box", "length_mm": 5, "width_mm": 5,
            "height_mm": 5, "position": [100, 0, 0]}})


# ── move_body ────────────────────────────────────────────────────────────────

def test_move_body_is_volume_invariant():
    for kw in ({"translate_mm": [30, 0, 0]},
               {"rotate_axis": [0, 0, 1], "rotate_deg": 45},
               {"translate_mm": [5, -3, 2], "rotate_axis": [1, 1, 0], "rotate_deg": 30}):
        r = MoveBody().apply(_box(20, 20, 20), kw)
        assert _rv(r) == pytest.approx(8000.0, rel=1e-6)


def test_move_body_rotate_about_offset_origin_repositions():
    # rotate 90° about Z through (10,0,0): a box centred at origin swings to a new
    # centre but keeps its volume — proves the T·R·T⁻¹ compose about an origin.
    r = MoveBody().apply(_box(10, 10, 10), {
        "rotate_axis": [0, 0, 1], "rotate_deg": 90, "rotate_origin_mm": [10, 0, 0]})
    assert _rv(r) == pytest.approx(1000.0, rel=1e-6)


def test_move_body_leave_copy_returns_two_bodies():
    r = MoveBody().apply(_box(20, 20, 20), {"translate_mm": [30, 0, 0], "leave_copy": True})
    assert _rv(r) == pytest.approx(16000.0, rel=1e-6)
    assert r.extras["transform"]["n_bodies"] == 2


def test_move_body_zero_rotation_axis_refused():
    with pytest.raises(ValueError, match="zero_rotation_axis"):
        MoveBody().apply(_box(10, 10, 10), {"rotate_axis": [0, 0, 0], "rotate_deg": 30})


# ── mirror_body ──────────────────────────────────────────────────────────────

def test_mirror_body_reflects_and_preserves_volume():
    # a 10³ box on the +x side, mirrored across YZ → a congruent box on −x side.
    r = MirrorBody().apply(_box(10, 10, 10, pos=(15, 0, 0)), {"plane_normal": [1, 0, 0]})
    assert _rv(r) == pytest.approx(1000.0, rel=1e-6)


def test_mirror_body_merge_doubles_volume():
    # model-half → mirror-merge: two disjoint congruent halves fuse to 2× volume.
    r = MirrorBody().apply(_box(10, 10, 10, pos=(15, 0, 0)),
                           {"plane_normal": [1, 0, 0], "merge": True})
    assert _rv(r) == pytest.approx(2000.0, rel=1e-6)


def test_mirror_body_zero_normal_refused():
    with pytest.raises(ValueError, match="zero_plane_normal"):
        MirrorBody().apply(_box(10, 10, 10), {"plane_normal": [0, 0, 0]})


# ── scale_body ───────────────────────────────────────────────────────────────

def test_scale_body_uniform_cubes_the_factor():
    r = ScaleBody().apply(_box(20, 20, 20), {"factor": 2.0})
    assert _rv(r) == pytest.approx(64000.0, rel=1e-6)  # 8000 × 2³


def test_scale_body_anisotropic_multiplies_per_axis():
    r = ScaleBody().apply(_box(20, 20, 20), {"factors_xyz": [2, 3, 1]})
    assert _rv(r) == pytest.approx(48000.0, rel=1e-6)  # 8000 × 2·3·1
    assert r.extras["transform"]["mode"] == "anisotropic"


def test_scale_body_ambiguous_mode_refused():
    # neither, or both, of factor / factors_xyz is a validation-time refusal.
    with pytest.raises(ValidationError, match="scale_mode_ambiguous"):
        ScaleBody().apply(_box(10, 10, 10), {})
    with pytest.raises(ValidationError, match="scale_mode_ambiguous"):
        ScaleBody().apply(_box(10, 10, 10), {"factor": 2, "factors_xyz": [1, 1, 1]})


def test_scale_body_non_positive_factor_refused():
    with pytest.raises(ValidationError, match="non_positive_factor"):
        ScaleBody().apply(_box(10, 10, 10), {"factor": 0})


# ── split_body ───────────────────────────────────────────────────────────────

def test_split_body_halves_by_plane():
    # 20³ centred box split at z=0 → two 20×20×10 halves = 4000 each.
    box = _box(20, 20, 20)
    pos = SplitBody().apply(box, {"plane_normal": [0, 0, 1], "keep": "positive"})
    neg = SplitBody().apply(box, {"plane_normal": [0, 0, 1], "keep": "negative"})
    assert _rv(pos) == pytest.approx(4000.0, rel=1e-6)
    assert _rv(neg) == pytest.approx(4000.0, rel=1e-6)
    assert pos.extras["transform"]["n_pieces_total"] == 2


def test_split_body_keep_both_is_full_volume_in_two_pieces():
    r = SplitBody().apply(_box(20, 20, 20), {"plane_normal": [0, 0, 1], "keep": "both"})
    assert _rv(r) == pytest.approx(8000.0, rel=1e-6)
    assert r.extras["transform"]["n_pieces_kept"] == 2


def test_split_body_diagonal_plane_cuts_two_pieces():
    r = SplitBody().apply(_box(20, 20, 20),
                          {"plane_origin_mm": [0, 0, 0], "plane_normal": [1, 1, 0]})
    assert _rv(r) == pytest.approx(8000.0, rel=1e-6)
    assert r.extras["transform"]["n_pieces_total"] == 2


def test_split_body_plane_missing_body_selects_nothing_on_one_side():
    # a plane well outside the body leaves one uncut piece; keeping the empty side
    # is a structured refusal, not a bogus zero-volume "body".
    with pytest.raises(ValueError, match="split_no_cut"):
        SplitBody().apply(_box(10, 10, 10, pos=(0, 0, 0)),
                          {"plane_origin_mm": [100, 0, 0], "plane_normal": [1, 0, 0],
                           "keep": "positive"})


# ── from-scratch via generate_from_spec (MCP) + discoverability ──────────────

def test_all_five_reachable_via_generate_from_spec():
    from phone_designer.skills.create.generate_from_spec import GenerateFromSpec

    def gen(spec):
        return GenerateFromSpec().apply(None, {"spec": spec}).extras["generated"]

    gi = gen([{"op": "box", "args": {"length_mm": 20, "width_mm": 20, "height_mm": 20}},
              {"op": "intersect", "args": {"other": {
                  "kind": "primitive_box", "length_mm": 20, "width_mm": 20,
                  "height_mm": 20, "position": [10, 0, 0]}}}])
    assert gi["ok"] and gi["is_solid"]
    gm = gen([{"op": "box", "args": {"length_mm": 20, "width_mm": 20, "height_mm": 20}},
              {"op": "move_body", "args": {"translate_mm": [30, 0, 0]}}])
    assert gm["ok"] and gm["volume_mm3"] == pytest.approx(8000.0, rel=1e-4)
    gs = gen([{"op": "box", "args": {"length_mm": 20, "width_mm": 20, "height_mm": 20}},
              {"op": "scale_body", "args": {"factor": 2.0}}])
    assert gs["ok"] and gs["volume_mm3"] == pytest.approx(64000.0, rel=1e-4)


def test_transform_skills_are_in_the_manifest():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    assert {"intersect", "move_body", "mirror_body", "scale_body", "split_body"} <= names
    mv = next(s for s in m["skills"] if s["name"] == "move_body")
    assert mv["category"] == "transform" and mv["level"] == "atomic"
    it = next(s for s in m["skills"] if s["name"] == "intersect")
    assert it["category"] == "compose"
