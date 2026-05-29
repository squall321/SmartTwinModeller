"""Tests for wearable-specific skills:
   spring_bar_lug_pair (boss), crown_seal_gland (pocket),
   sapphire_glass_seat (pocket).

Each skill is exercised on a simple box body. We verify:
  - the operation changes volume in the expected direction (pockets decrease,
    bosses increase) — this is also enforced by the declared post_conditions,
  - the resulting bbox / geometry is plausible,
  - manifest-level spec attributes (category, post_conditions).
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.spring_bar_lug_pair import SpringBarLugPair
from phone_designer.skills.modify_pocket.crown_seal_gland import CrownSealGland
from phone_designer.skills.modify_pocket.sapphire_glass_seat import SapphireGlassSeat


# ── helpers ─────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _bbox(body) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(body.wrapped, bb)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def _make_box(L: float = 40.0, W: float = 40.0, H: float = 10.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ── spring_bar_lug_pair ─────────────────────────────────────────────────────


def test_spring_bar_lug_pair_increases_volume_and_grows_above_face():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = SpringBarLugPair().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "lug_separation_mm": 20.0,
        "lug_h_mm": 3.0,
        "lug_t_mm": 2.0,
        "bar_d_mm": 1.5,
        "hole_inset_mm": 0.6,
    })
    v1 = _volume(r.body)
    assert v1 > v0, "spring bar lugs must add material"
    # bbox top should reach face_z + lug_h
    (_, mx) = _bbox(r.body)
    assert mx[2] >= 10.0 + 3.0 - 0.1


def test_spring_bar_lug_pair_through_hole_removes_some_material():
    """A through-hole through both lugs should remove some material vs. no hole.

    We compare delta-volume against a coarse estimate of two solid lug boxes,
    and require the delta to be a bit smaller (i.e. some material was drilled).
    """
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = SpringBarLugPair().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "lug_separation_mm": 20.0,
        "lug_h_mm": 3.0,
        "lug_t_mm": 2.0,
        "bar_d_mm": 1.5,
        "hole_inset_mm": 0.6,
    })
    v1 = _volume(r.body)
    delta = v1 - v0
    # Each lug: footprint_x = bar_d + 2*inset = 1.5 + 1.2 = 2.7, t=2, h=3
    #   per-lug solid vol = 2.7 * 2.0 * 3.0 = 16.2  →  two lugs ≈ 32.4
    two_lug_solid = 2 * (2.7 * 2.0 * 3.0)
    # Through-hole: π * (0.75)² * (sep + 2*half_t + slop ≈ 20 + 2 + 1)
    hole_v = math.pi * 0.75 ** 2 * (20.0 + 2.0 + 1.0)
    # Delta should be less than full solid lugs and greater than (lugs - hole),
    # because the hole also bites into the box itself between the lugs.
    assert delta < two_lug_solid, (
        f"hole did not remove material: delta={delta:.2f}, "
        f"two_lug_solid={two_lug_solid:.2f}"
    )
    assert delta > two_lug_solid - 1.5 * hole_v, (
        f"too much removed: delta={delta:.2f}"
    )


def test_spring_bar_lug_pair_spec_metadata():
    spec = SpringBarLugPair.spec
    assert spec.name == "spring_bar_lug_pair"
    assert spec.category == "modify/boss"
    assert spec.level == "macro"
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── crown_seal_gland ────────────────────────────────────────────────────────


def test_crown_seal_gland_removes_annular_volume():
    box = _make_box(20, 20, 10)
    v0 = _volume(box)
    r = CrownSealGland().apply(box, {
        # bore axis enters the +Z top face of the box at its center.
        "axis_origin": (0.0, 0.0, 10.0),
        "axis_direction": (0.0, 0.0, -1.0),
        "shaft_d_mm": 2.5,
        "gland_id_mm": 2.6,
        "gland_od_mm": 3.6,
        "gland_d_mm": 0.85,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Annular ring volume = π * (R² - r²) * d
    R = 1.8
    r_ = 1.3
    expected = math.pi * (R * R - r_ * r_) * 0.85
    actual = v0 - v1
    # generous tolerance — entry overshoots slightly
    assert 0.7 * expected < actual < 1.6 * expected, (
        f"gland removal: expected ≈ {expected:.3f}, got {actual:.3f}"
    )


def test_crown_seal_gland_rejects_id_below_shaft():
    box = _make_box(20, 20, 10)
    with pytest.raises(ValueError, match="shaft_d_mm"):
        CrownSealGland().apply(box, {
            "axis_origin": (0.0, 0.0, 10.0),
            "axis_direction": (0.0, 0.0, -1.0),
            "shaft_d_mm": 3.0,
            "gland_id_mm": 2.6,
            "gland_od_mm": 3.6,
            "gland_d_mm": 0.85,
        })


def test_crown_seal_gland_rejects_od_le_id():
    box = _make_box(20, 20, 10)
    with pytest.raises(ValueError, match="gland_od_mm"):
        CrownSealGland().apply(box, {
            "axis_origin": (0.0, 0.0, 10.0),
            "axis_direction": (0.0, 0.0, -1.0),
            "shaft_d_mm": 2.5,
            "gland_id_mm": 3.6,
            "gland_od_mm": 3.6,
            "gland_d_mm": 0.85,
        })


def test_crown_seal_gland_spec_metadata():
    spec = CrownSealGland.spec
    assert spec.name == "crown_seal_gland"
    assert spec.category == "modify/pocket"
    assert spec.level == "atomic"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── sapphire_glass_seat ─────────────────────────────────────────────────────


def test_sapphire_glass_seat_decreases_volume_with_stepped_pocket():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = SapphireGlassSeat().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "glass_d_mm": 30.0,
        "glass_t_mm": 0.8,
        "adhesive_step_d_mm": 0.4,
        "gasket_step_d_mm": 0.15,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Expected removed volume (steps stacked):
    #   glass pocket: π * 15² * 0.8 ≈ 565.5
    #   adhesive step radius = 15 - 0.15 = 14.85: π * 14.85² * 0.4 ≈ 277.2
    #   gasket step radius = 15 - 0.30 = 14.70: π * 14.70² * 0.15 ≈ 101.8
    expected = (
        math.pi * 15.0 ** 2 * 0.8
        + math.pi * 14.85 ** 2 * 0.4
        + math.pi * 14.70 ** 2 * 0.15
    )
    actual = v0 - v1
    # generous tolerance — entry overshoots add a little
    assert 0.85 * expected < actual < 1.15 * expected, (
        f"sapphire seat removal: expected ≈ {expected:.2f}, got {actual:.2f}"
    )


def test_sapphire_glass_seat_lowest_point_below_face():
    box = _make_box(40, 40, 10)
    r = SapphireGlassSeat().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "glass_d_mm": 20.0,
        "glass_t_mm": 0.8,
        "adhesive_step_d_mm": 0.4,
        "gasket_step_d_mm": 0.15,
    })
    # bbox bottom should remain 0 (box origin), and top remains 10
    (mn, mx) = _bbox(r.body)
    assert mx[2] == pytest.approx(10.0, abs=0.05)
    assert mn[2] == pytest.approx(0.0, abs=0.05)


def test_sapphire_glass_seat_rejects_too_large_gasket_step():
    box = _make_box(40, 40, 10)
    with pytest.raises(ValueError, match="gasket_step"):
        SapphireGlassSeat().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "glass_d_mm": 1.0,
            "glass_t_mm": 0.8,
            "adhesive_step_d_mm": 0.4,
            "gasket_step_d_mm": 0.30,
        })


def test_sapphire_glass_seat_spec_metadata():
    spec = SapphireGlassSeat.spec
    assert spec.name == "sapphire_glass_seat"
    assert spec.category == "modify/pocket"
    assert spec.level == "atomic"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)
