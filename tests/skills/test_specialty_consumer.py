"""Tests for specialty consumer-product features:
   cable_routing_channel, breathing_hole_array,
   cable_clip, hinge_pin_boss, battery_dock_pad.

Each skill is exercised on a simple box body. We verify:
  - the operation changes volume in the expected direction (pockets decrease,
    bosses increase) — this is also enforced by the declared post_conditions,
  - the resulting bbox is plausible,
  - manifest-level spec attributes (category, post_conditions).
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.battery_dock_pad import BatteryDockPad
from phone_designer.skills.modify_boss.cable_clip import CableClip
from phone_designer.skills.modify_boss.hinge_pin_boss import HingePinBoss
from phone_designer.skills.modify_pocket.breathing_hole_array import BreathingHoleArray
from phone_designer.skills.modify_pocket.cable_routing_channel import CableRoutingChannel


# ── helpers ─────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _bbox(body) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return ((xmin, ymin, zmin), (xmax, ymax, zmax)) via OCP Bnd_Box."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bbox = Bnd_Box()
    BRepBndLib.Add_s(body.wrapped, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def _make_box(L: float = 40.0, W: float = 40.0, H: float = 10.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ── cable_routing_channel ───────────────────────────────────────────────────


def test_cable_routing_channel_decreases_volume():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = CableRoutingChannel().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "path_points": [(-15.0, 0.0), (15.0, 0.0)],   # straight 30 mm channel along X
        "radius_mm": 1.5,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Approximate half-cylinder volume = 0.5 * π * r² * length = 0.5 * π * 2.25 * 30
    expected = 0.5 * math.pi * 1.5 ** 2 * 30.0
    delta = v0 - v1
    # generous tolerance: end-caps, boolean tolerance, path endpoints semi-spheres etc.
    assert 0.5 * expected < delta < 2.0 * expected


def test_cable_routing_channel_l_shape_path():
    """Channel along an L-shaped polyline still cuts material."""
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = CableRoutingChannel().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "path_points": [(-10.0, -10.0), (0.0, -10.0), (0.0, 10.0)],
        "radius_mm": 1.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0


def test_cable_routing_channel_spec_metadata():
    spec = CableRoutingChannel.spec
    assert spec.name == "cable_routing_channel"
    assert spec.category == "modify/pocket"
    assert spec.level == "atomic"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── breathing_hole_array ────────────────────────────────────────────────────


def test_breathing_hole_array_drills_multiple_holes():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = BreathingHoleArray().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "region_sketch": {
            "kind": "rectangle",
            "length_mm": 12.0,
            "width_mm": 12.0,
        },
        "hole_diameter_mm": 0.8,
        "spacing_mm": 2.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # at least a handful of hex points should fall inside a 12x12 rect at spacing=2:
    # nx ≈ 6, ny ≈ 7 → ~40 holes. each hole = π * 0.16 * 10 ≈ 5.03 mm³
    # so total volume removed should be at least 4 holes (very conservative).
    one_hole = math.pi * (0.4 ** 2) * 10.0
    delta = v0 - v1
    assert delta > 4 * one_hole


def test_breathing_hole_array_rejects_diameter_geq_spacing():
    box = _make_box(40, 40, 10)
    with pytest.raises(ValueError, match="overlap"):
        BreathingHoleArray().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "region_sketch": {
                "kind": "rectangle", "length_mm": 10.0, "width_mm": 10.0,
            },
            "hole_diameter_mm": 2.0,
            "spacing_mm": 2.0,
        })


def test_breathing_hole_array_spec_metadata():
    spec = BreathingHoleArray.spec
    assert spec.name == "breathing_hole_array"
    assert spec.category == "modify/pocket"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── cable_clip ──────────────────────────────────────────────────────────────


def test_cable_clip_increases_volume_and_extends_above_face():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = CableClip().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "opening_width_mm": 3.0,
        "height_mm": 4.0,
        "wall_thickness_mm": 1.0,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    # wall = wt * ow * h = 1 * 3 * 4 = 12
    # lip  = (ow + wt) * ow * wt = 4 * 3 * 1 = 12
    # union ≈ 12 + 12 - (overlap above wall top? lip starts where wall ends → no overlap)
    expected = 12.0 + 12.0
    delta = v1 - v0
    assert abs(delta - expected) / expected < 0.15
    # bbox top z ≥ height + wall_thickness
    (_, mx) = _bbox(r.body)
    assert mx[2] >= 10.0 + 4.0 + 1.0 - 0.1


def test_cable_clip_rejects_wall_geq_opening():
    box = _make_box(40, 40, 10)
    with pytest.raises(ValueError, match="no cable cavity"):
        CableClip().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "opening_width_mm": 1.0,
            "height_mm": 3.0,
            "wall_thickness_mm": 1.0,
        })


def test_cable_clip_spec_metadata():
    spec = CableClip.spec
    assert spec.name == "cable_clip"
    assert spec.category == "modify/boss"
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── hinge_pin_boss ──────────────────────────────────────────────────────────


def test_hinge_pin_boss_increases_volume_with_through_hole():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = HingePinBoss().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position": (0.0, 0.0),
        "pin_diameter_mm": 3.0,
        "height_mm": 6.0,
        "notch_count": 4,
        "wall_thickness_mm": 1.5,
        "notch_width_mm": 1.0,
        "notch_depth_mm": 0.5,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    # outer_r = pin_r + wall = 1.5 + 1.5 = 3.0
    # ring vol = π * (outer_r² - pin_r²) * h = π * (9 - 2.25) * 6 ≈ 127.2
    pin_r = 1.5
    outer_r = pin_r + 1.5
    ring = math.pi * (outer_r ** 2 - pin_r ** 2) * 6.0
    delta = v1 - v0
    # delta < ring because of notches; > 0.6 * ring because notches are tiny
    assert 0.6 * ring < delta < 1.1 * ring
    # bbox top z ≥ 16
    (_, mx) = _bbox(r.body)
    assert mx[2] >= 10.0 + 6.0 - 0.1


def test_hinge_pin_boss_with_zero_notches_is_plain_ring():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = HingePinBoss().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "pin_diameter_mm": 3.0,
        "height_mm": 5.0,
        "notch_count": 0,
        "wall_thickness_mm": 1.5,
    })
    v1 = _volume(r.body)
    pin_r = 1.5
    outer_r = pin_r + 1.5
    ring = math.pi * (outer_r ** 2 - pin_r ** 2) * 5.0
    delta = v1 - v0
    assert abs(delta - ring) / ring < 0.10


def test_hinge_pin_boss_rejects_notch_depth_geq_wall():
    box = _make_box(40, 40, 10)
    with pytest.raises(ValueError, match="notch breaks through"):
        HingePinBoss().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "pin_diameter_mm": 3.0,
            "height_mm": 5.0,
            "notch_count": 4,
            "wall_thickness_mm": 1.0,
            "notch_depth_mm": 1.0,
        })


def test_hinge_pin_boss_spec_metadata():
    spec = HingePinBoss.spec
    assert spec.name == "hinge_pin_boss"
    assert spec.category == "modify/boss"
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── battery_dock_pad ────────────────────────────────────────────────────────


def test_battery_dock_pad_increases_volume_and_has_ribs_above_pad():
    box = _make_box(60, 40, 10)
    v0 = _volume(box)
    r = BatteryDockPad().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "length_mm": 30.0,
        "width_mm": 20.0,
        "height_mm": 2.0,
        "contact_pitch_mm": 10.0,
        "rib_width_mm": 0.8,
        "rib_height_mm": 0.5,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    # pad vol = 30 * 20 * 2 = 1200; ribs = 2 * (30 * 0.8 * 0.5) = 24
    expected = 1200.0 + 24.0
    delta = v1 - v0
    assert abs(delta - expected) / expected < 0.05
    # bbox top z ≥ 10 + 2 + 0.5 = 12.5
    (_, mx) = _bbox(r.body)
    assert mx[2] >= 12.5 - 0.1


def test_battery_dock_pad_rejects_ribs_outside_pad():
    box = _make_box(60, 40, 10)
    with pytest.raises(ValueError, match="spill outside pad"):
        BatteryDockPad().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "length_mm": 30.0,
            "width_mm": 5.0,
            "height_mm": 2.0,
            "contact_pitch_mm": 8.0,   # 8 + 0.6 > 5
            "rib_width_mm": 0.6,
        })


def test_battery_dock_pad_spec_metadata():
    spec = BatteryDockPad.spec
    assert spec.name == "battery_dock_pad"
    assert spec.category == "modify/boss"
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)
