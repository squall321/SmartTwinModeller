"""hole 단위 테스트."""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.hole import Hole


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def test_hole_reduces_volume():
    box_inst = Box()
    box_result = box_inst.apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    v_before = _volume(box_result.body)

    hole_inst = Hole()
    r = hole_inst.apply(
        box_result.body,
        {
            "position": (0.0, 0.0, 10.0),
            "diameter_mm": 4.0,
            "depth_mm": 5.0,
            "direction": "-Z",
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, f"hole 후 부피 감소해야: {v_before} → {v_after}"


def test_through_hole_when_depth_none():
    box_inst = Box()
    box_result = box_inst.apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    v_before = _volume(box_result.body)

    hole_inst = Hole()
    r = hole_inst.apply(
        box_result.body,
        {"position": (0.0, 0.0, 10.0), "diameter_mm": 4.0, "depth_mm": None, "direction": "-Z"},
    )
    v_after = _volume(r.body)
    # through hole = depth 10mm 의 cylinder 가 모두 body 와 겹침
    # expected loss ≈ π * 2² * 10 ≈ 125.7
    expected_loss = 3.14159 * 4 * 10
    actual_loss = v_before - v_after
    assert abs(actual_loss - expected_loss) / expected_loss < 0.05, \
        f"through hole 부피 손실: 기대 {expected_loss:.1f}, 실제 {actual_loss:.1f}"


def test_hole_rejects_zero_diameter():
    hole_inst = Hole()
    with pytest.raises(Exception):
        hole_inst.apply(
            None,
            {"position": (0.0, 0.0, 0.0), "diameter_mm": 0, "depth_mm": 5.0},
        )


def test_hole_spec_metadata():
    spec = Hole.spec
    assert spec.name == "hole"
    assert "cylindrical_hole" in spec.produces_features
