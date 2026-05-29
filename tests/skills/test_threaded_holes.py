"""Threaded-fastener hole skills — ISO 273 / 4762 / 10642."""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.clearance_hole import ClearanceHole
from phone_designer.skills.modify_pocket.counterbore_hole import CounterboreHole
from phone_designer.skills.modify_pocket.countersink_hole import CountersinkHole
from phone_designer.skills.modify_pocket.tap_drill_hole import TapDrillHole


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _top_face_selector():
    """SelectorRef matching the +Z face of a centered box."""
    from phone_designer.skills._selectors import FacesByNormalSelector
    return FacesByNormalSelector(direction=(0.0, 0.0, 1.0), tol_deg=5.0)


def _make_box(length=40, width=40, height=15):
    return Box().apply(None, {
        "length_mm": length, "width_mm": width, "height_mm": height,
    }).body


# --------------------------------------------------------------------- clearance

def test_clearance_hole_m3_medium_blind_decreases_volume():
    box = _make_box()
    v0 = _volume(box)

    r = ClearanceHole().apply(box, {
        "face_selector": _top_face_selector(),
        "position_xy": (0.0, 0.0),
        "thread_spec": "M3",
        "fit": "medium",
        "depth_mm": 8.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0

    # M3 medium clearance = 3.4 mm → expected loss ≈ π*(3.4/2)^2 * 8 ≈ 72.6 mm³.
    expected = math.pi * (3.4 / 2) ** 2 * 8.0
    actual_loss = v0 - v1
    assert abs(actual_loss - expected) / expected < 0.10, \
        f"clearance M3 medium loss expected ~{expected:.1f}, got {actual_loss:.1f}"


def test_clearance_hole_through_traverses_body():
    box = _make_box(height=10)
    v0 = _volume(box)
    r = ClearanceHole().apply(box, {
        "face_selector": _top_face_selector(),
        "thread_spec": "M4",
        "fit": "close",
        "depth_mm": 1.0,        # ignored when through=True
        "through": True,
    })
    v1 = _volume(r.body)
    # M4 close clearance = 4.3 mm → through 10 mm → ≈ π * 2.15² * 10 ≈ 145.3 mm³.
    expected = math.pi * (4.3 / 2) ** 2 * 10.0
    actual = v0 - v1
    assert abs(actual - expected) / expected < 0.05


def test_clearance_hole_unknown_thread_raises():
    box = _make_box()
    with pytest.raises(Exception):
        ClearanceHole().apply(box, {
            "face_selector": _top_face_selector(),
            "thread_spec": "M99",
            "fit": "medium",
            "depth_mm": 5.0,
        })


# --------------------------------------------------------------------- tap drill

def test_tap_drill_hole_m3_uses_tap_diameter():
    box = _make_box()
    v0 = _volume(box)
    r = TapDrillHole().apply(box, {
        "face_selector": _top_face_selector(),
        "thread_spec": "M3",
        "depth_mm": 8.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # M3 tap drill = 2.5 mm → expected loss ≈ π * 1.25² * 8 ≈ 39.3 mm³.
    expected = math.pi * (2.5 / 2) ** 2 * 8.0
    actual = v0 - v1
    assert abs(actual - expected) / expected < 0.10


# --------------------------------------------------------------------- counterbore

def test_counterbore_hole_m4_medium_stepped():
    box = _make_box()
    v0 = _volume(box)
    r = CounterboreHole().apply(box, {
        "face_selector": _top_face_selector(),
        "thread_spec": "M4",
        "fit": "medium",
        "depth_mm": 6.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Catalog: M4 counterbore_d = 8.0, depth = 4.4; clearance medium = 4.5.
    cb_vol = math.pi * (8.0 / 2) ** 2 * 4.4
    shaft_vol = math.pi * (4.5 / 2) ** 2 * 6.0
    expected = cb_vol + shaft_vol
    actual = v0 - v1
    assert abs(actual - expected) / expected < 0.10, \
        f"counterbore stepped loss expected ~{expected:.1f}, got {actual:.1f}"


# --------------------------------------------------------------------- countersink

def test_countersink_hole_m3_close_conical_plus_shaft():
    box = _make_box()
    v0 = _volume(box)
    r = CountersinkHole().apply(box, {
        "face_selector": _top_face_selector(),
        "thread_spec": "M3",
        "fit": "close",
        "depth_mm": 6.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Catalog: M3 countersink_d = 6.0 mm, 90° → cone depth = 3.0 mm.
    # Cone volume = (1/3) π r² h = (1/3) π (3.0)² (3.0) ≈ 28.3 mm³.
    # Catalog: M3 clearance_close = 3.2 mm → shaft ≈ π * 1.6² * 6 ≈ 48.3 mm³.
    cone_vol = (1.0 / 3.0) * math.pi * (3.0 ** 2) * 3.0
    shaft_vol = math.pi * (3.2 / 2) ** 2 * 6.0
    expected = cone_vol + shaft_vol
    actual = v0 - v1
    assert abs(actual - expected) / expected < 0.15


# --------------------------------------------------------------------- metadata

def test_skill_specs_declare_volume_decreased_postcondition():
    for cls in (ClearanceHole, TapDrillHole, CounterboreHole, CountersinkHole):
        kinds = {pc.kind for pc in cls.spec.post_conditions}
        assert "volume_decreased" in kinds, \
            f"{cls.__name__} missing volume_decreased post-condition"
