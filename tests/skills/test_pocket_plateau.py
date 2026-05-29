"""extrude_pocket / extrude_plateau 단위 테스트."""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.extrude_plateau import ExtrudePlateau
from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def test_pocket_reduces_volume_on_top_face():
    box_inst = Box()
    box_result = box_inst.apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    v_before = _volume(box_result.body)

    pocket = ExtrudePocket()
    r = pocket.apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "sketch": {"kind": "circle", "diameter_mm": 8.0},
            "depth_mm": 1.0,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before
    # 기대 손실 ≈ π * 4² * 1 ≈ 50.27
    expected = 3.14159 * 16 * 1.0
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.05


def test_plateau_increases_volume_on_top_face():
    box_inst = Box()
    box_result = box_inst.apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    v_before = _volume(box_result.body)

    plateau = ExtrudePlateau()
    r = plateau.apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "sketch": {"kind": "circle", "diameter_mm": 6.0},
            "height_mm": 2.0,
        },
    )
    v_after = _volume(r.body)
    assert v_after > v_before
    # 기대 증가 ≈ π * 3² * 2 ≈ 56.55
    expected = 3.14159 * 9 * 2.0
    actual = v_after - v_before
    assert abs(actual - expected) / expected < 0.05


def test_pocket_rejects_no_face_match():
    pocket = ExtrudePocket()
    with pytest.raises(RuntimeError, match="0 faces"):
        # 의도적으로 매칭 안 되는 선택자 — 안 된 케이스
        pocket.apply(
            Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body,
            {
                "face_selector": {"kind": "face_named", "name": "nonexistent"},
                "sketch": {"kind": "circle", "diameter_mm": 5.0},
                "depth_mm": 1.0,
            },
        )


def test_pocket_spec_metadata():
    spec = ExtrudePocket.spec
    assert spec.name == "extrude_pocket"
    assert "pocket" in spec.produces_features


def test_plateau_spec_metadata():
    spec = ExtrudePlateau.spec
    assert spec.name == "extrude_plateau"
    assert "plateau" in spec.produces_features
