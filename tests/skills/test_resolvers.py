"""_resolvers 의 face_named heuristic 및 다른 selector resolution 검증."""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.disc_with_dome import DiscWithDome
from phone_designer.skills._resolvers import resolve_edges, resolve_faces
from phone_designer.skills._selectors import (
    AxisAlignedEdgesSelector,
    EdgesByLengthSelector,
    EdgesByPositionSelector,
    FaceNamedSelector,
    FacesByNormalSelector,
)


def _shape_of(part):
    return part.wrapped if hasattr(part, "wrapped") else part


def test_axis_aligned_z_on_box_returns_4_edges():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    edges = resolve_edges(_shape_of(box), AxisAlignedEdgesSelector(axis="Z"))
    assert len(edges) == 4


def test_axis_aligned_x_on_box_returns_4_edges():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    edges = resolve_edges(_shape_of(box), AxisAlignedEdgesSelector(axis="X"))
    assert len(edges) == 4


def test_edges_by_length_filters():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    # 길이 10인 edge (Z-axis) 만
    edges = resolve_edges(_shape_of(box), EdgesByLengthSelector(min=9.5, max=10.5))
    assert len(edges) == 4


def test_edges_by_position_filters():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    # top z=10 근처의 edge 만 (top face boundary 4개)
    edges = resolve_edges(_shape_of(box), EdgesByPositionSelector(
        bbox={"min": (-100, -100, 9.5), "max": (100, 100, 10.5)},
    ))
    assert len(edges) == 4


def test_face_named_top_on_flat_box():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    faces = resolve_faces(_shape_of(box), FaceNamedSelector(name="top"))
    assert len(faces) == 1


def test_face_named_top_on_flat_disc():
    """dome_rise=0 → flat top → face_named=top 매칭."""
    disc = DiscWithDome().apply(None, {
        "diameter_mm": 40, "height_mm": 10,
        "dome_rise_mm": 0, "corner_r_mm": 2,
    }).body
    faces = resolve_faces(_shape_of(disc), FaceNamedSelector(name="top"))
    assert len(faces) == 1


def test_face_named_top_on_domed_disc_fallbacks_to_curved():
    """dome_rise > 0 → planar top 없음 → fallback 으로 z 최대 face 매칭."""
    disc = DiscWithDome().apply(None, {
        "diameter_mm": 40, "height_mm": 10,
        "dome_rise_mm": 1.5, "corner_r_mm": 0,
    }).body
    faces = resolve_faces(_shape_of(disc), FaceNamedSelector(name="top"))
    # 곡면이라도 fallback 으로 매칭 (개수 ≥ 1)
    assert len(faces) >= 1


def test_face_named_bottom_on_box():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    faces = resolve_faces(_shape_of(box), FaceNamedSelector(name="bottom"))
    assert len(faces) == 1


def test_faces_by_normal_on_box():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    # +X 방향 face — build123d 0.10 의 Box 는 face split 으로 여러 face 일 수 있음.
    # +X normal 의 face 가 최소 1개 매칭되면 OK.
    faces = resolve_faces(_shape_of(box), FacesByNormalSelector(
        direction=(1.0, 0.0, 0.0), tol_deg=5.0,
    ))
    assert len(faces) >= 1
