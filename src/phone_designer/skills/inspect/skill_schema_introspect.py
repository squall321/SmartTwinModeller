"""skill_schema_introspect — atomic, read-only.

LLM 가 특정 skill 의 정확한 args 스키마 / post_conditions / DFM 제약을
질의할 때 쓰는 introspection helper. registry 에서 SkillSpec 을 꺼내
LLM-readable JSON 덩어리로 반환.

Body 는 통과. post_condition=body_present 이므로 호출자는 placeholder body 라도
한 개 넘긴다.

result.extras schema:
    {"schema": {
        "args": {...},                 # Pydantic JSON schema (model_json_schema())
        "post_conditions": [           # 선언적 사후조건 (kind+옵션)
            {"kind": str, "min_delta_mm3": float, "allow_no_change": bool},
            ...
        ],
        "manufacturing": {             # 공정별 DFM 룰
            "<process>": {...},
            ...
        },
        "summary": str,                # 한 문장 설명
        "name": str,
        "category": str,
        "level": str,
        "selector_kinds": [str],
        "preconditions": [str],        # named refs
        "produces_features": [str],
        "preserves": [str],
        "failure_modes": [str],
        "cost_hint": float,
        "expansion": [str] | None,     # macro only
     },
     "found": bool,                    # always True when 이 줄까지 도달
     "error": str | None}              # unknown skill 이면 메시지 + found=False
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import registry, skill, skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _serialize_post_conditions(specs: list[PostCondition]) -> list[dict]:
    out = []
    for pc in specs:
        out.append({
            "kind": pc.kind,
            "min_delta_mm3": pc.min_delta_mm3,
            "allow_no_change": pc.allow_no_change,
        })
    return out


@skill(
    name="skill_schema_introspect",
    category="inspect",
    level="atomic",
    summary="Look up a skill by name in the registry and return its Pydantic "
            "Args JSON schema, post_condition kinds, manufacturing constraints, "
            "summary, and surrounding metadata. Read-only introspection for LLM "
            "planning.",
    selector_kinds=[],
    history_rules={},
    produces_features=["skill_metadata"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class SkillSchemaIntrospect(SkillBase):
    class Args(BaseModel):
        skill_name: str = Field(min_length=1)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        try:
            spec = registry.get(args.skill_name)
        except KeyError as e:
            # 진단 정보 — 예외 던지지 않고 extras 로 보고. body 는 통과.
            extras = {
                "schema": None,
                "found": False,
                "error": str(e),
            }
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras=extras,
            )

        # Args 스키마
        try:
            args_schema = spec.args_model.model_json_schema()
        except Exception as e:
            args_schema = {"error": f"model_json_schema failed: {e}"}

        schema = {
            "name": spec.name,
            "category": spec.category,
            "level": spec.level,
            "summary": spec.summary,
            "args": args_schema,
            "post_conditions": _serialize_post_conditions(spec.post_conditions),
            "manufacturing": spec.manufacturing.to_dict(),
            "selector_kinds": list(spec.selector_kinds),
            "preconditions": list(spec.preconditions),
            "produces_features": list(spec.produces_features),
            "preserves": list(spec.preserves),
            "failure_modes": list(spec.failure_modes),
            "cost_hint": spec.cost_hint,
            "expansion": list(spec.expansion) if spec.expansion else None,
        }

        extras = {
            "schema": schema,
            "found": True,
            "error": None,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
