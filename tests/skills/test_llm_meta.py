"""selector_robustness_score + tag_inventory 단위 테스트.

이 둘은 read-only 메타-introspection skill. 작은 box body 위에 tag 를 부착하고
얼마나 잘 잡히는지 (robustness) + 어떤 tag 가 어떤 face 를 가리키는지 (inventory)
를 확인.
"""
from __future__ import annotations

import pytest

from phone_designer.skills.compose.tag_face import TagFace
from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.selector_robustness_score import (
    SelectorRobustnessScore,
)
from phone_designer.skills.inspect.tag_inventory import TagInventory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def plain_box():
    """tag 없는 plain box."""
    return Box().apply(
        None, {"length_mm": 20, "width_mm": 20, "height_mm": 10},
    ).body


@pytest.fixture
def tagged_box():
    """top face 에 'TOP' tag, bottom face 에 'BOTTOM' tag 부착."""
    body = Box().apply(
        None, {"length_mm": 20, "width_mm": 20, "height_mm": 10},
    ).body
    tagger = TagFace()
    r1 = tagger.apply(
        body,
        {"selector": {"kind": "face_named", "name": "top"}, "tag": "TOP"},
    )
    r2 = tagger.apply(
        r1.body,
        {"selector": {"kind": "face_named", "name": "bottom"}, "tag": "BOTTOM"},
    )
    return r2.body


# ---------------------------------------------------------------------------
# selector_robustness_score
# ---------------------------------------------------------------------------


def test_robustness_score_metadata():
    spec = SelectorRobustnessScore.spec
    assert spec.name == "selector_robustness_score"
    assert spec.category == "inspect"
    assert spec.level == "atomic"


def test_robustness_score_extras_shape(plain_box):
    inst = SelectorRobustnessScore()
    r = inst.apply(
        plain_box,
        {"selector_dict": {"kind": "face_named", "name": "top"}},
    )
    assert "selector_score" in r.extras
    s = r.extras["selector_score"]
    for k in ("score", "matched", "alternatives_suggested"):
        assert k in s
    assert isinstance(s["alternatives_suggested"], list)
    assert 0.0 <= s["score"] <= 1.0


def test_robustness_score_single_match_is_high(plain_box):
    """face_named 'top' 은 정확히 1 face 매칭 → score 가 높아야 (≥ 0.9)."""
    inst = SelectorRobustnessScore()
    r = inst.apply(
        plain_box,
        {"selector_dict": {"kind": "face_named", "name": "top"}},
    )
    s = r.extras["selector_score"]
    assert s["matched"] == 1
    assert s["score"] >= 0.9


def test_robustness_score_zero_match_is_zero(plain_box):
    """존재하지 않는 tag → 0 매칭 → score 0.0."""
    inst = SelectorRobustnessScore()
    r = inst.apply(
        plain_box,
        {"selector_dict": {"kind": "tagged", "tag": "NONEXISTENT_TAG_XYZ"}},
    )
    s = r.extras["selector_score"]
    assert s["matched"] == 0
    assert s["score"] == 0.0


def test_robustness_score_many_matches_is_penalized(plain_box):
    """faces_by_area min=0 은 모든 face (box=6 개) → ambiguous → score 낮음."""
    inst = SelectorRobustnessScore()
    r = inst.apply(
        plain_box,
        {"selector_dict": {"kind": "faces_by_area", "min": 0.0}},
    )
    s = r.extras["selector_score"]
    # box 의 6 face 가 모두 매칭. score 는 0.5 미만이어야 함.
    assert s["matched"] >= 6
    assert s["score"] < 0.5


def test_robustness_score_tagged_beats_geometric(tagged_box):
    """tagged selector (stability_rank=5) 는 같은 1 매칭이라도 weight=1.0 →
    score 가 face_named (rank=4, weight=0.95) 보다 약간 높거나 같음."""
    inst = SelectorRobustnessScore()
    r_tag = inst.apply(
        tagged_box,
        {"selector_dict": {"kind": "tagged", "tag": "TOP"}},
    )
    r_named = inst.apply(
        tagged_box,
        {"selector_dict": {"kind": "face_named", "name": "top"}},
    )
    s_tag = r_tag.extras["selector_score"]
    s_named = r_named.extras["selector_score"]
    # 둘 다 1 매칭이라고 가정
    assert s_tag["matched"] == 1
    assert s_named["matched"] == 1
    # tagged weight (1.0) ≥ face_named weight (0.95)
    assert s_tag["score"] >= s_named["score"]


