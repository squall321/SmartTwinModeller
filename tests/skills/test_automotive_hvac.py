"""Automotive HVAC + trim pack:

  - hvac_blade_array (modify/pattern, volume_decreased)
  - barrel_pivot_socket_pair (modify/boss, volume_increased)
  - christmas_tree_fastener_post (modify/boss, volume_increased)
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.barrel_pivot_socket_pair import (
    BarrelPivotSocketPair,
)
from phone_designer.skills.modify_boss.christmas_tree_fastener_post import (
    ChristmasTreeFastenerPost,
)
from phone_designer.skills.modify_pattern.hvac_blade_array import (
    HvacBladeArray,
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _make_box(L: float = 120.0, W: float = 80.0, H: float = 20.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ── hvac_blade_array ────────────────────────────────────────────────────────


def test_hvac_blade_array_decreases_volume():
    box = _make_box(120, 80, 20)
    v0 = _volume(box)
    r = HvacBladeArray().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "blade_count": 5,
        "blade_length_mm": 60.0,
        "blade_width_mm": 6.0,
        "blade_thickness_mm": 1.2,
        "blade_pitch_mm": 10.0,
        "pivot_d_mm": 1.5,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Each slot ≈ 60 * 1.2 * 6 = 432 mm³.  5 slots → ~2160 mm³.
    one_slot = 60.0 * 1.2 * 6.0
    delta = v0 - v1
    assert delta > 0.5 * 5 * one_slot


def test_hvac_blade_array_rejects_thick_blade():
    box = _make_box(80, 60, 15)
    with pytest.raises(ValueError, match="blade_thickness_mm"):
        HvacBladeArray().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "blade_count": 4,
            "blade_length_mm": 40.0,
            "blade_width_mm": 5.0,
            "blade_thickness_mm": 8.0,
            "blade_pitch_mm": 6.0,
        })


def test_hvac_blade_array_spec_metadata():
    spec = HvacBladeArray.spec
    assert spec.name == "hvac_blade_array"
    assert spec.category == "modify/pattern"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── barrel_pivot_socket_pair ────────────────────────────────────────────────


def test_barrel_pivot_socket_pair_increases_volume():
    box = _make_box(60, 40, 15)
    v0 = _volume(box)
    r = BarrelPivotSocketPair().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "separation_mm": 20.0,
        "pivot_inner_d_mm": 2.0,
        "pivot_outer_d_mm": 5.0,
        "socket_depth_mm": 3.0,
        "boss_height_mm": 5.0,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    # Each boss ≈ π * 2.5² * 5 ≈ 98 mm³; minus bore ≈ π * 1² * 3 ≈ 9.4 mm³.
    one_net = math.pi * (2.5 ** 2) * 5.0 - math.pi * (1.0 ** 2) * 3.0
    delta = v1 - v0
    assert delta > 0.5 * 2 * one_net


def test_barrel_pivot_socket_pair_rejects_inner_geq_outer():
    box = _make_box(60, 40, 15)
    with pytest.raises(ValueError, match="pivot_inner_d_mm"):
        BarrelPivotSocketPair().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "separation_mm": 20.0,
            "pivot_inner_d_mm": 5.0,
            "pivot_outer_d_mm": 4.0,
        })


def test_barrel_pivot_socket_pair_spec_metadata():
    spec = BarrelPivotSocketPair.spec
    assert spec.name == "barrel_pivot_socket_pair"
    assert spec.category == "modify/boss"
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── christmas_tree_fastener_post ────────────────────────────────────────────


def test_christmas_tree_fastener_post_increases_volume():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = ChristmasTreeFastenerPost().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "panel_t_mm": 2.0,
        "hole_d_mm": 6.0,
        "fin_count": 3,
        "fin_pitch_mm": 2.0,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    # Shaft ≈ π * (6*0.95/2)² * (2 + 3*2 + 1) ≈ π * 8.12 * 9 ≈ 229 mm³.
    shaft_r = 6.0 * 0.95 / 2.0
    shaft_h = 2.0 + 3 * 2.0 + 2.0 * 0.5
    shaft_v = math.pi * (shaft_r ** 2) * shaft_h
    delta = v1 - v0
    # Total post (shaft + 3 cones) clearly larger than the shaft alone.
    assert delta > 0.5 * shaft_v


def test_christmas_tree_fastener_post_more_fins_more_volume():
    box1 = _make_box(40, 40, 10)
    box2 = _make_box(40, 40, 10)
    v0 = _volume(box1)
    r1 = ChristmasTreeFastenerPost().apply(box1, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "panel_t_mm": 2.0,
        "hole_d_mm": 5.0,
        "fin_count": 2,
        "fin_pitch_mm": 2.0,
    })
    r2 = ChristmasTreeFastenerPost().apply(box2, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "panel_t_mm": 2.0,
        "hole_d_mm": 5.0,
        "fin_count": 5,
        "fin_pitch_mm": 2.0,
    })
    d1 = _volume(r1.body) - v0
    d2 = _volume(r2.body) - v0
    assert d2 > d1


def test_christmas_tree_fastener_post_spec_metadata():
    spec = ChristmasTreeFastenerPost.spec
    assert spec.name == "christmas_tree_fastener_post"
    assert spec.category == "modify/boss"
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)
