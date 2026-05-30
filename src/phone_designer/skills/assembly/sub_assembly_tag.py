"""sub_assembly_tag — atomic assembly metadata.

여러 component 를 하나의 sub-assembly 그룹으로 marking 한다. 형상은 변하지 않고
``body._pd_sub_assemblies`` 와 ``result.extras["sub_assemblies"]`` 에 그룹 멤버십을
누적한다. BOM 출력, exploded-view 계층화, planner-사이드 grouping 에 활용.

Behavior
--------
- body 가 None 이면 RuntimeError.
- component_names 의 각 이름이 assembly 안에 존재하지 않으면 RuntimeError.
- 같은 sub_assembly_name 으로 재호출하면 멤버 list 는 union (중복 제거).
- 다른 sub-assembly 의 멤버를 추가하는 것은 막지 않는다 — component 가 동시에
  여러 sub-assembly 에 속할 수 있다 (예: 화면 그룹 + 외관 그룹).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.assembly._compound import (
    get_component_names,
    iter_compound_components,
)


def _get_sub_assemblies(body: Any) -> dict[str, list[str]]:
    """body 의 sub-assembly 맵 (있으면) — 없으면 빈 dict."""
    if body is None:
        return {}
    existing = getattr(body, "_pd_sub_assemblies", None)
    if not existing:
        return {}
    # defensive deep-ish copy (list-of-str values).
    return {k: list(v) for k, v in existing.items()}


@skill(
    name="sub_assembly_tag",
    category="assembly",
    level="atomic",
    summary="Tag a set of components as belonging to a named sub-assembly. "
            "Body geometry is unchanged; membership is recorded in "
            "result.extras['sub_assemblies'].",
    selector_kinds=[],
    history_rules={},
    produces_features=["sub_assembly_group"],
    preserves=["assembly_topology", "body_topology"],
    manufacturing={},
    failure_modes=["fm.component_not_found", "fm.empty_component_list"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class SubAssemblyTag(SkillBase):
    class Args(BaseModel):
        component_names: list[str] = Field(
            min_length=1,
            description="이 sub-assembly 에 묶을 component 이름들.",
        )
        sub_assembly_name: str = Field(
            min_length=1,
            description="sub-assembly 의 고유 이름 (예: 'display_module').",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise RuntimeError("sub_assembly_tag: body is None")
        if not args.component_names:
            raise RuntimeError("sub_assembly_tag: component_names is empty")

        shape = body.wrapped if hasattr(body, "wrapped") else body
        prior_names = get_component_names(body)
        components = list(iter_compound_components(shape, prior_names))
        known = {n for n, _ in components}

        missing = [n for n in args.component_names if n not in known]
        if missing:
            raise RuntimeError(
                f"sub_assembly_tag: components not found: {missing} "
                f"(known: {sorted(known)})"
            )

        sub_map = _get_sub_assemblies(body)
        existing_members = sub_map.get(args.sub_assembly_name, [])
        # union (preserve first-seen order, then append new ones in input order)
        merged = list(existing_members)
        seen = set(merged)
        for n in args.component_names:
            if n not in seen:
                merged.append(n)
                seen.add(n)
        sub_map[args.sub_assembly_name] = merged

        # Re-attach the metadata to the (unchanged) body.
        body._pd_sub_assemblies = sub_map

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "component_names": [n for n, _ in components],
                "sub_assemblies": {k: list(v) for k, v in sub_map.items()},
                "tagged_sub_assembly": args.sub_assembly_name,
                "tagged_members": list(merged),
            },
        )
