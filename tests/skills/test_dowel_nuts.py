"""dowel_pin_hole / nut_trap_hex / nut_trap_square / captive_nut_pocket 단위 테스트.

각 skill 은
  - catalog 의 size/spec key 로 cutter geometry 결정
  - face_selector + position_xy 로 cut
  - post_condition 'volume_decreased' 가 silent no-op 도 잡는다.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.captive_nut_pocket import CaptiveNutPocket
from phone_designer.skills.modify_pocket.dowel_pin_hole import DowelPinHole
from phone_designer.skills.modify_pocket.nut_trap_hex import NutTrapHex
from phone_designer.skills.modify_pocket.nut_trap_square import NutTrapSquare


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return float(props.Mass())


def _make_box(L=40.0, W=40.0, H=15.0):
    return Box().apply(None, {
        "length_mm": L, "width_mm": W, "height_mm": H,
    }).body


# face_selector for box top (Z=H, normal +Z)
TOP_SEL = {"kind": "face_named", "name": "top"}


# ─────────────────────────────────── dowel_pin_hole ────────────────────────


def test_dowel_pin_hole_press_fit_reduces_volume():
    body = _make_box()
    v_before = _volume(body)
    r = DowelPinHole().apply(
        body,
        {
            "face_selector": TOP_SEL,
            "position_xy": (0.0, 0.0),
            "pin_diameter_mm": "3",
            "fit": "press",
            "depth_mm": 5.0,
        },
    )
    v_after = _volume(r.body)
    delta = v_before - v_after
    # H7 hole for ø3 = 3.010 → r^2*pi*depth = (1.505)^2 * pi * 5 ≈ 35.6
    expected = math.pi * (3.010 / 2.0) ** 2 * 5.0
    assert delta > 0.7 * expected, f"delta {delta:.3f}, expected ~{expected:.3f}"
    assert delta < 1.3 * expected, f"delta {delta:.3f}, expected ~{expected:.3f}"


def test_dowel_pin_hole_slip_fit_is_larger_than_press():
    """H8 hole > H7 hole at every size, so slip-fit removes more material."""
    base = _make_box()
    v_base = _volume(base)

    press = DowelPinHole().apply(
        base,
        {"face_selector": TOP_SEL, "position_xy": (0.0, 0.0),
         "pin_diameter_mm": "5", "fit": "press", "depth_mm": 5.0},
    )
    slip = DowelPinHole().apply(
        _make_box(),
        {"face_selector": TOP_SEL, "position_xy": (0.0, 0.0),
         "pin_diameter_mm": "5", "fit": "slip", "depth_mm": 5.0},
    )
    d_press = v_base - _volume(press.body)
    d_slip = v_base - _volume(slip.body)
    assert d_slip > d_press, f"slip {d_slip} should exceed press {d_press}"


def test_dowel_pin_hole_through_pierces_body():
    body = _make_box(L=20, W=20, H=8)
    v_before = _volume(body)
    r = DowelPinHole().apply(
        body,
        {
            "face_selector": TOP_SEL,
            "position_xy": (0.0, 0.0),
            "pin_diameter_mm": "4",
            "fit": "press",
            "depth_mm": 1.0,    # ignored because through=True
            "through": True,
        },
    )
    v_after = _volume(r.body)
    delta = v_before - v_after
    # ø4.012 through 8mm thick box → ~101 mm^3
    expected = math.pi * (4.012 / 2.0) ** 2 * 8.0
    assert delta > 0.8 * expected, (
        f"through delta {delta:.3f} too small (expected ~{expected:.3f})"
    )


def test_dowel_pin_hole_rejects_unknown_size():
    body = _make_box()
    with pytest.raises(ValueError):
        DowelPinHole().apply(
            body,
            {"face_selector": TOP_SEL, "position_xy": (0.0, 0.0),
             "pin_diameter_mm": "99", "fit": "press", "depth_mm": 3.0},
        )


def test_dowel_pin_hole_spec_metadata():
    spec = DowelPinHole.spec
    assert spec.name == "dowel_pin_hole"
    assert "dowel_pin_hole" in spec.produces_features
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ─────────────────────────────────── nut_trap_hex ──────────────────────────


def test_nut_trap_hex_m3_reduces_volume():
    body = _make_box()
    v_before = _volume(body)
    r = NutTrapHex().apply(
        body,
        {
            "face_selector": TOP_SEL,
            "position_xy": (0.0, 0.0),
            "nut_spec": "M3",
        },
    )
    v_after = _volume(r.body)
    delta = v_before - v_after
    # M3: AF 5.5 + 0.1 = 5.6. Regular hex inscribed (flat-to-flat) AF
    # has area = sqrt(3)/2 * AF^2 (apothem = AF/2, perimeter 6*AF/sqrt(3)).
    # depth = thickness 2.4 → ~65 mm^3
    af = 5.5 + 0.1
    expected = (math.sqrt(3.0) / 2.0) * af * af * 2.4
    assert delta > 0.7 * expected, f"delta {delta:.3f}, expected ~{expected:.3f}"
    assert delta < 1.3 * expected, f"delta {delta:.3f}, expected ~{expected:.3f}"


def test_nut_trap_hex_depth_override_works():
    """Larger depth_mm_override should remove more material."""
    base_v = _volume(_make_box())
    r_default = NutTrapHex().apply(
        _make_box(),
        {"face_selector": TOP_SEL, "position_xy": (0, 0), "nut_spec": "M4"},
    )
    r_deep = NutTrapHex().apply(
        _make_box(),
        {"face_selector": TOP_SEL, "position_xy": (0, 0), "nut_spec": "M4",
         "depth_mm_override": 6.0},
    )
    d_default = base_v - _volume(r_default.body)
    d_deep = base_v - _volume(r_deep.body)
    assert d_deep > d_default, f"deeper {d_deep} should exceed default {d_default}"


def test_nut_trap_hex_orientation_invariant_volume():
    """Hex prism volume invariant under any rotation about the normal."""
    base_v = _volume(_make_box())
    r0 = NutTrapHex().apply(
        _make_box(),
        {"face_selector": TOP_SEL, "position_xy": (0, 0), "nut_spec": "M3"},
    )
    r60 = NutTrapHex().apply(
        _make_box(),
        {"face_selector": TOP_SEL, "position_xy": (0, 0), "nut_spec": "M3",
         "orientation_deg": 30.0},
    )
    d0 = base_v - _volume(r0.body)
    d60 = base_v - _volume(r60.body)
    assert abs(d0 - d60) / d0 < 0.01, f"rotation changed volume: {d0} vs {d60}"


def test_nut_trap_hex_rejects_unknown_spec():
    body = _make_box()
    with pytest.raises(ValueError):
        NutTrapHex().apply(
            body,
            {"face_selector": TOP_SEL, "position_xy": (0, 0), "nut_spec": "M99"},
        )


# ─────────────────────────────────── nut_trap_square ───────────────────────


def test_nut_trap_square_m4_reduces_volume():
    body = _make_box()
    v_before = _volume(body)
    r = NutTrapSquare().apply(
        body,
        {"face_selector": TOP_SEL, "position_xy": (0, 0), "nut_spec": "M4"},
    )
    v_after = _volume(r.body)
    delta = v_before - v_after
    # M4 square: AF 7.0 + 0.1 = 7.1, thickness 2.2 → 7.1^2 * 2.2 = 110.9
    side = 7.0 + 0.1
    expected = side * side * 2.2
    assert delta > 0.7 * expected, f"delta {delta:.3f}, expected ~{expected:.3f}"
    assert delta < 1.3 * expected, f"delta {delta:.3f}, expected ~{expected:.3f}"


def test_nut_trap_square_rejects_unknown_spec():
    body = _make_box()
    with pytest.raises(ValueError):
        NutTrapSquare().apply(
            body,
            {"face_selector": TOP_SEL, "position_xy": (0, 0), "nut_spec": "M2"},
        )


# ─────────────────────────────────── captive_nut_pocket ────────────────────


def test_captive_nut_pocket_removes_more_than_closed_hex():
    """Captive (slide-in) pocket removes hex + slot, so > pure hex trap."""
    body_a = _make_box()
    v_before = _volume(body_a)
    r_closed = NutTrapHex().apply(
        body_a,
        {"face_selector": TOP_SEL, "position_xy": (0, 0), "nut_spec": "M3"},
    )
    body_b = _make_box()
    r_captive = CaptiveNutPocket().apply(
        body_b,
        {"face_selector": TOP_SEL, "position_xy": (0, 0),
         "nut_spec": "M3", "slide_in_height_mm": 2.0},
    )
    d_closed = v_before - _volume(r_closed.body)
    d_captive = v_before - _volume(r_captive.body)
    assert d_captive > d_closed, (
        f"captive {d_captive:.3f} should exceed closed hex {d_closed:.3f}"
    )


def test_captive_nut_pocket_rejects_unknown_spec():
    body = _make_box()
    with pytest.raises(ValueError):
        CaptiveNutPocket().apply(
            body,
            {"face_selector": TOP_SEL, "position_xy": (0, 0),
             "nut_spec": "M99", "slide_in_height_mm": 2.0},
        )


def test_captive_nut_pocket_spec_metadata():
    spec = CaptiveNutPocket.spec
    assert spec.name == "captive_nut_pocket"
    assert "captive_nut_pocket" in spec.produces_features
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ─────────────────────────────────── catalog smoke ─────────────────────────


def test_dowel_pin_catalog_loads():
    from phone_designer.skills.modify_pocket.dowel_pin_hole import _load
    cat = _load("dowel_pins")
    pins = cat["pins"]
    for k in ["2", "2.5", "3", "4", "5", "6", "8", "10"]:
        assert k in pins, f"missing dowel pin {k}"
        assert pins[k]["press_fit_hole_mm"] > 0
        assert pins[k]["slip_fit_hole_mm"] > pins[k]["press_fit_hole_mm"]


def test_hex_nuts_catalog_loads():
    from phone_designer.skills.modify_pocket.nut_trap_hex import _load
    cat = _load("hex_nuts_din934")
    nuts = cat["nuts"]
    for k in ["M2", "M2.5", "M3", "M4", "M5", "M6", "M8", "M10"]:
        assert k in nuts, f"missing hex nut {k}"
        assert nuts[k]["across_flats_mm"] > 0
        assert nuts[k]["thickness_mm"] > 0


def test_square_nuts_catalog_loads():
    from phone_designer.skills.modify_pocket.nut_trap_square import _load
    cat = _load("square_nuts")
    nuts = cat["nuts"]
    for k in ["M3", "M4", "M5", "M6", "M8"]:
        assert k in nuts, f"missing square nut {k}"
        assert nuts[k]["across_flats_mm"] > 0
        assert nuts[k]["thickness_mm"] > 0
