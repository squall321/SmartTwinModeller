"""fillet_edges_by_predicate 단위 테스트."""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_curvature.fillet_predicate import FilletEdgesByPredicate
from phone_designer.skills._selectors import AxisAlignedEdgesSelector


def _count_faces(body) -> int:
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    count = 0
    it = TopExp_Explorer(body.wrapped, TopAbs_FACE)
    while it.More():
        count += 1
        it.Next()
    return count


def test_fillet_z_edges_adds_faces():
    box_inst = Box()
    box_result = box_inst.apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    initial_faces = _count_faces(box_result.body)
    assert initial_faces == 6

    fillet_inst = FilletEdgesByPredicate()
    fillet_result = fillet_inst.apply(
        box_result.body,
        {
            "selector": AxisAlignedEdgesSelector(axis="Z").model_dump(),
            "radius_mm": 2.0,
        },
    )
    final_faces = _count_faces(fillet_result.body)
    assert final_faces > initial_faces, "fillet 후 face 수가 늘어나야 함"


def test_fillet_freeze_matches_4_edges_on_box():
    box_inst = Box()
    box_result = box_inst.apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})

    fillet_inst = FilletEdgesByPredicate()
    fillet_result = fillet_inst.apply(
        box_result.body,
        {
            "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
            "radius_mm": 1.5,
        },
    )
    assert fillet_result.selector_freeze is not None
    assert fillet_result.selector_freeze.matched_count == 4
    assert len(fillet_result.selector_freeze.topology_signature) == 16


def test_fillet_freeze_deterministic_across_rebuilds():
    """같은 plan 5회 재실행 → freeze signature 동일."""
    signatures = []
    for _ in range(5):
        box_inst = Box()
        box_result = box_inst.apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
        fillet_inst = FilletEdgesByPredicate()
        fr = fillet_inst.apply(
            box_result.body,
            {"selector": {"kind": "axis_aligned_edges", "axis": "Z"}, "radius_mm": 1.5},
        )
        signatures.append(fr.selector_freeze.topology_signature)
    assert len(set(signatures)) == 1, f"signatures: {set(signatures)}"


def test_fillet_rejects_no_match():
    box_inst = Box()
    box_result = box_inst.apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    fillet_inst = FilletEdgesByPredicate()
    # X-축 edge — box 의 면 모서리 (위/아래) 4개 있지만 selector 가 axis_aligned 만 잡음
    # 즉 X-axis edge 는 길이축의 4개 — 있어야 함. 대신 unused position 박스 사용
    with pytest.raises(RuntimeError, match="0 edges"):
        fillet_inst.apply(
            box_result.body,
            {
                "selector": {
                    "kind": "edges_by_position",
                    "bbox": {"min": [1000, 1000, 1000], "max": [2000, 2000, 2000]},
                },
                "radius_mm": 1.0,
            },
        )


def test_fillet_rejects_out_of_range_radius():
    fillet_inst = FilletEdgesByPredicate()
    with pytest.raises(Exception):  # pydantic ValidationError
        fillet_inst.apply(
            None,
            {
                "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
                "radius_mm": 100.0,  # max=50 위반
            },
        )


def test_fillet_spec_metadata():
    spec = FilletEdgesByPredicate.spec
    assert spec.name == "fillet_edges_by_predicate"
    assert spec.level == "atomic"
    assert "target_edges" in spec.history_rules
    assert "fillet_face" in spec.produces_features
