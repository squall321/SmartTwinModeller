"""Tests for specialty boss skills: standoff, gusset, heat_stake_boss, heatsink_fin.

Each skill is exercised on a simple box body. We verify:
  - the operation increases body volume (also enforced by post_conditions),
  - the resulting bbox is plausible (roughly = original bbox enlarged by the boss),
  - manifest-level spec attributes (category, post_conditions).
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.gusset import Gusset
from phone_designer.skills.modify_boss.heat_stake_boss import HeatStakeBoss
from phone_designer.skills.modify_boss.heatsink_fin import HeatsinkFin
from phone_designer.skills.modify_boss.standoff import Standoff


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


def _make_box(L: float = 30.0, W: float = 30.0, H: float = 10.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ── standoff ────────────────────────────────────────────────────────────────


def test_standoff_increases_volume_with_through_hole():
    box = _make_box(30, 30, 10)
    v0 = _volume(box)
    r = Standoff().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "outer_diameter_mm": 6.0,
        "inner_diameter_mm": 3.0,
        "height_mm": 5.0,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    # expected ring volume = π * (R² - r²) * h = π * (9 - 2.25) * 5 ≈ 106.0
    ring = math.pi * (9.0 - 2.25) * 5.0
    delta = v1 - v0
    # allow 10% (boolean tolerance + boss base shaving)
    assert abs(delta - ring) / ring < 0.10
    # bbox expanded upward by ~5 mm
    (_, mx) = _bbox(r.body)
    assert mx[2] >= 10.0 + 5.0 - 0.1


def test_standoff_with_taper_still_increases_volume():
    box = _make_box(30, 30, 10)
    v0 = _volume(box)
    r = Standoff().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "outer_diameter_mm": 8.0,
        "inner_diameter_mm": 3.0,
        "height_mm": 5.0,
        "taper_deg": 3.0,
    })
    assert _volume(r.body) > v0
    # tapered upper r < base r, so total < straight cylinder ring vol
    straight = math.pi * (16.0 - 2.25) * 5.0
    delta = _volume(r.body) - v0
    assert delta < straight


def test_standoff_rejects_inner_geq_outer():
    box = _make_box()
    with pytest.raises(ValueError, match="no wall remains"):
        Standoff().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "outer_diameter_mm": 5.0,
            "inner_diameter_mm": 5.0,
            "height_mm": 3.0,
        })


def test_standoff_spec_metadata():
    spec = Standoff.spec
    assert spec.name == "standoff"
    assert spec.category == "modify/boss"
    assert spec.level == "atomic"
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── gusset ──────────────────────────────────────────────────────────────────


def test_gusset_between_top_and_right_face_adds_volume():
    """top face (+Z) and +X face — shared edge along Y."""
    box = _make_box(30, 30, 10)
    v0 = _volume(box)
    r = Gusset().apply(box, {
        "face_a_selector": {"kind": "face_named", "name": "top"},
        "face_b_selector": {"kind": "faces_by_normal", "direction": (1.0, 0.0, 0.0)},
        "length_mm": 4.0,
        "height_mm": 4.0,
        "thickness_mm": 1.5,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    # triangle prism volume = 0.5 * 4 * 4 * 1.5 = 12.0
    expected = 0.5 * 4.0 * 4.0 * 1.5
    delta = v1 - v0
    assert abs(delta - expected) / expected < 0.10


def test_gusset_between_top_and_front_face():
    """top (+Z) and +Y face — shared edge along X."""
    box = _make_box(30, 30, 10)
    v0 = _volume(box)
    r = Gusset().apply(box, {
        "face_a_selector": {"kind": "face_named", "name": "top"},
        "face_b_selector": {"kind": "faces_by_normal", "direction": (0.0, 1.0, 0.0)},
        "length_mm": 3.0,
        "height_mm": 3.0,
        "thickness_mm": 2.0,
    })
    assert _volume(r.body) > v0


def test_gusset_rejects_parallel_faces():
    box = _make_box()
    with pytest.raises(ValueError, match="not perpendicular"):
        Gusset().apply(box, {
            "face_a_selector": {"kind": "face_named", "name": "top"},
            "face_b_selector": {"kind": "face_named", "name": "bottom"},
            "length_mm": 4.0,
            "height_mm": 4.0,
            "thickness_mm": 1.5,
        })


def test_gusset_spec_metadata():
    spec = Gusset.spec
    assert spec.name == "gusset"
    assert spec.category == "modify/boss"
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── heat_stake_boss ─────────────────────────────────────────────────────────


def test_heat_stake_boss_adds_volume_and_extends_above_face():
    box = _make_box(30, 30, 10)
    v0 = _volume(box)
    r = HeatStakeBoss().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "shaft_diameter_mm": 3.0,
        "shaft_height_mm": 4.0,
        "crown_chamfer_deg": 45.0,
        "crown_height_mm": 0.5,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    # shaft vol = π * 1.5² * 4 ≈ 28.27
    shaft = math.pi * 1.5 ** 2 * 4.0
    delta = v1 - v0
    # delta should be at least the shaft volume; crown adds a bit more
    assert delta >= shaft * 0.9
    # boss top z = 10 + 4 + 0.5 = 14.5
    (_, mx) = _bbox(r.body)
    assert mx[2] >= 14.5 - 0.1


def test_heat_stake_boss_zero_chamfer_uses_straight_cylinder_crown():
    box = _make_box(30, 30, 10)
    v0 = _volume(box)
    r = HeatStakeBoss().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "shaft_diameter_mm": 2.0,
        "shaft_height_mm": 3.0,
        "crown_chamfer_deg": 0.0,
        "crown_height_mm": 0.5,
    })
    v1 = _volume(r.body)
    # total = π * 1² * 3.5 ≈ 11.0
    expected = math.pi * 1.0 ** 2 * 3.5
    delta = v1 - v0
    assert abs(delta - expected) / expected < 0.05


def test_heat_stake_boss_spec_metadata():
    spec = HeatStakeBoss.spec
    assert spec.name == "heat_stake_boss"
    assert spec.category == "modify/boss"
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── heatsink_fin ────────────────────────────────────────────────────────────


def test_heatsink_fin_no_taper_is_a_box():
    box = _make_box(30, 30, 10)
    v0 = _volume(box)
    r = HeatsinkFin().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "length_mm": 10.0,
        "width_mm": 1.0,
        "height_mm": 5.0,
    })
    v1 = _volume(r.body)
    expected = 10.0 * 1.0 * 5.0
    delta = v1 - v0
    assert abs(delta - expected) / expected < 0.05
    # bbox top z ≥ 15
    (_, mx) = _bbox(r.body)
    assert mx[2] >= 15.0 - 0.1


def test_heatsink_fin_with_top_taper_volume_between_two_extremes():
    """fin with top_taper_mm: volume is between the upper-rectangle prism
    and the base-rectangle prism (a trapezoidal loft sits in between)."""
    box = _make_box(30, 30, 10)
    v0 = _volume(box)
    r = HeatsinkFin().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "length_mm": 10.0,
        "width_mm": 1.0,
        "height_mm": 5.0,
        "top_taper_mm": 4.0,
    })
    delta = _volume(r.body) - v0
    box_full = 10.0 * 1.0 * 5.0       # 50.0
    box_min = 6.0 * 1.0 * 5.0          # 30.0
    # loft volume = h * (A1 + A2 + sqrt(A1*A2)) / 3, but our two sections share
    # the same width so the loft is a trapezoid-prism along X:
    #   avg = (10 + 6) / 2 * 1 = 8 mm² × 5 = 40 mm³
    assert box_min - 1.0 < delta < box_full + 1.0


def test_heatsink_fin_rejects_taper_geq_length():
    box = _make_box()
    with pytest.raises(ValueError, match="degenerate"):
        HeatsinkFin().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "length_mm": 5.0,
            "width_mm": 1.0,
            "height_mm": 3.0,
            "top_taper_mm": 5.0,
        })


def test_heatsink_fin_spec_metadata():
    spec = HeatsinkFin.spec
    assert spec.name == "heatsink_fin"
    assert spec.category == "modify/boss"
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)
