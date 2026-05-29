"""magsafe_magnet_ring + motor_flange_bolt_pattern — unit tests.

Tests:
  - magsafe ring + magnet pockets net-changes volume (ring fuse >> pocket cuts)
  - magsafe rejects unknown magnet_spec / inverted radii / pocket too wide
  - motor flange drills the correct # holes (per NEMA bolt count) and
    reduces volume by ≈ N * π r² * length
  - motor flange rejects unknown motor_spec
  - both skills expose correct post_conditions / spec metadata
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.magsafe_magnet_ring import (
    MagsafeMagnetRing,
)
from phone_designer.skills.modify_pattern.motor_flange_bolt_pattern import (
    MotorFlangeBoltPattern,
)


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _box(length=100.0, width=100.0, height=10.0):
    return Box().apply(
        None,
        {"length_mm": length, "width_mm": width, "height_mm": height},
    ).body


# ───────────────────── magsafe_magnet_ring ──────────────────────────────────


def test_magsafe_ring_adds_volume_net_of_pockets():
    body = _box()
    v_before = _volume(body)

    r = MagsafeMagnetRing().apply(
        body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "center_xy": (0.0, 0.0),
            "inner_d_mm": 45.0,
            "outer_d_mm": 55.0,
            "ring_height_mm": 1.2,
            "magnet_count": 18,
            "magnet_spec": "N42_D5x2",
        },
    )
    v_after = _volume(r.body)

    # Ring volume (annulus) = π * (R_o² − R_i²) * h
    ring_vol = math.pi * (27.5 ** 2 - 22.5 ** 2) * 1.2
    # Pocket volume per magnet (Ø ≈ 5.05 mm, depth ≈ 1.2 mm capped by ring h)
    pocket_d = 5.0 + 0.05
    pocket_depth = min(2.0, 1.2)
    pocket_vol = math.pi * (pocket_d / 2.0) ** 2 * pocket_depth
    net_expected = ring_vol - 18 * pocket_vol
    assert net_expected > 0.0
    assert v_after > v_before, "ring fuse must dominate net volume"

    net_actual = v_after - v_before
    assert abs(net_actual - net_expected) / net_expected < 0.05, (
        f"ring net volume: expected {net_expected:.3f}, got {net_actual:.3f}"
    )


def test_magsafe_unknown_magnet_spec_raises():
    body = _box()
    with pytest.raises(ValueError, match="unknown magnet_spec"):
        MagsafeMagnetRing().apply(
            body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "magnet_spec": "NOT_A_MAGNET",
            },
        )


def test_magsafe_rejects_inverted_radii():
    body = _box()
    with pytest.raises(ValueError, match="inner_d"):
        MagsafeMagnetRing().apply(
            body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "inner_d_mm": 60.0,
                "outer_d_mm": 50.0,
                "magnet_spec": "N42_D5x2",
            },
        )


def test_magsafe_rejects_pocket_too_large_for_ring_width():
    # Ring width = (50 − 48) / 2 = 1.0 mm, pocket = 10 mm → reject.
    body = _box()
    with pytest.raises(ValueError, match="too large"):
        MagsafeMagnetRing().apply(
            body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "inner_d_mm": 48.0,
                "outer_d_mm": 50.0,
                "magnet_spec": "N42_D10x3",
            },
        )


def test_magsafe_spec_metadata():
    spec = MagsafeMagnetRing.spec
    assert spec.name == "magsafe_magnet_ring"
    assert spec.level == "macro"
    assert "magsafe_magnet_ring" in spec.produces_features
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "volume_changed" in kinds


# ──────────────────── motor_flange_bolt_pattern ─────────────────────────────


@pytest.mark.parametrize(
    "motor_spec,bolt_circle_d,hole_d,bolts",
    [
        ("NEMA_17", 31.0, 3.0, 4),
        ("NEMA_23", 47.14, 5.0, 4),
        ("NEMA_34", 69.6, 6.6, 4),
    ],
)
def test_motor_flange_through_holes_remove_correct_volume(
    motor_spec, bolt_circle_d, hole_d, bolts,
):
    # Use a plate large enough to contain even NEMA_34 (Ø ≈ 70 mm + Ø6.6).
    body = _box(length=100.0, width=100.0, height=6.0)
    v_before = _volume(body)
    r = MotorFlangeBoltPattern().apply(
        body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "center_xy": (0.0, 0.0),
            "motor_spec": motor_spec,
            "hole_through": True,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, f"{motor_spec} through holes must remove material"

    # N holes × π r² × plate thickness
    expected = bolts * math.pi * (hole_d / 2.0) ** 2 * 6.0
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.03, (
        f"{motor_spec} hole volume: expected {expected:.3f}, got {actual:.3f}"
    )


def test_motor_flange_blind_recess_removes_less_than_through():
    body_t = _box(length=100.0, width=100.0, height=20.0)
    body_b = _box(length=100.0, width=100.0, height=20.0)
    v0 = _volume(body_t)

    r_through = MotorFlangeBoltPattern().apply(
        body_t,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "motor_spec": "NEMA_17",
            "hole_through": True,
        },
    )
    r_blind = MotorFlangeBoltPattern().apply(
        body_b,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "motor_spec": "NEMA_17",
            "hole_through": False,
        },
    )
    removed_through = v0 - _volume(r_through.body)
    removed_blind = v0 - _volume(r_blind.body)
    assert removed_blind < removed_through, (
        f"blind {removed_blind:.3f} should remove less than through "
        f"{removed_through:.3f}"
    )


def test_motor_flange_unknown_spec_raises():
    body = _box()
    with pytest.raises(ValueError, match="unknown motor_spec"):
        MotorFlangeBoltPattern().apply(
            body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "motor_spec": "NEMA_999",
            },
        )


def test_motor_flange_spec_metadata():
    spec = MotorFlangeBoltPattern.spec
    assert spec.name == "motor_flange_bolt_pattern"
    assert spec.level == "macro"
    assert "motor_flange_bolt_pattern" in spec.produces_features
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "volume_decreased" in kinds
