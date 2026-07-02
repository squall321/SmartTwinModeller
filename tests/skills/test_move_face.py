"""move_face — focused unit tests.

Analytic invariants on a 20x20x20 box (vol 8000 mm3):
    move TOP face (+Z normal) by +5  -> 8000 + 20*20*5 = 10000
    move TOP face (+Z normal) by -5  -> 8000 - 20*20*5 =  6000
    move a SIDE face (+X normal) by +5 grows the same 20*20*5 = 2000 slab.
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_curvature.move_face import MoveFace


def _vol(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, p)
    return abs(float(p.Mass()))


def _box20():
    return Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 20}).body


TOP = {"kind": "faces_by_normal", "direction": (0.0, 0.0, 1.0), "tol_deg": 5.0}
PLUSX = {"kind": "faces_by_normal", "direction": (1.0, 0.0, 0.0), "tol_deg": 5.0}


def test_move_top_face_out_grows_by_slab():
    body = _box20()
    assert _vol(body.wrapped) == pytest.approx(8000.0, rel=1e-6)
    r = MoveFace().apply(body, {"face_selector": TOP, "distance_mm": 5.0})
    assert _vol(r.body.wrapped) == pytest.approx(10000.0, rel=1e-6)
    assert r.extras["move_face"]["op"] == "fuse"
    assert r.extras["move_face"]["result_volume_mm3"] == pytest.approx(10000.0, rel=1e-4)


def test_move_top_face_in_shrinks_by_slab():
    body = _box20()
    r = MoveFace().apply(body, {"face_selector": TOP, "distance_mm": -5.0})
    assert _vol(r.body.wrapped) == pytest.approx(6000.0, rel=1e-6)
    assert r.extras["move_face"]["op"] == "cut"


def test_move_side_face_out_grows():
    body = _box20()
    r = MoveFace().apply(body, {"face_selector": PLUSX, "distance_mm": 5.0})
    # 20x20 cross-section * 5 = 2000 added
    assert _vol(r.body.wrapped) == pytest.approx(10000.0, rel=1e-6)


def test_zero_distance_rejected():
    body = _box20()
    with pytest.raises(ValueError, match="fm.zero_distance"):
        MoveFace().apply(body, {"face_selector": TOP, "distance_mm": 0.0})


def test_no_face_matched_rejected():
    body = _box20()
    bogus = {"kind": "faces_by_normal", "direction": (1.0, 1.0, 1.0), "tol_deg": 0.1}
    with pytest.raises(ValueError, match="fm.no_face_matched"):
        MoveFace().apply(body, {"face_selector": bogus, "distance_mm": 5.0})


def test_tapered_adjacent_wall_rejected():
    # Cone frustum (R=10, r=5, h=10): its lateral wall is TAPERED (26.6 deg off
    # the +Z sweep direction), so the prism-slab Fuse/Cut would silently build a
    # stepped pad (grow) or knife-edge pocket (shrink) instead of Move Face
    # semantics — both directions must be refused.
    from build123d import Cone
    frustum = Cone(bottom_radius=10, top_radius=5, height=10)
    with pytest.raises(ValueError, match="fm.adjacent_wall_not_prismatic"):
        MoveFace().apply(frustum, {"face_selector": TOP, "distance_mm": 5.0})
    with pytest.raises(ValueError, match="fm.adjacent_wall_not_prismatic"):
        MoveFace().apply(frustum, {"face_selector": TOP, "distance_mm": -5.0})


def test_prismatic_curved_wall_still_allowed():
    # A cylinder's lateral wall is CURVED but PRISMATIC (parallel to +Z), so the
    # guard must not fire: moving the top face +5 grows exactly pi*r^2*5.
    import math

    from build123d import Cylinder
    cyl = Cylinder(radius=5, height=10)
    r = MoveFace().apply(cyl, {"face_selector": TOP, "distance_mm": 5.0})
    assert _vol(r.body.wrapped) == pytest.approx(math.pi * 25.0 * 15.0, rel=1e-6)
