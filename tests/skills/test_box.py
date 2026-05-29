"""box skill 단위 테스트."""
from __future__ import annotations

import pytest

# import 으로 skill 등록 발동
from phone_designer.skills.create.box import Box


def test_box_creates_six_faces():
    inst = Box()
    result = inst.apply(None, {"length_mm": 20.0, "width_mm": 15.0, "height_mm": 10.0})

    body = result.body
    # build123d Part 의 face count
    faces = body.faces() if hasattr(body, "faces") else list(body.wrapped.Faces())
    # build123d 0.9 의 face count API 일관성을 위해
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    count = 0
    it = TopExp_Explorer(body.wrapped, TopAbs_FACE)
    while it.More():
        count += 1
        it.Next()
    assert count == 6, f"box 는 face 6개여야 함, got {count}"


def test_box_history_records_new_entities():
    inst = Box()
    result = inst.apply(None, {"length_mm": 10.0, "width_mm": 10.0, "height_mm": 10.0})
    assert len(result.history.new_entities) == 6
    for ref in result.history.new_entities:
        assert ref.kind == "face"


def test_box_rejects_zero_dimension():
    inst = Box()
    with pytest.raises(Exception):
        inst.apply(None, {"length_mm": 0, "width_mm": 10, "height_mm": 10})


def test_box_rejects_negative_dimension():
    inst = Box()
    with pytest.raises(Exception):
        inst.apply(None, {"length_mm": -1, "width_mm": 10, "height_mm": 10})


def test_box_spec_metadata():
    spec = Box.spec
    assert spec.name == "box"
    assert spec.level == "atomic"
    assert "cnc_3axis" in spec.manufacturing.processes
