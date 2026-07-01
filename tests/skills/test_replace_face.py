"""replace_face — retarget a solid's selected PLANAR face to a new plane.

Pins the SolidWorks Replace/Move-Face op (robust v1): a plane replaces the
selected face's plane and the MATERIAL-side piece is kept, re-trimming the
adjacent walls. Volumes are exact analytic values for a 20×20×20 box:

    replace top (z=20) with a plane at z=17 (normal +Z) → box z∈[0,17]
        vol = 20 · 20 · 17 = 6800 mm³
    equivalently offset_mm = -3 on the top face → same 6800 mm³.

Also pins the honest refusals — an ambiguous / no-match selector, a curved
target, an under-specified replacement (both/neither mode), and a plane that
leaves no material-side piece.

Box alignment note: the create-skill `box` op uses align (CENTER,CENTER,MIN),
so a 20³ box sits on z=0 with its top at z=20 — the exact geometry these
analytic volumes assume.
"""
from __future__ import annotations

import pytest
from build123d import Align, Box

# Import the skill module under test at the top (registers @skill).
from phone_designer.skills.modify_curvature.replace_face import ReplaceFace


def _vol(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(g.Mass())


def _box_on_z0(l, w, h):
    """A box sitting on z=0 (bottom at z=0, top at z=h) — matches the `box`
    create-skill convention align=(CENTER,CENTER,MIN)."""
    return Box(l, w, h, align=(Align.CENTER, Align.CENTER, Align.MIN))


def _rv(res):
    return _vol(res.body.wrapped)


# ── the headline analytic case: top z=20 → z=17, vol 6800 ────────────────────

def test_replace_top_with_absolute_plane_shortens_box():
    # 20×20×20 box (top at z=20). Replace the top face with a plane at z=17,
    # normal +Z → the box is shortened to z∈[0,17], vol = 20·20·17 = 6800.
    box = _box_on_z0(20, 20, 20)
    r = ReplaceFace().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "plane_origin_mm": [0, 0, 17], "plane_normal": [0, 0, 1]})
    assert _rv(r) == pytest.approx(6800.0, rel=1e-9)
    assert r.extras["transform"]["mode"] == "plane"
    assert r.extras["transform"]["n_pieces_kept"] == 1


def test_replace_top_via_offset_matches_absolute():
    # offset_mm = -3 on the top face (outward normal +Z) moves the plane from
    # z=20 to z=17 → identical 6800 mm³.
    box = _box_on_z0(20, 20, 20)
    r = ReplaceFace().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "offset_mm": -3.0})
    assert _rv(r) == pytest.approx(6800.0, rel=1e-9)
    assert r.extras["transform"]["mode"] == "offset"
    # replacement plane origin sits at the target-face centroid + (-3)·(+Z)
    assert r.extras["transform"]["replacement_origin_mm"][2] == pytest.approx(17.0)


def test_positive_offset_outside_body_is_a_noop_trim():
    # HONEST LIMIT of the trim-based v1: offset_mm = +5 puts the replacement
    # plane at z=25, ABOVE the solid (top at z=20). A plane-trim cannot ADD
    # material where none exists — the plane misses the body, so the whole
    # original box is on the material side and the volume is unchanged (8000).
    # (A true modeling grow would need a face-extend, out of scope for v1.)
    box = _box_on_z0(20, 20, 20)
    r = ReplaceFace().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "offset_mm": 5.0})
    assert _rv(r) == pytest.approx(8000.0, rel=1e-9)
    assert r.extras["transform"]["n_pieces_total"] == 1


def test_replace_side_face_shrinks_in_x():
    # Replace the +X face (right, at x=+10 for a 20-wide box centred in X) with
    # a plane at x=+4 → width in X becomes 14 (x∈[-10,4]) → vol 14·20·20 = 5600.
    box = _box_on_z0(20, 20, 20)
    r = ReplaceFace().apply(box, {
        "face_selector": {"kind": "face_named", "name": "right"},
        "plane_origin_mm": [4, 0, 0], "plane_normal": [1, 0, 0]})
    assert _rv(r) == pytest.approx(5600.0, rel=1e-9)


# ── honest refusals ──────────────────────────────────────────────────────────

def test_ambiguous_selector_refused():
    # faces_by_normal +Z with a loose tolerance still matches only the top for a
    # box, so force ambiguity with a selector matching all 6 faces via area.
    box = _box_on_z0(20, 20, 20)
    with pytest.raises(ValueError, match="face_selector_ambiguous"):
        ReplaceFace().apply(box, {
            "face_selector": {"kind": "faces_by_area", "min": 1.0},
            "offset_mm": -3.0})


def test_no_match_selector_refused():
    box = _box_on_z0(20, 20, 20)
    with pytest.raises(ValueError, match="face_selector_no_match"):
        ReplaceFace().apply(box, {
            "face_selector": {"kind": "faces_by_area", "min": 1e9},
            "offset_mm": -3.0})


def test_underspecified_both_modes_refused():
    from pydantic import ValidationError
    box = _box_on_z0(20, 20, 20)
    with pytest.raises(ValidationError, match="replace_underspecified"):
        ReplaceFace().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "offset_mm": -3.0, "plane_origin_mm": [0, 0, 17],
            "plane_normal": [0, 0, 1]})


def test_underspecified_neither_mode_refused():
    from pydantic import ValidationError
    box = _box_on_z0(20, 20, 20)
    with pytest.raises(ValidationError, match="replace_underspecified"):
        ReplaceFace().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"}})


def test_no_body_refused():
    with pytest.raises(ValueError, match="no_body"):
        ReplaceFace().apply(None, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "offset_mm": -3.0})
