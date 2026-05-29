"""tag_face + tagged selector 단위 테스트 (Phase 4 v1)."""
from __future__ import annotations

import pytest

from phone_designer.skills.compose.tag_face import TagFace, get_tags
from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_curvature.fillet_predicate import FilletEdgesByPredicate
from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket


def _shape(part):
    return part.wrapped if hasattr(part, "wrapped") else part


def test_tag_face_attaches_pd_tags():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = TagFace().apply(box, {
        "selector": {"kind": "face_named", "name": "top"},
        "tag": "MY_TOP",
    })
    tags = get_tags(r.body)
    assert "MY_TOP" in tags
    assert len(tags["MY_TOP"]) == 1
    ref = tags["MY_TOP"][0]
    assert ref.kind == "face"
    # top face z=10 의 중심
    assert abs(ref.bbox_center[2] - 10) < 0.5


def test_tag_face_no_match_raises():
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    with pytest.raises(RuntimeError, match="0 faces"):
        TagFace().apply(box, {
            "selector": {"kind": "face_named", "name": "nonexistent"},
            "tag": "X",
        })


def test_tag_chain_propagates_through_subsequent_skill():
    """tag_face 후 다른 skill 호출해도 tag 가 propagate."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    tagged = TagFace().apply(box, {
        "selector": {"kind": "face_named", "name": "top"},
        "tag": "TOP_TAG",
    }).body

    # 후속: top face 에 pocket
    pocketed = ExtrudePocket().apply(tagged, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "circle", "diameter_mm": 5},
        "depth_mm": 1.0,
    }).body
    # tag 보존 — SkillBase.apply 가 chain propagate
    tags_after = get_tags(pocketed)
    assert "TOP_TAG" in tags_after


def test_tag_chain_through_fillet():
    """fillet 같이 형상 mutate 하는 skill 후에도 tag 보존."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    tagged = TagFace().apply(box, {
        "selector": {"kind": "face_named", "name": "top"},
        "tag": "TOP",
    }).body

    filleted = FilletEdgesByPredicate().apply(tagged, {
        "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
        "radius_mm": 1.0,
    }).body
    tags = get_tags(filleted)
    assert "TOP" in tags


def test_tagged_selector_resolves_face():
    """tagged selector 가 tag 의 face 매칭."""
    from phone_designer.skills._resolvers import resolve_faces
    from phone_designer.skills._selectors import TaggedSelector

    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    tagged = TagFace().apply(box, {
        "selector": {"kind": "face_named", "name": "top"},
        "tag": "MARKED",
    }).body

    faces = resolve_faces(_shape(tagged), TaggedSelector(tag="MARKED"), body=tagged)
    assert len(faces) == 1


def test_tagged_selector_without_body_returns_empty():
    """body 미전달 시 tagged selector 빈 list."""
    from phone_designer.skills._resolvers import resolve_faces
    from phone_designer.skills._selectors import TaggedSelector

    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    # body 안 넘김
    faces = resolve_faces(_shape(box), TaggedSelector(tag="ANY"))
    assert faces == []


def test_tag_face_spec_metadata():
    spec = TagFace.spec
    assert spec.name == "tag_face"
    assert spec.level == "atomic"
    assert "tag_attribute" in spec.produces_features


def test_multiple_tags_coexist():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    a = TagFace().apply(box, {
        "selector": {"kind": "face_named", "name": "top"}, "tag": "T1",
    }).body
    b = TagFace().apply(a, {
        "selector": {"kind": "face_named", "name": "bottom"}, "tag": "T2",
    }).body
    tags = get_tags(b)
    assert "T1" in tags and "T2" in tags
