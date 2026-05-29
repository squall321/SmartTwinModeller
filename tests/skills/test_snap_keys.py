"""Tests for snap-ring grooves + DIN 6885 keyseats.

Skills under test:
    - snap_ring_groove_external (DIN 471)
    - snap_ring_groove_internal (DIN 472)
    - keyseat_shaft             (DIN 6885 Form A, shaft side)
    - keyseat_hub               (DIN 6885 Form A, hub side)
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.hole import Hole
from phone_designer.skills.modify_pocket.keyseat_hub import KeyseatHub
from phone_designer.skills.modify_pocket.keyseat_shaft import KeyseatShaft
from phone_designer.skills.modify_pocket.snap_ring_groove_external import (
    SnapRingGrooveExternal,
)
from phone_designer.skills.modify_pocket.snap_ring_groove_internal import (
    SnapRingGrooveInternal,
)


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _make_cylinder(diameter_mm: float, height_mm: float):
    from build123d import Align, Cylinder
    return Cylinder(
        radius=diameter_mm / 2.0,
        height=height_mm,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )


# ── snap_ring_groove_external ──────────────────────────────────────────────


def test_snap_ring_external_removes_volume():
    """20 mm shaft, groove at z=10. Volume must decrease by an amount close to
    the annular ring (π · (R² − r²) · w) with catalog DIN 471 d=20 values."""
    body = _make_cylinder(diameter_mm=20.0, height_mm=20.0)
    v0 = _volume(body)

    r = SnapRingGrooveExternal().apply(body, {
        "axis_origin": (0.0, 0.0, 0.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "shaft_diameter_mm": 20.0,
        "axial_position_mm": 10.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"external snap-ring groove must remove volume ({v0} → {v1})"

    import math
    # DIN 471 d=20: groove_d = 19.0, groove_width = 1.3
    # Expected ≈ π · (10² − 9.5²) · 1.3 ≈ 39.8 mm³. Loose bounds.
    removed = v0 - v1
    expected = math.pi * (10.0 ** 2 - 9.5 ** 2) * 1.3
    assert 0.3 * expected < removed < 3.0 * expected, (
        f"removed volume {removed:.2f} mm³ far from expected {expected:.2f}"
    )


def test_snap_ring_external_post_condition_kind():
    spec = SnapRingGrooveExternal.spec
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── snap_ring_groove_internal ──────────────────────────────────────────────


def test_snap_ring_internal_in_drilled_hole():
    """Box with a through-bore D=20; cut an internal DIN 472 groove inside."""
    body = Box().apply(None, {
        "length_mm": 40.0, "width_mm": 40.0, "height_mm": 30.0,
    }).body
    drilled = Hole().apply(body, {
        "position": (0.0, 0.0, 30.0),
        "diameter_mm": 20.0,
        "direction": "-Z",
        "depth_mm": 30.0,
    }).body
    v0 = _volume(drilled)

    r = SnapRingGrooveInternal().apply(drilled, {
        "axis_origin": (0.0, 0.0, 0.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "bore_diameter_mm": 20.0,
        "axial_position_mm": 15.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0, (
        f"internal snap-ring groove must remove material ({v0} → {v1})"
    )


def test_snap_ring_internal_post_condition_kind():
    spec = SnapRingGrooveInternal.spec
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── keyseat_shaft ─────────────────────────────────────────────────────────


def test_keyseat_shaft_basic_removes_volume():
    """20 mm shaft, 30 mm keyseat. DIN 6885 (17 < d ≤ 22): b=6, t1=3.5."""
    body = _make_cylinder(diameter_mm=20.0, height_mm=50.0)
    v0 = _volume(body)

    r = KeyseatShaft().apply(body, {
        "axis_origin": (0.0, 0.0, 0.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "shaft_diameter_mm": 20.0,
        "axial_start_mm": 10.0,
        "length_mm": 30.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"keyseat must remove volume ({v0} → {v1})"

    # Coarse sanity: slot box volume ≈ b · t1 · length = 6 · 3.5 · 30 = 630 mm³.
    # Actual removed is less because the box overshoots the cylinder slightly
    # and is clipped by the curved OD; still must be at least a few hundred
    # mm³, not zero.
    removed = v0 - v1
    assert removed > 50.0, f"removed too little: {removed:.2f} mm³"


def test_keyseat_shaft_post_condition_kind():
    spec = KeyseatShaft.spec
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── keyseat_hub ───────────────────────────────────────────────────────────


def test_keyseat_hub_through_bore():
    """40×40×20 box with D=20 through bore; cut a through hub keyseat."""
    body = Box().apply(None, {
        "length_mm": 40.0, "width_mm": 40.0, "height_mm": 20.0,
    }).body
    drilled = Hole().apply(body, {
        "position": (0.0, 0.0, 20.0),
        "diameter_mm": 20.0,
        "direction": "-Z",
        "depth_mm": 20.0,
    }).body
    v0 = _volume(drilled)

    r = KeyseatHub().apply(drilled, {
        "axis_origin": (0.0, 0.0, 0.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "bore_diameter_mm": 20.0,
        "length_mm": 20.0,
        "through": True,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"hub keyseat must remove volume ({v0} → {v1})"


def test_keyseat_hub_blind():
    """Blind hub keyseat — length confined to [0, length_mm]."""
    body = Box().apply(None, {
        "length_mm": 40.0, "width_mm": 40.0, "height_mm": 30.0,
    }).body
    drilled = Hole().apply(body, {
        "position": (0.0, 0.0, 30.0),
        "diameter_mm": 20.0,
        "direction": "-Z",
        "depth_mm": 30.0,
    }).body
    v0 = _volume(drilled)

    r = KeyseatHub().apply(drilled, {
        "axis_origin": (0.0, 0.0, 0.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "bore_diameter_mm": 20.0,
        "length_mm": 15.0,
        "through": False,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"blind hub keyseat must remove volume ({v0} → {v1})"

    v0_through = v0
    # Through cut should remove strictly more than blind cut of the same
    # length (it spans the full height instead of half).
    r_through = KeyseatHub().apply(drilled, {
        "axis_origin": (0.0, 0.0, 0.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "bore_diameter_mm": 20.0,
        "length_mm": 15.0,
        "through": True,
    })
    v_through = _volume(r_through.body)
    assert v_through < v1, (
        f"through keyseat should remove more than blind: "
        f"through={v_through}, blind={v1}"
    )


def test_keyseat_hub_post_condition_kind():
    spec = KeyseatHub.spec
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)