def test_robustness_score_invalid_selector_dict_no_raise(plain_box):
    """unknown kind 는 예외 던지지 말고 error 메시지로 진단."""
    inst = SelectorRobustnessScore()
    r = inst.apply(
        plain_box,
        {"selector_dict": {"kind": "definitely_unknown_kind_xyz"}},
    )
    s = r.extras["selector_score"]
    assert s["score"] == 0.0
    assert s["error"] is not None
    assert "definitely_unknown_kind_xyz" in s["error"]


def test_robustness_score_body_unchanged(plain_box):
    """body geometry 는 변하면 안 됨 (post body_present)."""
    inst = SelectorRobustnessScore()
    r = inst.apply(
        plain_box,
        {"selector_dict": {"kind": "face_named", "name": "top"}},
    )
    assert r.body is not None


def test_robustness_score_reports_kind_and_rank(plain_box):
    inst = SelectorRobustnessScore()
    r = inst.apply(
        plain_box,
        {"selector_dict": {"kind": "face_named", "name": "top"}},
    )
    s = r.extras["selector_score"]
    assert s["kind"] == "face_named"
    assert s["stability_rank"] == 4


# ---------------------------------------------------------------------------
# tag_inventory
# ---------------------------------------------------------------------------


def test_tag_inventory_metadata():
    spec = TagInventory.spec
    assert spec.name == "tag_inventory"
    assert spec.category == "inspect"
    assert spec.level == "atomic"


def test_tag_inventory_empty_when_no_tags(plain_box):
    """tag 없는 body → tags 빈 리스트."""
    inst = TagInventory()
    r = inst.apply(plain_box, {})
    assert "tags" in r.extras
    assert r.extras["tags"] == []


def test_tag_inventory_lists_all_tags(tagged_box):
    """2 개 tag 부착된 box → 2 개 entry."""
    inst = TagInventory()
    r = inst.apply(tagged_box, {})
    tags = r.extras["tags"]
    names = {t["tag"] for t in tags}
    assert names == {"TOP", "BOTTOM"}


def test_tag_inventory_entry_shape(tagged_box):
    inst = TagInventory()
    r = inst.apply(tagged_box, {})
    tags = r.extras["tags"]
    assert len(tags) >= 1
    e = tags[0]
    for k in ("tag", "set_by_skill", "history", "current_resolution"):
        assert k in e
    cr = e["current_resolution"]
    assert "matched" in cr
    assert "face_centers" in cr
    assert isinstance(cr["face_centers"], list)


def test_tag_inventory_history_has_bbox_center(tagged_box):
    """history 는 tag 부착 당시 EntityRef metadata."""
    inst = TagInventory()
    r = inst.apply(tagged_box, {})
    tags = r.extras["tags"]
    top = next(t for t in tags if t["tag"] == "TOP")
    assert len(top["history"]) >= 1
    h0 = top["history"][0]
    assert "bbox_center" in h0
    assert "kind" in h0
    assert "measure" in h0
    # top face 의 z center ≈ 10 (box height)
    assert h0["bbox_center"][2] == pytest.approx(10.0, abs=0.1)


def test_tag_inventory_current_resolution_matches_top(tagged_box):
    """TOP tag 는 지금 body 에서도 1 face 잡혀야 (mutation 없으므로)."""
    inst = TagInventory()
    r = inst.apply(tagged_box, {})
    tags = r.extras["tags"]
    top = next(t for t in tags if t["tag"] == "TOP")
    assert top["current_resolution"]["matched"] == 1
    centers = top["current_resolution"]["face_centers"]
    assert len(centers) == 1
    # z ≈ 10
    assert centers[0][2] == pytest.approx(10.0, abs=0.1)


def test_tag_inventory_set_by_skill_defaults(tagged_box):
    """set_by_skill 트래킹은 아직 인프라가 안 함 → 'tag_face' default."""
    inst = TagInventory()
    r = inst.apply(tagged_box, {})
    for t in r.extras["tags"]:
        assert t["set_by_skill"] in ("tag_face", "unknown")


def test_tag_inventory_body_unchanged(tagged_box):
    inst = TagInventory()
    r = inst.apply(tagged_box, {})
    assert r.body is not None


def test_tag_inventory_surface_types_planar(tagged_box):
    """box 의 top/bottom face 는 planar."""
    inst = TagInventory()
    r = inst.apply(tagged_box, {})
    for t in r.extras["tags"]:
        st = t["current_resolution"]["surface_types"]
        if st:
            assert st[0] == "plane"
