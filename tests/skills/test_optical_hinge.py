"""Optical + hinge mechanism pack tests.

Covers:
  - light_pipe_channel: small circular tunnel swept along a 2D path on a face.
  - hinge_detent_cam:   central pivot bore + N detent notches at given angles.

Each skill is exercised on a simple Box and we assert:
  - volume strictly decreases (PostCondition: volume_decreased),
  - removed volume is in the right ballpark for the swept / drilled geometry,
  - basic argument-validation paths fail loudly,
  - spec metadata wires up to category="modify/pocket" + volume_decreased.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.hinge_detent_cam import HingeDetentCam
from phone_designer.skills.modify_pocket.light_pipe_channel import LightPipeChannel


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _make_box(L: float = 40.0, W: float = 40.0, H: float = 20.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ── light_pipe_channel ──────────────────────────────────────────────────────


def test_light_pipe_channel_straight_path_decreases_volume():
    box = _make_box(40, 40, 20)
    v0 = _volume(box)
    r = LightPipeChannel().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "path_points": [(-10.0, 0.0), (10.0, 0.0)],
        "channel_d_mm": 1.2,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Straight tunnel: π * r² * length. r=0.6mm, length=20mm → ~22.6mm³.
    expected = math.pi * (0.6 ** 2) * 20.0
    delta = v0 - v1
    assert 0.6 * expected < delta < 1.5 * expected, \
        f"light_pipe_channel removed {delta:.2f}, expected ~{expected:.2f}"


def test_light_pipe_channel_L_shaped_path_works():
    box = _make_box(40, 40, 20)
    v0 = _volume(box)
    r = LightPipeChannel().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "path_points": [(-8.0, -8.0), (-8.0, 8.0), (8.0, 8.0)],
        "channel_d_mm": 1.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0


def test_light_pipe_channel_rejects_single_point():
    box = _make_box(40, 40, 20)
    with pytest.raises(Exception):
        LightPipeChannel().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "path_points": [(0.0, 0.0)],
            "channel_d_mm": 1.2,
        })


def test_light_pipe_channel_rejects_zero_diameter():
    box = _make_box(40, 40, 20)
    with pytest.raises(Exception):
        LightPipeChannel().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "path_points": [(-5.0, 0.0), (5.0, 0.0)],
            "channel_d_mm": 0.0,
        })


def test_light_pipe_channel_spec_metadata():
    spec = LightPipeChannel.spec
    assert spec.name == "light_pipe_channel"
    assert spec.category == "modify/pocket"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── hinge_detent_cam ────────────────────────────────────────────────────────


def test_hinge_detent_cam_default_two_detents_decreases_volume():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = HingeDetentCam().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "pivot_d_mm": 2.0,
        "detent_count": 2,
        "detent_r_mm": 0.4,
        "detent_angles_deg": [30.0, 150.0],
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Pivot through-hole removes π * 1² * 10 ≈ 31.4 mm³.
    # Detents are tiny by comparison — most of the loss is the pivot bore.
    pivot_loss = math.pi * (1.0 ** 2) * 10.0
    delta = v0 - v1
    assert delta > 0.7 * pivot_loss, \
        f"hinge_detent_cam removed {delta:.2f}, expected > {0.7 * pivot_loss:.2f}"


def test_hinge_detent_cam_zero_detents_just_drills_pivot():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = HingeDetentCam().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "pivot_d_mm": 3.0,
        "detent_count": 0,
        "detent_angles_deg": [],
    })
    v1 = _volume(r.body)
    pivot_loss = math.pi * (1.5 ** 2) * 10.0
    delta = v0 - v1
    # No detents — removed volume should match the pivot bore.
    assert 0.9 * pivot_loss < delta < 1.1 * pivot_loss


def test_hinge_detent_cam_four_detents_remove_more_than_two():
    box_a = _make_box(40, 40, 10)
    box_b = _make_box(40, 40, 10)
    v0 = _volume(box_a)

    r2 = HingeDetentCam().apply(box_a, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "pivot_d_mm": 2.0,
        "detent_count": 2,
        "detent_r_mm": 0.5,
        "detent_angles_deg": [0.0, 180.0],
    })
    r4 = HingeDetentCam().apply(box_b, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "pivot_d_mm": 2.0,
        "detent_count": 4,
        "detent_r_mm": 0.5,
        "detent_angles_deg": [0.0, 90.0, 180.0, 270.0],
    })
    delta_2 = v0 - _volume(r2.body)
    delta_4 = v0 - _volume(r4.body)
    # 4 detents should remove strictly more material than 2.
    assert delta_4 > delta_2


def test_hinge_detent_cam_rejects_angle_count_mismatch():
    box = _make_box(40, 40, 10)
    with pytest.raises(Exception):
        HingeDetentCam().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "pivot_d_mm": 2.0,
            "detent_count": 3,
            "detent_angles_deg": [30.0, 150.0],   # only 2 angles for count=3
        })


def test_hinge_detent_cam_rejects_zero_pivot_diameter():
    box = _make_box(40, 40, 10)
    with pytest.raises(Exception):
        HingeDetentCam().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "pivot_d_mm": 0.0,
            "detent_count": 0,
            "detent_angles_deg": [],
        })


def test_hinge_detent_cam_spec_metadata():
    spec = HingeDetentCam.spec
    assert spec.name == "hinge_detent_cam"
    assert spec.category == "modify/pocket"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)
