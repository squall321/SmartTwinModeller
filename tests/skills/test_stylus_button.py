"""Wave1-C pack: stylus_tip_clearance + button_cap_rounded_actuation.

Each skill runs on a simple box body. We verify:
  - volume changes in the expected direction (decreased for the pocket,
    increased for the boss),
  - approximate volume delta is consistent with the expected geometry,
  - bad arg combinations are rejected,
  - skill spec metadata is wired up correctly,
  - the export manifest picks up both new skills.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.button_cap_rounded_actuation import (
    ButtonCapRoundedActuation,
)
from phone_designer.skills.modify_pocket.stylus_tip_clearance import (
    StylusTipClearance,
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _make_box(L: float = 40.0, W: float = 40.0, H: float = 20.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ── stylus_tip_clearance ────────────────────────────────────────────────────


def test_stylus_tip_clearance_decreases_volume():
    box = _make_box(40, 40, 20)
    v0 = _volume(box)
    r = StylusTipClearance().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "tip_d_mm": 2.5,
        "throat_d_mm": 4.0,
        "depth_mm": 8.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"stylus pocket must remove volume: {v0} → {v1}"

    # Volume bounds: pocket fits inside the bounding cylinder of d=throat, h=depth
    # = π * 2² * 8 ≈ 100 mm³ (upper bound). Also strictly larger than tip-only
    # cylinder = π * 1.25² * 8 ≈ 39 mm³ (lower bound).
    delta = v0 - v1
    upper = math.pi * (4.0 / 2.0) ** 2 * 8.0
    lower = math.pi * (2.5 / 2.0) ** 2 * 8.0
    assert lower < delta < upper, (
        f"stylus pocket delta {delta:.2f} not in ({lower:.2f}, {upper:.2f})"
    )


def test_stylus_tip_clearance_rejects_tip_geq_throat():
    box = _make_box(40, 40, 20)
    with pytest.raises(ValueError, match="throat"):
        StylusTipClearance().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "tip_d_mm": 4.0,
            "throat_d_mm": 4.0,
            "depth_mm": 8.0,
        })


def test_stylus_tip_clearance_deeper_removes_more():
    """Doubling depth should remove materially more volume."""
    box1 = _make_box(40, 40, 30)
    v0_a = _volume(box1)
    r_a = StylusTipClearance().apply(box1, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "tip_d_mm": 2.5, "throat_d_mm": 4.0, "depth_mm": 6.0,
    })
    delta_a = v0_a - _volume(r_a.body)

    box2 = _make_box(40, 40, 30)
    v0_b = _volume(box2)
    r_b = StylusTipClearance().apply(box2, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "tip_d_mm": 2.5, "throat_d_mm": 4.0, "depth_mm": 12.0,
    })
    delta_b = v0_b - _volume(r_b.body)
    assert delta_b > delta_a * 1.4, (
        f"doubling depth should grow delta: {delta_a:.2f} vs {delta_b:.2f}"
    )


def test_stylus_tip_clearance_spec_metadata():
    spec = StylusTipClearance.spec
    assert spec.name == "stylus_tip_clearance"
    assert spec.category == "modify/pocket"
    assert spec.level == "atomic"
    assert "stylus_tip_clearance" in spec.produces_features
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── button_cap_rounded_actuation ────────────────────────────────────────────


def test_button_cap_increases_volume():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = ButtonCapRoundedActuation().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "base_d_mm": 6.0,
        "dome_h_mm": 1.0,
    })
    v1 = _volume(r.body)
    assert v1 > v0, f"button cap must add volume: {v0} → {v1}"

    # Expected: post (h = r = 3 mm) + dome cap.
    # post = π * 3² * 3 = 84.8 mm³
    # spherical cap (r=3, h=1) = π * h² * (R - h/3), R = (9+1)/2 = 5
    #                          = π * 1 * (5 - 0.333) = 14.66 mm³
    # total ≈ 99.5 mm³
    delta = v1 - v0
    post_v = math.pi * 3.0 ** 2 * 3.0
    R = (3.0 ** 2 + 1.0 ** 2) / (2.0 * 1.0)
    cap_v = math.pi * 1.0 ** 2 * (R - 1.0 / 3.0)
    expected = post_v + cap_v
    assert abs(delta - expected) / expected < 0.10, (
        f"button delta {delta:.2f}, expected ≈ {expected:.2f}"
    )


def test_button_cap_larger_base_adds_more_volume():
    box1 = _make_box(40, 40, 10)
    v0_a = _volume(box1)
    r_a = ButtonCapRoundedActuation().apply(box1, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "base_d_mm": 6.0, "dome_h_mm": 1.0,
    })
    delta_a = _volume(r_a.body) - v0_a

    box2 = _make_box(40, 40, 10)
    v0_b = _volume(box2)
    r_b = ButtonCapRoundedActuation().apply(box2, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "base_d_mm": 10.0, "dome_h_mm": 1.0,
    })
    delta_b = _volume(r_b.body) - v0_b
    assert delta_b > delta_a, (
        f"larger base_d should add more volume: {delta_a:.2f} vs {delta_b:.2f}"
    )


def test_button_cap_rejects_excessive_dome():
    box = _make_box(40, 40, 10)
    # dome_h > 2*r (full sphere height) — geometrically impossible cap.
    with pytest.raises(ValueError, match="dome_h"):
        ButtonCapRoundedActuation().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "base_d_mm": 4.0,
            "dome_h_mm": 10.0,
        })


def test_button_cap_spec_metadata():
    spec = ButtonCapRoundedActuation.spec
    assert spec.name == "button_cap_rounded_actuation"
    assert spec.category == "modify/boss"
    assert spec.level == "atomic"
    assert "button_cap" in spec.produces_features
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── manifest ────────────────────────────────────────────────────────────────


def test_manifest_includes_wave1c_pack():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    assert {"stylus_tip_clearance", "button_cap_rounded_actuation"} <= names
