"""inspect_geometry — read-only skill 단위 테스트."""
from __future__ import annotations

import pytest

from phone_designer.skills.compose.tag_face import TagFace, get_tags
from phone_designer.skills.create.box import Box
from phone_designer.skills.create.disc_with_dome import DiscWithDome
from phone_designer.skills.inspect.inspect_geometry import InspectGeometry


def test_inspect_box_basic_report():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 30, "height_mm": 10}).body
    r = InspectGeometry().apply(box, {})
    rep = r.extras["inspection_report"]

    # 기본 형태
    assert "volume_mm3" in rep
    assert "bbox" in rep
    assert "face_count" in rep
    assert "edges" in rep

    # box volume = 20*30*10 = 6000
    assert abs(rep["volume_mm3"] - 6000.0) < 1.0
    # box 6 faces
    assert rep["face_count"] == 6
    # 12 edges
    assert rep["edge_count"] == 12

    # bbox size 일치
    sz = rep["bbox"]["size"]
    assert sorted([round(s, 1) for s in sz]) == [10.0, 20.0, 30.0]


def test_inspect_box_faces_have_surface_type_and_normal():
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = InspectGeometry().apply(box, {})
    rep = r.extras["inspection_report"]

    assert len(rep["faces"]) == 6
    for f in rep["faces"]:
        assert f["surface_type"] == "plane"
        assert f["normal_at_center"] is not None
        # outward 가 box 의 모든 face 에 True
        assert f["is_outward"] is True


def test_inspect_disc_has_cylinder_surface_type():
    disc = DiscWithDome().apply(None, {
        "diameter_mm": 30, "height_mm": 5,
        "dome_rise_mm": 0.0, "corner_r_mm": 0.0,
    }).body
    r = InspectGeometry().apply(disc, {})
    rep = r.extras["inspection_report"]

    surfaces = {f["surface_type"] for f in rep["faces"]}
    assert "plane" in surfaces
    assert "cylinder" in surfaces


def test_inspect_bbox_only_skips_face_list():
    """bbox_only=True now counts faces/edges (cheap) but skips per-face
    attributes (expensive). This was the shell-aware fix — earlier code
    returned 0/0 even on a closed solid, which masked real bugs on
    mesh→BREP shells."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = InspectGeometry().apply(box, {"bbox_only": True})
    rep = r.extras["inspection_report"]

    assert rep["face_count"] == 6
    assert rep["edge_count"] == 12
    assert rep["faces"] == []  # per-face attribute list still skipped
    assert "bbox" in rep
    assert "volume_mm3" in rep
    assert rep["body_kind"] == "solid"


def test_inspect_max_entities_caps_face_list():
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = InspectGeometry().apply(box, {"max_entities": 3})
    rep = r.extras["inspection_report"]

    # face_count 는 실제값 (6) 이지만 faces 배열은 3 개로 capped
    assert rep["face_count"] == 6
    assert len(rep["faces"]) == 3


def test_inspect_include_edges_returns_edge_entries():
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = InspectGeometry().apply(box, {"include_edges": True})
    rep = r.extras["inspection_report"]

    assert len(rep["edges"]) == 12
    for e in rep["edges"]:
        assert "length_mm" in e
        # box edge length = 10
        assert abs(e["length_mm"] - 10.0) < 0.01


def test_inspect_body_unchanged():
    """skill 은 body 를 mutate 하지 않는다 — result.body is body."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = InspectGeometry().apply(box, {})
    assert r.body is box


def test_inspect_preserves_tags():
    """inspect 후에도 input tag 가 유지된다 (read-only skill)."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    tagged = TagFace().apply(box, {
        "selector": {"kind": "face_named", "name": "top"},
        "tag": "TOP",
    }).body
    r = InspectGeometry().apply(tagged, {})
    tags = get_tags(r.body)
    assert "TOP" in tags


def test_inspect_spec_metadata():
    spec = InspectGeometry.spec
    assert spec.name == "inspect_geometry"
    assert spec.category == "inspect"
    assert spec.level == "atomic"
    assert "inspection_report" in spec.produces_features


def test_inspect_extras_default_is_empty_dict_for_other_skills():
    """non-inspect skill 은 extras 가 빈 dict (backwards-compatible)."""
    r = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10})
    assert r.extras == {}
