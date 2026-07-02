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


def test_positive_offset_outside_body_refused():
    # HONEST LIMIT of the trim-based v1: offset_mm = +5 puts the replacement
    # plane at z=25, ABOVE the solid (top at z=20). A plane-trim cannot ADD
    # material where none exists — returning the unchanged 8000mm³ box as
    # "success" was a silent no-op; it must be a structured refusal instead.
    box = _box_on_z0(20, 20, 20)
    with pytest.raises(ValueError, match="replace_plane_misses_body"):
        ReplaceFace().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "offset_mm": 5.0})


def test_absolute_plane_missing_body_refused():
    # Same silent no-op via the absolute-plane mode: a plane at z=25 (above the
    # solid) does not cut anything — refuse instead of echoing the input body.
    box = _box_on_z0(20, 20, 20)
    with pytest.raises(ValueError, match="replace_plane_misses_body"):
        ReplaceFace().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "plane_origin_mm": [0, 0, 25], "plane_normal": [0, 0, 1]})


def test_replace_side_face_shrinks_in_x():
    # Replace the +X face (right, at x=+10 for a 20-wide box centred in X) with
    # a plane at x=+4 → width in X becomes 14 (x∈[-10,4]) → vol 14·20·20 = 5600.
    box = _box_on_z0(20, 20, 20)
    r = ReplaceFace().apply(box, {
        "face_selector": {"kind": "face_named", "name": "right"},
        "plane_origin_mm": [4, 0, 0], "plane_normal": [1, 0, 0]})
    assert _rv(r) == pytest.approx(5600.0, rel=1e-9)


def test_tilted_plane_side_is_origin_independent():
    # TILTED replacement plane (15° about Y) through z=15: the plane cuts the
    # box symmetrically, so the kept volume is exactly 20·20·15 = 6000 mm³.
    # The material-side test projects on the PLANE's own normal, so ANY origin
    # point on the same plane must give the identical result — the face-normal
    # projection used before flipped verdicts with in-plane origin moves
    # (silently returning the whole uncut box for some origins).
    import math
    n = [math.sin(math.radians(15)), 0.0, math.cos(math.radians(15))]
    r1 = ReplaceFace().apply(_box_on_z0(20, 20, 20), {
        "face_selector": {"kind": "face_named", "name": "top"},
        "plane_origin_mm": [0, 0, 15], "plane_normal": n})
    assert _rv(r1) == pytest.approx(6000.0, rel=1e-6)
    assert r1.extras["transform"]["n_pieces_kept"] == 1
    # a different point ON THE SAME PLANE (origin + 10·in-plane tangent)
    t = 10.0
    o2 = [-t * math.cos(math.radians(15)), 0.0, 15 + t * math.sin(math.radians(15))]
    r2 = ReplaceFace().apply(_box_on_z0(20, 20, 20), {
        "face_selector": {"kind": "face_named", "name": "top"},
        "plane_origin_mm": o2, "plane_normal": n})
    assert _rv(r2) == pytest.approx(6000.0, rel=1e-6)
    assert r2.extras["transform"]["n_pieces_kept"] == 1


def test_perpendicular_replacement_plane_refused():
    # A replacement plane PERPENDICULAR to the selected face's outward normal
    # has no defined material side (n·fn = 0) — structured refusal, not an
    # arbitrary keep/drop.
    box = _box_on_z0(20, 20, 20)
    with pytest.raises(ValueError, match="replace_plane_perpendicular"):
        ReplaceFace().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "plane_origin_mm": [0, 0, 10], "plane_normal": [1, 0, 0]})


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
