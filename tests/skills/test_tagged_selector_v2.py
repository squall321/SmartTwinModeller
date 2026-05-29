"""Tagged selector v2 — cross-step tag propagation via SkillBase.apply().

[[lat.md/persistent-naming]] 의 v2: history_rules 기반 nearest-match propagation.
v1 의 한계 (bbox 위치만 보고 nearest) 가 형상 mutation 으로 어긋나는 문제를
SkillBase.apply 가 _apply 직후 자동으로 보정한다.
"""
from __future__ import annotations

import pytest

from phone_designer.skills._resolvers import resolve_faces
from phone_designer.skills._selectors import TaggedSelector
from phone_designer.skills._tag_propagation import _classify_rules
from phone_designer.skills._history import HistoryRule
from phone_designer.skills.compose.tag_face import TagFace, get_tags, set_tags
from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_curvature.fillet_predicate import (
    FilletEdgesByPredicate,
)
from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket
from phone_designer.skills.modify_pocket.extrude_through import ExtrudeThrough


def _shape(part):
    return part.wrapped if hasattr(part, "wrapped") else part


# ──────────────────────────────────────────────────────────────────────────────
# 1. MODIFIED_INHERIT — tag survives a pocket on the tagged face

def test_tag_survives_modified_inherit_pocket():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    tagged = TagFace().apply(box, {
        "selector": {"kind": "face_named", "name": "top"},
        "tag": "MY_TOP",
    }).body
    # depth 작게 (0.5mm) — top face 가 분할되지만 새 평면이 z≈9.5 에 생긴다.
    pocketed = ExtrudePocket().apply(tagged, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "circle", "diameter_mm": 5.0},
        "depth_mm": 0.5,
    }).body

    # tag 가 propagation 후에도 있어야 한다
    tags = get_tags(pocketed)
    assert "MY_TOP" in tags, f"tag dropped after pocket — got tags={list(tags)}"

    # TaggedSelector 로 resolve 가능 — top 의 분할된 ring (또는 원래 자리에 근접한
    # face) 가 잡힌다.
    faces = resolve_faces(
        _shape(pocketed), TaggedSelector(tag="MY_TOP"), body=pocketed,
    )
    assert len(faces) >= 1


# ──────────────────────────────────────────────────────────────────────────────
# 2. CONSUMED — tag is dropped when a skill 's history_rules consume the face
#
# 기존 skill 중 history_rules 가 *MODIFIED_INHERIT 도 SPLIT 도 없는* 100% CONSUMED-
# only 인 것이 없다. extrude_through 는 target_face=MODIFIED_INHERIT,
# consumed_volume=CONSUMED 라 — 분류상 "modified" 로 빠진다.
# 따라서 CONSUMED-drop 경로는 _classify_rules 단위로 확인 + helper level 에서
# clear_tags 호출 검증으로 cover. (skill registry 에 CONSUMED-only 가 추가되면
# end-to-end 케이스로 확장.)

def test_consumed_rules_classify_as_drop():
    rules = {"face": HistoryRule.CONSUMED}
    assert _classify_rules(rules) == "consumed"


def test_consumed_drops_tags_via_helper():
    from phone_designer.skills._tag_propagation import propagate_tags

    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    tagged = TagFace().apply(box, {
        "selector": {"kind": "face_named", "name": "top"},
        "tag": "ZAP",
    }).body
    # output body 따로 만든 (실제 skill 이 새 body 반환하는 상황 모사)
    out = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    propagate_tags(tagged, out, {"face": HistoryRule.CONSUMED})
    assert "ZAP" not in get_tags(out)


# ──────────────────────────────────────────────────────────────────────────────
# 3. propagation doesn 't break untagged workflow

def test_tag_propagation_does_not_break_untagged_workflow():
    """tag 없는 body 가 통과해도 crash 없음, 기존 동작 유지."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    assert get_tags(box) == {}

    # pocket — tag 없는 input
    pocketed = ExtrudePocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "circle", "diameter_mm": 5.0},
        "depth_mm": 1.0,
    }).body
    assert get_tags(pocketed) == {}

    # fillet — tag 없는 input
    filleted = FilletEdgesByPredicate().apply(box, {
        "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
        "radius_mm": 1.0,
    }).body
    assert get_tags(filleted) == {}


def test_propagate_tags_none_body_safe():
    from phone_designer.skills._tag_propagation import propagate_tags
    # input None — early return, no crash
    propagate_tags(None, None, {})
    propagate_tags(None, "anything", {"x": HistoryRule.MODIFIED_INHERIT})


# ──────────────────────────────────────────────────────────────────────────────
# 4. Tag survives a fillet (MODIFIED_INHERIT-like — chain through topology change)

def test_tag_survives_fillet_via_v2():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    tagged = TagFace().apply(box, {
        "selector": {"kind": "face_named", "name": "top"},
        "tag": "PRESERVE",
    }).body
    filleted = FilletEdgesByPredicate().apply(tagged, {
        "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
        "radius_mm": 1.0,
    }).body
    tags = get_tags(filleted)
    assert "PRESERVE" in tags
    # selector resolve — top 이 약간 줄었지만 여전히 매칭
    faces = resolve_faces(
        _shape(filleted), TaggedSelector(tag="PRESERVE"), body=filleted,
    )
    assert len(faces) >= 1


# ──────────────────────────────────────────────────────────────────────────────
# 5. Empty history_rules default = MODIFIED_INHERIT-for-all

def test_empty_history_rules_default_modified():
    assert _classify_rules({}) == "default"


def test_default_mode_preserves_tag_through_no_op_skill():
    """history_rules 가 비어 있는 skill (예: inspect_geometry) 통해도 tag 보존."""
    from phone_designer.skills.inspect.inspect_geometry import InspectGeometry

    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    tagged = TagFace().apply(box, {
        "selector": {"kind": "face_named", "name": "top"},
        "tag": "KEEP",
    }).body
    r = InspectGeometry().apply(tagged, {})
    assert "KEEP" in get_tags(r.body)


# ──────────────────────────────────────────────────────────────────────────────
# 6. propagation does not duplicate tags when _apply already attaches them

def test_propagation_does_not_overwrite_tags_set_by_apply():
    """tag_face 자체가 새 tag 를 result.body 에 직접 attach 한다. propagation
    이 그 tag 를 지우거나 중복하지 않아야."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = TagFace().apply(box, {
        "selector": {"kind": "face_named", "name": "top"},
        "tag": "FRESH",
    })
    tags = get_tags(r.body)
    assert "FRESH" in tags
    assert len(tags["FRESH"]) == 1
