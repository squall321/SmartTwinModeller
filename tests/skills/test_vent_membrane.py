"""vent_membrane_pocket 단위 테스트.

Gore-style 어쿠스틱 vent: through hole (mounting_d) + 얕은 멤브레인 recess (membrane_d × boss_h).
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.vent_membrane_pocket import VentMembranePocket


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _box(l=30.0, w=30.0, h=5.0):
    return Box().apply(None, {"length_mm": l, "width_mm": w, "height_mm": h}).body


def test_vent_membrane_pocket_reduces_volume_pmf_f25():
    box = _box()
    v_before = _volume(box)

    r = VentMembranePocket().apply(
        box,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "membrane_spec": "PMF_F25",
            "catalog_family": "gore",
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before

    # 예상 손실: recess (π * (8/2)² * 0.3) + through (π * (5.5/2)² * (h - boss_h))
    # box h = 5, boss_h = 0.3 → through cuts 4.7mm of solid below recess
    membrane_d, mounting_d, boss_h, box_h = 8.0, 5.5, 0.3, 5.0
    recess_vol = math.pi * (membrane_d / 2) ** 2 * boss_h
    through_remaining = math.pi * (mounting_d / 2) ** 2 * (box_h - boss_h)
    expected = recess_vol + through_remaining
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.05


def test_vent_membrane_pocket_acoustic_vent_lv85():
    box = _box()
    v_before = _volume(box)

    r = VentMembranePocket().apply(
        box,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "membrane_spec": "Acoustic_Vent_LV85",
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before
    # 단순 sanity: 손실량은 양수이고 위쪽 recess 부피(π*(6.5/2)²*0.25 ≈ 8.3)보다 큼.
    membrane_d, boss_h = 6.5, 0.25
    recess_vol = math.pi * (membrane_d / 2) ** 2 * boss_h
    assert (v_before - v_after) > recess_vol


def test_vent_membrane_unknown_spec_raises():
    box = _box()
    with pytest.raises(ValueError, match="unknown membrane_spec"):
        VentMembranePocket().apply(
            box,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "membrane_spec": "NONEXISTENT_XYZ",
            },
        )


def test_vent_membrane_offset_position():
    """position_xy != (0,0) — vent center 가 face center 에서 떨어진 위치."""
    box = _box(l=30, w=30, h=5)
    v_before = _volume(box)
    r = VentMembranePocket().apply(
        box,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (5.0, -3.0),
            "membrane_spec": "PMF_F25",
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before


def test_vent_membrane_spec_metadata():
    spec = VentMembranePocket.spec
    assert spec.name == "vent_membrane_pocket"
    assert "vent_through_hole" in spec.produces_features
    assert "membrane_recess" in spec.produces_features
