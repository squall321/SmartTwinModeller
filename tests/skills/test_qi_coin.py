"""Tests for wireless_charging_coil_seat + coin_cell_cavity skills.

Verifies catalog wiring, volume_decreased post-condition, expected swept
volume against catalog dimensions, and error handling for unknown specs.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.coin_cell_cavity import CoinCellCavity
from phone_designer.skills.modify_pocket.wireless_charging_coil_seat import (
    WirelessChargingCoilSeat,
)


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


# ───────────────────────── Qi coil seat ──────────────────────────────────────


def test_qi_a11_coil_seat_removes_catalog_volume():
    # Host shell large enough for a 43 mm coil + 1 mm pocket + 0.2 ferrite.
    box_result = Box().apply(
        None, {"length_mm": 60, "width_mm": 60, "height_mm": 6},
    )
    v_before = _volume(box_result.body)

    r = WirelessChargingCoilSeat().apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "coil_spec": "Qi_A11",
            "with_ferrite": True,
            "ferrite_t_mm": 0.2,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before

    outer_d, coil_t, ferr_t = 43.0, 1.0, 0.2
    expected = math.pi * (outer_d / 2.0) ** 2 * (coil_t + ferr_t)
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02, (
        f"Qi_A11 seat volume: expected {expected:.2f}, got {actual:.2f}"
    )


def test_qi_a28_coil_seat_no_ferrite():
    box_result = Box().apply(
        None, {"length_mm": 60, "width_mm": 60, "height_mm": 6},
    )
    v_before = _volume(box_result.body)

    r = WirelessChargingCoilSeat().apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "coil_spec": "Qi_A28",
            "with_ferrite": False,
        },
    )
    v_after = _volume(r.body)
    outer_d, coil_t = 48.0, 1.2
    expected = math.pi * (outer_d / 2.0) ** 2 * coil_t
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02


def test_qi_unknown_coil_spec_raises():
    box_result = Box().apply(
        None, {"length_mm": 50, "width_mm": 50, "height_mm": 5},
    )
    with pytest.raises(ValueError, match="unknown coil_spec"):
        WirelessChargingCoilSeat().apply(
            box_result.body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "coil_spec": "NotAReal_Coil",
            },
        )


def test_qi_spec_metadata():
    spec = WirelessChargingCoilSeat.spec
    assert spec.name == "wireless_charging_coil_seat"
    assert "wireless_coil_seat" in spec.produces_features
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "volume_decreased" in kinds


# ───────────────────────── coin cell cavity ──────────────────────────────────


def test_cr2032_cavity_removes_catalog_volume():
    box_result = Box().apply(
        None, {"length_mm": 30, "width_mm": 30, "height_mm": 5},
    )
    v_before = _volume(box_result.body)

    r = CoinCellCavity().apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "cell_spec": "CR2032",
            "side_clearance_mm": 0.1,
        },
    )
    v_after = _volume(r.body)

    cavity_d = 20.0 + 2 * 0.1
    cell_t = 3.2
    expected = math.pi * (cavity_d / 2.0) ** 2 * cell_t
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02


def test_lr44_cavity_zero_clearance():
    box_result = Box().apply(
        None, {"length_mm": 20, "width_mm": 20, "height_mm": 8},
    )
    v_before = _volume(box_result.body)

    r = CoinCellCavity().apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "cell_spec": "LR44",
            "side_clearance_mm": 0.0,
        },
    )
    v_after = _volume(r.body)
    expected = math.pi * (11.6 / 2.0) ** 2 * 5.4
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02


def test_sr41_small_cavity():
    box_result = Box().apply(
        None, {"length_mm": 15, "width_mm": 15, "height_mm": 6},
    )
    v_before = _volume(box_result.body)

    r = CoinCellCavity().apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "cell_spec": "SR41",
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before


def test_coin_unknown_cell_spec_raises():
    box_result = Box().apply(
        None, {"length_mm": 30, "width_mm": 30, "height_mm": 5},
    )
    with pytest.raises(ValueError, match="unknown cell_spec"):
        CoinCellCavity().apply(
            box_result.body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "cell_spec": "NotAReal_Cell",
            },
        )


def test_coin_spec_metadata():
    spec = CoinCellCavity.spec
    assert spec.name == "coin_cell_cavity"
    assert "coin_cell_cavity" in spec.produces_features
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "volume_decreased" in kinds
