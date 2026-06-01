"""Tests for biometric / health-sensor pocket + boss skills (PACK W2-A).

Skills under test:
    - modify_pocket/capacitive_touch_pad
    - modify_pocket/ecg_electrode_pad
    - modify_pocket/heart_rate_optical_dome
    - modify_boss/haptic_motor_mount
    - modify_pocket/temperature_sensor_pocket

For each: verifies post-condition direction (volume_decreased / volume_increased),
expected swept volume against analytic geometry, and (where applicable)
catalog wiring + unknown-key error handling.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.haptic_motor_mount import HapticMotorMount
from phone_designer.skills.modify_pocket.capacitive_touch_pad import CapacitiveTouchPad
from phone_designer.skills.modify_pocket.ecg_electrode_pad import EcgElectrodePad
from phone_designer.skills.modify_pocket.heart_rate_optical_dome import (
    HeartRateOpticalDome,
)
from phone_designer.skills.modify_pocket.temperature_sensor_pocket import (
    TemperatureSensorPocket,
)


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _box(L=60.0, W=60.0, H=8.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H})


# ───────────────────────── capacitive_touch_pad ─────────────────────────────


def test_capacitive_touch_pad_removes_pocket_plus_grooves():
    box_r = _box(L=60, W=60, H=6)
    v_before = _volume(box_r.body)

    region_w, region_h = 30.0, 30.0
    electrode_d = 8.0
    pitch = 10.0
    clearance = 0.3

    r = CapacitiveTouchPad().apply(
        box_r.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "region_w_mm": region_w,
            "region_h_mm": region_h,
            "electrode_d_mm": electrode_d,
            "electrode_pitch_mm": pitch,
            "copper_clearance_mm": clearance,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before

    # Expected: outer pocket + N×M electrode grooves (cylinders).
    usable_w = region_w - electrode_d
    usable_h = region_h - electrode_d
    nx = int(usable_w // pitch) + 1
    ny = int(usable_h // pitch) + 1
    pocket_v = region_w * region_h * clearance
    groove_v = nx * ny * math.pi * (electrode_d / 2.0) ** 2 * clearance
    expected = pocket_v + groove_v
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.05, (
        f"capacitive_touch_pad volume: expected {expected:.2f}, got {actual:.2f}"
    )


def test_capacitive_touch_pad_pitch_smaller_than_electrode_raises():
    box_r = _box()
    with pytest.raises(ValueError, match="electrode_pitch_mm"):
        CapacitiveTouchPad().apply(
            box_r.body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "region_w_mm": 30.0,
                "region_h_mm": 30.0,
                "electrode_d_mm": 8.0,
                "electrode_pitch_mm": 5.0,  # < electrode_d
                "copper_clearance_mm": 0.3,
            },
        )


def test_capacitive_touch_pad_spec_metadata():
    spec = CapacitiveTouchPad.spec
    assert spec.name == "capacitive_touch_pad"
    assert "capacitive_touch_pad" in spec.produces_features
    assert {pc.kind for pc in spec.post_conditions} >= {"volume_decreased"}


# ───────────────────────── ecg_electrode_pad ────────────────────────────────


def test_ecg_electrode_pad_removes_well_plus_channel():
    box_r = _box(L=40, W=40, H=5)
    v_before = _volume(box_r.body)

    electrode_d = 12.0
    step = 0.3
    channel_w = 1.5
    channel_l = 10.0

    r = EcgElectrodePad().apply(
        box_r.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "electrode_d_mm": electrode_d,
            "body_contact_step_mm": step,
            "lead_wire_channel_w_mm": channel_w,
            "channel_length_mm": channel_l,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before

    # Expected: circular well + side-exit channel (with overlap inside the well).
    well_v = math.pi * (electrode_d / 2.0) ** 2 * step
    channel_v = channel_l * channel_w * step
    # Overlap = intersection inside the circle for x in [0, electrode_r],
    # y in [-w/2, +w/2]. Approximate by half-disc area * step (upper bound).
    overlap_v = 0.5 * math.pi * (electrode_d / 2.0) ** 2 * step
    lower_bound = well_v + channel_v - overlap_v
    upper_bound = well_v + channel_v
    actual = v_before - v_after
    assert lower_bound * 0.95 <= actual <= upper_bound * 1.05, (
        f"ecg_electrode_pad volume {actual:.2f} not in "
        f"[{lower_bound:.2f}, {upper_bound:.2f}]"
    )


def test_ecg_electrode_pad_channel_wider_than_electrode_raises():
    box_r = _box()
    with pytest.raises(ValueError, match="lead_wire_channel_w_mm"):
        EcgElectrodePad().apply(
            box_r.body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "electrode_d_mm": 5.0,
                "lead_wire_channel_w_mm": 10.0,  # > electrode_d
            },
        )


def test_ecg_electrode_pad_spec_metadata():
    spec = EcgElectrodePad.spec
    assert spec.name == "ecg_electrode_pad"
    assert "ecg_electrode_pad" in spec.produces_features
    assert {pc.kind for pc in spec.post_conditions} >= {"volume_decreased"}


# ───────────────────────── heart_rate_optical_dome ──────────────────────────


def test_heart_rate_optical_dome_removes_window_and_two_leds():
    box_r = _box(L=40, W=40, H=4)
    v_before = _volume(box_r.body)

    win_d = 7.0
    dome_h = 0.4
    led_d = 2.5

    r = HeartRateOpticalDome().apply(
        box_r.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "optical_window_d_mm": win_d,
            "dome_height_mm": dome_h,
            "led_clearance_d_mm": led_d,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before

    win_v = math.pi * (win_d / 2.0) ** 2 * dome_h
    led_v = math.pi * (led_d / 2.0) ** 2 * dome_h
    expected = win_v + 2.0 * led_v
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02, (
        f"heart_rate_optical_dome volume: expected {expected:.4f}, got {actual:.4f}"
    )


def test_heart_rate_optical_dome_spec_metadata():
    spec = HeartRateOpticalDome.spec
    assert spec.name == "heart_rate_optical_dome"
    assert "heart_rate_optical_dome" in spec.produces_features
    assert {pc.kind for pc in spec.post_conditions} >= {"volume_decreased"}


# ───────────────────────── haptic_motor_mount ────────────────────────────────


def test_haptic_motor_mount_lra_adds_wall_volume():
    box_r = _box(L=40, W=40, H=4)
    v_before = _volume(box_r.body)

    wall = 1.0
    r = HapticMotorMount().apply(
        box_r.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "motor_kind": "lra_10x3",
            "wall_thickness_mm": wall,
        },
    )
    v_after = _volume(r.body)
    assert v_after > v_before

    # Catalog: lra_10x3 = motor_d 10, motor_l 3, seat_clearance 0.15
    motor_d, motor_l, seat_clearance = 10.0, 3.0, 0.15
    inner_d = motor_d + 2 * seat_clearance
    outer_d = inner_d + 2 * wall
    ring_v = (math.pi * (outer_d / 2) ** 2 - math.pi * (inner_d / 2) ** 2) * motor_l
    actual = v_after - v_before
    assert abs(actual - ring_v) / ring_v < 0.05, (
        f"haptic_motor_mount LRA ring volume: expected {ring_v:.2f}, got {actual:.2f}"
    )


def test_haptic_motor_mount_erm_uses_catalog_dimensions():
    box_r = _box(L=40, W=40, H=4)
    v_before = _volume(box_r.body)

    wall = 0.8
    r = HapticMotorMount().apply(
        box_r.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "motor_kind": "erm_4x14",
            "wall_thickness_mm": wall,
        },
    )
    v_after = _volume(r.body)
    motor_d, motor_l, seat_clearance = 4.0, 14.0, 0.2
    inner_d = motor_d + 2 * seat_clearance
    outer_d = inner_d + 2 * wall
    ring_v = (math.pi * (outer_d / 2) ** 2 - math.pi * (inner_d / 2) ** 2) * motor_l
    actual = v_after - v_before
    assert abs(actual - ring_v) / ring_v < 0.05


def test_haptic_motor_mount_unknown_kind_raises():
    """motor_kind is a Pydantic Literal — invalid values raise ValidationError
    before the catalog check ever runs."""
    from pydantic import ValidationError
    box_r = _box()
    with pytest.raises(ValidationError):
        HapticMotorMount().apply(
            box_r.body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "motor_kind": "not_a_motor",
            },
        )


def test_haptic_motor_mount_spec_metadata():
    spec = HapticMotorMount.spec
    assert spec.name == "haptic_motor_mount"
    assert "haptic_motor_mount" in spec.produces_features
    assert {pc.kind for pc in spec.post_conditions} >= {"volume_increased"}


# ───────────────────────── temperature_sensor_pocket ─────────────────────────


def test_temperature_sensor_pocket_circular_mlx90614():
    box_r = _box(L=30, W=30, H=8)
    v_before = _volume(box_r.body)

    r = TemperatureSensorPocket().apply(
        box_r.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "sensor_kind": "mlx90614",
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before

    # Catalog: mlx90614 body_w=8.1, body_t=4.1, clearance=0.1, is_circular=true
    body_w, body_t, clearance = 8.1, 4.1, 0.1
    pocket_d = body_w + 2 * clearance
    expected = math.pi * (pocket_d / 2) ** 2 * body_t
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02


def test_temperature_sensor_pocket_rectangular_si705x():
    box_r = _box(L=20, W=20, H=4)
    v_before = _volume(box_r.body)

    r = TemperatureSensorPocket().apply(
        box_r.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "sensor_kind": "si705x",
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before

    body_w, body_h, body_t, clearance = 3.0, 3.0, 0.8, 0.1
    pocket_w = body_w + 2 * clearance
    pocket_h = body_h + 2 * clearance
    expected = pocket_w * pocket_h * body_t
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02


def test_temperature_sensor_pocket_unknown_kind_raises():
    """sensor_kind is a Pydantic Literal — invalid values raise ValidationError
    before the catalog check ever runs."""
    from pydantic import ValidationError
    box_r = _box()
    with pytest.raises(ValidationError):
        TemperatureSensorPocket().apply(
            box_r.body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "sensor_kind": "not_a_sensor",
            },
        )


def test_temperature_sensor_pocket_spec_metadata():
    spec = TemperatureSensorPocket.spec
    assert spec.name == "temperature_sensor_pocket"
    assert "temperature_sensor_pocket" in spec.produces_features
    assert {pc.kind for pc in spec.post_conditions} >= {"volume_decreased"}
