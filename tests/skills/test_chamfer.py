"""chamfer_edges_by_predicate 단위 테스트."""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_curvature.chamfer_predicate import ChamferEdgesByPredicate


def _count_faces(body) -> int:
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    count = 0
    it = TopExp_Explorer(body.wrapped, TopAbs_FACE)
    while it.More():
        count += 1
        it.Next()
    return count


def test_chamfer_z_edges_adds_faces():
    box_inst = Box()
    box_result = box_inst.apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    initial = _count_faces(box_result.body)

    cham = ChamferEdgesByPredicate()
    result = cham.apply(
        box_result.body,
        {
            "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
            "width_mm": 1.0,
        },
    )
    final = _count_faces(result.body)
    assert final > initial


def test_chamfer_freeze_4_edges():
    box_inst = Box()
    box_result = box_inst.apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    cham = ChamferEdgesByPredicate()
    r = cham.apply(
        box_result.body,
        {"selector": {"kind": "axis_aligned_edges", "axis": "Z"}, "width_mm": 0.5},
    )
    assert r.selector_freeze is not None
    assert r.selector_freeze.matched_count == 4


def test_chamfer_spec_metadata():
    spec = ChamferEdgesByPredicate.spec
    assert spec.name == "chamfer_edges_by_predicate"
    assert spec.level == "atomic"
    assert "chamfer_face" in spec.produces_features


def test_chamfer_rejects_out_of_range_width():
    cham = ChamferEdgesByPredicate()
    with pytest.raises(Exception):
        cham.apply(
            None,
            {
                "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
                "width_mm": 100.0,   # max=20 위반
            },
        )
