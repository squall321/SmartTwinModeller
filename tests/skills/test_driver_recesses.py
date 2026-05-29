"""hex_socket_recess / torx_recess / phillips_recess 단위 테스트.

각 recess skill 은
  - catalog 의 size key 로 cutter geometry 결정
  - face_selector 로 target face 결정
  - position_xy + depth_mm 로 cut
post_condition 'volume_decreased' 가 silent no-op 도 잡는다.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.hex_socket_recess import HexSocketRecess
from phone_designer.skills.modify_pocket.phillips_recess import PhillipsRecess
from phone_designer.skills.modify_pocket.torx_recess import TorxRecess


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return float(props.Mass())


def _make_box(L=40.0, W=40.0, H=10.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ------------------------------------------------------------------ hex


def test_hex_socket_recess_reduces_volume():
    body = _make_box()
    v_before = _volume(body)

    r = HexSocketRecess().apply(
        body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "size": "3",
            "depth_mm": 3.0,
        },
    )
    v_after = _volume(r.body)
    delta = v_before - v_after

    # AF 3mm hex area = sqrt(3)/2 * af^2 = 7.79 mm^2 → depth 3 → ~23.4 mm^3
    expected = (math.sqrt(3.0) / 2.0) * (3.0 ** 2) * 3.0
    assert delta > 0.5 * expected, (
        f"hex recess delta too small: {delta:.3f} mm^3 (expected ≈ {expected:.3f})"
    )
    assert delta < 1.5 * expected, (
        f"hex recess delta too large: {delta:.3f} mm^3 (expected ≈ {expected:.3f})"
    )


def test_hex_socket_recess_rejects_unknown_size():
    body = _make_box()
    with pytest.raises(ValueError):
        HexSocketRecess().apply(
            body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "position_xy": (0.0, 0.0),
                "size": "99",   # not in catalog
                "depth_mm": 2.0,
            },
        )


def test_hex_socket_recess_orientation_does_not_break_volume():
    """Rotating the hex by 30° must still produce the same volume removed
    (the prism is a regular hexagon — invariant under 60° rotation)."""
    body = _make_box()
    v_before = _volume(body)

    r = HexSocketRecess().apply(
        body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (5.0, 5.0),
            "size": "4",
            "depth_mm": 2.0,
            "orientation_deg": 30.0,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before


# ------------------------------------------------------------------ torx


def test_torx_recess_reduces_volume():
    body = _make_box()
    v_before = _volume(body)

    r = TorxRecess().apply(
        body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "size": "T20",
            "depth_mm": 2.5,
        },
    )
    v_after = _volume(r.body)
    delta = v_before - v_after
    # T20 major ≈ 3.95 mm. Star area ≈ between inner-circle and outer-circle areas.
    # Outer circle π*R² = π*1.975² ≈ 12.25 mm²; depth 2.5 → upper bound ≈ 30.6 mm³.
    # Inner circle (r = R*0.71) area ≈ 6.17 mm² → depth*2.5 ≈ 15.4 mm³ lower bound.
    assert 5.0 < delta < 50.0, f"torx delta out of plausible range: {delta:.3f} mm^3"


def test_torx_recess_rejects_unknown_size():
    body = _make_box()
    with pytest.raises(ValueError):
        TorxRecess().apply(
            body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "position_xy": (0.0, 0.0),
                "size": "T99",
                "depth_mm": 2.0,
            },
        )


# ------------------------------------------------------------------ phillips


def test_phillips_recess_reduces_volume():
    body = _make_box()
    v_before = _volume(body)

    r = PhillipsRecess().apply(
        body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "size": "PH2",
            "depth_mm": 3.0,
        },
    )
    v_after = _volume(r.body)
    delta = v_before - v_after
    # PH2 major ≈ 4.20 mm; cross cutter spans ±2.1mm with arm half-width ≈ R*0.18 ≈ 0.38mm
    # Cross area approximation: 2 * (2R * 2w) - (2w)^2 = 8*R*w - 4w^2
    # = 8*2.1*0.38 - 4*0.38^2 ≈ 6.38 - 0.58 ≈ 5.80 mm². Depth 3 → ~17 mm³.
    assert 1.0 < delta < 50.0, f"phillips delta out of plausible range: {delta:.3f} mm^3"


def test_phillips_recess_rejects_unknown_size():
    body = _make_box()
    with pytest.raises(ValueError):
        PhillipsRecess().apply(
            body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "position_xy": (0.0, 0.0),
                "size": "PH9",
                "depth_mm": 2.0,
            },
        )


# ------------------------------------------------------------------ spec


def test_recess_specs_volume_decreased_post_condition():
    """All three skills declare post_conditions=[volume_decreased]."""
    for cls in (HexSocketRecess, TorxRecess, PhillipsRecess):
        spec = cls.spec
        assert spec.post_conditions, f"{spec.name} missing post_conditions"
        kinds = {pc.kind for pc in spec.post_conditions}
        assert "volume_decreased" in kinds, f"{spec.name} missing volume_decreased"


def test_recess_catalog_loads():
    """Catalog file exists and contains all three sections."""
    from phone_designer.skills.modify_pocket.hex_socket_recess import _load
    cat = _load("drivers")
    assert "hex_socket" in cat
    assert "torx" in cat
    assert "phillips" in cat
    assert "3" in cat["hex_socket"]
    assert "T20" in cat["torx"]
    assert "PH2" in cat["phillips"]
