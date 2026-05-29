"""find_skill_by_intent + skill_schema_introspect 단위 테스트.

이 두 skill 은 OCCT 형상 연산 없이 registry 메타데이터만 만지는 LLM helper
들이다. 따라서 placeholder body (작은 box) 한 개만 만들어 통과시키면 된다.
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.find_skill_by_intent import FindSkillByIntent
from phone_designer.skills.inspect.skill_schema_introspect import (
    SkillSchemaIntrospect,
)


@pytest.fixture(scope="module")
def placeholder_body():
    """body_present post_condition 만족시킬 dummy body."""
    box = Box()
    return box.apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body


# ---------------------------------------------------------------------------
# find_skill_by_intent
# ---------------------------------------------------------------------------


def test_find_skill_by_intent_returns_suggestions(placeholder_body):
    inst = FindSkillByIntent()
    r = inst.apply(
        placeholder_body,
        {"natural_language_query": "drill a hole", "top_k": 5},
    )
    sugg = r.extras["suggestions"]
    assert isinstance(sugg, list)
    assert len(sugg) > 0
    assert len(sugg) <= 5
    # 각 항목 구조 확인
    for s in sugg:
        assert "skill_name" in s
        assert "summary" in s
        assert "score" in s
        assert "category" in s
        assert s["score"] >= 0.0


def test_find_skill_by_intent_hole_query_finds_hole_skill(placeholder_body):
    """'drill a hole' 같은 쿼리는 hole skill (또는 *_hole) 을 상위에서 찾아야 함."""
    inst = FindSkillByIntent()
    r = inst.apply(
        placeholder_body,
        {"natural_language_query": "drill hole through body", "top_k": 10},
    )
    names = [s["skill_name"] for s in r.extras["suggestions"]]
    # hole 이라는 단어가 등록된 skill name 이거나 hole 관련 skill 이 top 10 안에 있어야.
    assert any("hole" in n for n in names), f"hole 관련 skill 없음: {names}"


def test_find_skill_by_intent_top_k_respected(placeholder_body):
    inst = FindSkillByIntent()
    r = inst.apply(
        placeholder_body,
        {"natural_language_query": "fillet edge corner round", "top_k": 3},
    )
    assert len(r.extras["suggestions"]) <= 3


def test_find_skill_by_intent_no_match_returns_empty(placeholder_body):
    """완전 무관한 쿼리는 빈 리스트 또는 score=0 suggestions."""
    inst = FindSkillByIntent()
    r = inst.apply(
        placeholder_body,
        {"natural_language_query": "zzqqxxnonexistenttoken99", "top_k": 5},
    )
    # 모두 score 0 이라 빈 리스트여야 함 (score>0 필터 적용).
    assert r.extras["suggestions"] == []
    assert r.extras["total_searched"] > 0


def test_find_skill_by_intent_metadata():
    spec = FindSkillByIntent.spec
    assert spec.name == "find_skill_by_intent"
    assert spec.category == "inspect"
    assert spec.level == "atomic"


def test_find_skill_by_intent_query_tokens_extracted(placeholder_body):
    inst = FindSkillByIntent()
    r = inst.apply(
        placeholder_body,
        {"natural_language_query": "Drill a Hole through the body!"},
    )
    toks = r.extras["query_tokens"]
    # stopword "a", "the" 제거되고 소문자화.
    assert "drill" in toks
    assert "hole" in toks
    assert "a" not in toks
    assert "the" not in toks


def test_find_skill_by_intent_body_passes_through(placeholder_body):
    inst = FindSkillByIntent()
    r = inst.apply(placeholder_body, {"natural_language_query": "hole"})
    assert r.body is placeholder_body or r.body is not None


def test_find_skill_by_intent_default_top_k_five(placeholder_body):
    inst = FindSkillByIntent()
    # top_k 생략 → 기본 5
    r = inst.apply(
        placeholder_body,
        {"natural_language_query": "create box rounded slab body"},
    )
    assert len(r.extras["suggestions"]) <= 5


# ---------------------------------------------------------------------------
# skill_schema_introspect
# ---------------------------------------------------------------------------


def test_skill_schema_introspect_known_skill(placeholder_body):
    inst = SkillSchemaIntrospect()
    r = inst.apply(placeholder_body, {"skill_name": "hole"})
    assert r.extras["found"] is True
    schema = r.extras["schema"]
    assert schema["name"] == "hole"
    assert "args" in schema
    assert "post_conditions" in schema
    assert "manufacturing" in schema
    assert "summary" in schema
    # hole 은 manufacturing 에 cnc/die_cast/injection_mold 룰 있음
    assert "cnc_3axis" in schema["manufacturing"]


def test_skill_schema_introspect_args_is_json_schema(placeholder_body):
    inst = SkillSchemaIntrospect()
    r = inst.apply(placeholder_body, {"skill_name": "hole"})
    args_schema = r.extras["schema"]["args"]
    # Pydantic model_json_schema 출력은 properties 갖는 object
    assert args_schema["type"] == "object"
    assert "properties" in args_schema
    # hole.Args 는 position, diameter_mm, depth_mm, direction
    assert "diameter_mm" in args_schema["properties"]
    assert "position" in args_schema["properties"]


def test_skill_schema_introspect_unknown_skill_no_raise(placeholder_body):
    """unknown skill 이름은 예외 던지지 않고 found=False + error 메시지."""
    inst = SkillSchemaIntrospect()
    r = inst.apply(
        placeholder_body,
        {"skill_name": "definitely_does_not_exist_skill_xyz"},
    )
    assert r.extras["found"] is False
    assert r.extras["schema"] is None
    assert r.extras["error"] is not None
    assert "definitely_does_not_exist_skill_xyz" in r.extras["error"]


def test_skill_schema_introspect_post_conditions_serialized(placeholder_body):
    """hole 은 post_conditions=[PostCondition(kind='volume_decreased')]."""
    inst = SkillSchemaIntrospect()
    r = inst.apply(placeholder_body, {"skill_name": "hole"})
    pcs = r.extras["schema"]["post_conditions"]
    assert isinstance(pcs, list)
    assert len(pcs) >= 1
    kinds = {pc["kind"] for pc in pcs}
    assert "volume_decreased" in kinds


def test_skill_schema_introspect_self_lookup(placeholder_body):
    """자기 자신 조회 가능 — meta-introspection."""
    inst = SkillSchemaIntrospect()
    r = inst.apply(placeholder_body, {"skill_name": "skill_schema_introspect"})
    assert r.extras["found"] is True
    assert r.extras["schema"]["name"] == "skill_schema_introspect"
    assert r.extras["schema"]["category"] == "inspect"


def test_skill_schema_introspect_finds_find_skill_by_intent(placeholder_body):
    """다른 신규 skill 도 조회 가능."""
    inst = SkillSchemaIntrospect()
    r = inst.apply(placeholder_body, {"skill_name": "find_skill_by_intent"})
    assert r.extras["found"] is True
    schema = r.extras["schema"]
    assert schema["name"] == "find_skill_by_intent"
    # Args = natural_language_query + top_k
    props = schema["args"]["properties"]
    assert "natural_language_query" in props
    assert "top_k" in props


def test_skill_schema_introspect_metadata():
    spec = SkillSchemaIntrospect.spec
    assert spec.name == "skill_schema_introspect"
    assert spec.category == "inspect"
    assert spec.level == "atomic"


def test_skill_schema_introspect_body_passes_through(placeholder_body):
    inst = SkillSchemaIntrospect()
    r = inst.apply(placeholder_body, {"skill_name": "hole"})
    # body 가 None 이 아니어야 함 (body_present post_condition)
    assert r.body is not None


# ---------------------------------------------------------------------------
# integration: find → introspect
# ---------------------------------------------------------------------------


def test_find_then_introspect_round_trip(placeholder_body):
    """find_skill_by_intent 결과의 skill_name 으로 곧장 introspect 가능."""
    finder = FindSkillByIntent()
    r1 = finder.apply(
        placeholder_body,
        {"natural_language_query": "drill hole", "top_k": 3},
    )
    assert r1.extras["suggestions"]
    first_name = r1.extras["suggestions"][0]["skill_name"]

    introspector = SkillSchemaIntrospect()
    r2 = introspector.apply(placeholder_body, {"skill_name": first_name})
    assert r2.extras["found"] is True
    assert r2.extras["schema"]["name"] == first_name
