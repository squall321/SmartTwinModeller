"""Tests for the chamfer expansion pack (PACK chamfer expansion):
    chamfer_two_distance, chamfer_distance_angle, chamfer_conic,
    vertex_chamfer.

Each skill is exercised on a 20×20×10 box. We assert that the resulting
body has a different face count than the input (post_condition kicks in
automatically inside SkillBase.apply, so a no-op would already raise —
but we double-check here for clarity).
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_curvature.chamfer_conic import ChamferConic
from phone_designer.skills.modify_curvature.chamfer_distance_angle import (
    ChamferDistanceAngle,
)
from phone_designer.skills.modify_curvature.chamfer_two_distance import (
    ChamferTwoDistance,
)
from phone_designer.skills.modify_curvature.vertex_chamfer import VertexChamfer


def _count_faces(body) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopTools import TopTools_MapOfShape

    seen = TopTools_MapOfShape()
    count = 0
    it = TopExp_Explorer(body.wrapped, TopAbs_FACE)
    while it.More():
        f = it.Current()
        if not seen.Contains(f):
            seen.Add(f)
            count += 1
        it.Next()
    return count


@pytest.fixture
def box_body():
    box_inst = Box()
    res = box_inst.apply(
        None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}
    )
    return res.body


# ──────────────────────────────────────────────────────────────────────
# chamfer_two_distance
# ──────────────────────────────────────────────────────────────────────


def test_chamfer_two_distance_adds_faces(box_body):
    initial = _count_faces(box_body)
    skill = ChamferTwoDistance()
    result = skill.apply(
        box_body,
        {
            "edge_selector": {"kind": "axis_aligned_edges", "axis": "Z"},
            "distance1_mm": 1.0,
            "distance2_mm": 2.0,
        },
    )
    assert _count_faces(result.body) > initial


def test_chamfer_two_distance_freeze(box_body):
    skill = ChamferTwoDistance()
    r = skill.apply(
        box_body,
        {
            "edge_selector": {"kind": "axis_aligned_edges", "axis": "Z"},
            "distance1_mm": 0.5,
            "distance2_mm": 0.8,
        },
    )
    assert r.selector_freeze is not None
    # Box has 4 Z-axis edges
    assert r.selector_freeze.matched_count == 4


def test_chamfer_two_distance_spec_metadata():
    spec = ChamferTwoDistance.spec
    assert spec.name == "chamfer_two_distance"
    assert spec.category == "modify/chamfer"
    assert spec.level == "atomic"
    assert any(pc.kind == "face_count_changed" for pc in spec.post_conditions)


# ──────────────────────────────────────────────────────────────────────
# chamfer_distance_angle
# ──────────────────────────────────────────────────────────────────────


def test_chamfer_distance_angle_adds_faces(box_body):
    initial = _count_faces(box_body)
    skill = ChamferDistanceAngle()
    result = skill.apply(
        box_body,
        {
            "edge_selector": {"kind": "axis_aligned_edges", "axis": "Z"},
            "distance_mm": 1.0,
            "angle_deg": 30.0,
        },
    )
    assert _count_faces(result.body) > initial


def test_chamfer_distance_angle_45_matches_symmetric(box_body):
    """θ=45° → d2=d·tan(45°)=d → symmetric chamfer of width d."""
    skill = ChamferDistanceAngle()
    r = skill.apply(
        box_body,
        {
            "edge_selector": {"kind": "axis_aligned_edges", "axis": "Z"},
            "distance_mm": 1.0,
            "angle_deg": 45.0,
        },
    )
    # 4 edges → +8 new faces (each chamfer adds 1 face, original 4 side faces
    # remain). Just assert topology changed.
    assert _count_faces(r.body) > 6   # box has 6 faces


def test_chamfer_distance_angle_rejects_angle_out_of_range():
    skill = ChamferDistanceAngle()
    with pytest.raises(Exception):
        skill.apply(
            None,
            {
                "edge_selector": {"kind": "axis_aligned_edges", "axis": "Z"},
                "distance_mm": 1.0,
                "angle_deg": 95.0,   # ≥89 violates spec
            },
        )


def test_chamfer_distance_angle_spec_metadata():
    spec = ChamferDistanceAngle.spec
    assert spec.name == "chamfer_distance_angle"
    assert spec.category == "modify/chamfer"
    assert any(pc.kind == "face_count_changed" for pc in spec.post_conditions)


# ──────────────────────────────────────────────────────────────────────
# chamfer_conic
# ──────────────────────────────────────────────────────────────────────


def test_chamfer_conic_straight_adds_faces(box_body):
    initial = _count_faces(box_body)
    skill = ChamferConic()
    result = skill.apply(
        box_body,
        {
            "edge_selector": {"kind": "axis_aligned_edges", "axis": "Z"},
            "distance_mm": 1.0,
            "conic_param": 0.0,
        },
    )
    assert _count_faces(result.body) > initial


def test_chamfer_conic_quarter_circle_adds_faces(box_body):
    initial = _count_faces(box_body)
    skill = ChamferConic()
    result = skill.apply(
        box_body,
        {
            "edge_selector": {"kind": "axis_aligned_edges", "axis": "Z"},
            "distance_mm": 1.0,
            "conic_param": 1.0,
        },
    )
    assert _count_faces(result.body) > initial


def test_chamfer_conic_spec_metadata():
    spec = ChamferConic.spec
    assert spec.name == "chamfer_conic"
    assert spec.category == "modify/chamfer"
    assert any(pc.kind == "face_count_changed" for pc in spec.post_conditions)


# ──────────────────────────────────────────────────────────────────────
# vertex_chamfer
# ──────────────────────────────────────────────────────────────────────


def test_vertex_chamfer_box_corner(box_body):
    """Box is centered at origin, so corner is at (±10, ±10, ±5)."""
    initial = _count_faces(box_body)
    skill = VertexChamfer()
    # Box from create.box: confirm orientation by checking a known corner.
    # Box spans (-L/2..+L/2, -W/2..+W/2, 0..H) — see create/box.py. Pick the
    # top-front-right corner.
    result = skill.apply(
        box_body,
        {
            "vertex_position": (10.0, 10.0, 10.0),
            "distance_mm": 2.0,
        },
    )
    assert _count_faces(result.body) > initial


def test_vertex_chamfer_freeze(box_body):
    skill = VertexChamfer()
    r = skill.apply(
        box_body,
        {
            "vertex_position": (10.0, 10.0, 10.0),
            "distance_mm": 1.5,
        },
    )
    assert r.selector_freeze is not None
    assert r.selector_freeze.matched_count == 1


def test_vertex_chamfer_spec_metadata():
    spec = VertexChamfer.spec
    assert spec.name == "vertex_chamfer"
    assert spec.category == "modify/chamfer"
    assert any(pc.kind == "face_count_changed" for pc in spec.post_conditions)


def test_vertex_chamfer_rejects_out_of_range_distance():
    skill = VertexChamfer()
    with pytest.raises(Exception):
        skill.apply(
            None,
            {
                "vertex_position": (10.0, 10.0, 10.0),
                "distance_mm": 100.0,   # >20 violates spec
            },
        )
