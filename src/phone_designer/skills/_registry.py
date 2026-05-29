"""@skill 데코레이터 + 전역 SkillRegistry + manifest export.

[[lat.md/skills.md#manifest-구조]] 구현.

사용:
    @skill(
        name="fillet_edges_by_predicate",
        category="modify/fillet",
        level="atomic",
        summary="...",
        ...
    )
    class FilletEdgesByPredicate(SkillBase):
        class Args(BaseModel):
            ...
        def _apply(self, body, args):
            ...

빌드:
    python -m phone_designer.skills.export_manifest > manifest.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phone_designer.skills._spec import (
    ManufacturingSpec,
    ProcessRule,
    SkillBase,
    SkillLevel,
    SkillSpec,
)
from phone_designer.skills._history import HistoryRule
from phone_designer.skills._post_conditions import PostCondition


class SkillRegistry:
    """전역 skill catalog."""

    def __init__(self):
        self._skills: dict[str, SkillSpec] = {}

    def register(self, spec: SkillSpec) -> None:
        if spec.name in self._skills:
            raise ValueError(f"duplicate skill name: {spec.name}")
        self._skills[spec.name] = spec

    def get(self, name: str) -> SkillSpec:
        if name not in self._skills:
            raise KeyError(f"unknown skill: {name}")
        return self._skills[name]

    def all(self) -> list[SkillSpec]:
        return list(self._skills.values())

    def by_category(self, category_prefix: str) -> list[SkillSpec]:
        return [s for s in self._skills.values() if s.category.startswith(category_prefix)]

    def by_level(self, level: SkillLevel) -> list[SkillSpec]:
        return [s for s in self._skills.values() if s.level == level]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "skills": [s.to_manifest_dict() for s in self._skills.values()],
        }

    def export_manifest_json(self, path: Path | None = None) -> str:
        text = json.dumps(self.to_manifest(), ensure_ascii=False, indent=2)
        if path:
            path.write_text(text, encoding="utf-8")
        return text


# Module-level singleton
registry = SkillRegistry()


def _normalize_manufacturing(value: dict | ManufacturingSpec | None) -> ManufacturingSpec:
    if value is None:
        return ManufacturingSpec()
    if isinstance(value, ManufacturingSpec):
        return value
    # dict → ManufacturingSpec
    procs = {}
    for proc, rules in value.items():
        if isinstance(rules, ProcessRule):
            procs[proc] = rules
        else:
            known = {"min_wall_mm", "min_draft_deg", "undercut_allowed", "min_fillet_r_mm"}
            extras = {k: v for k, v in rules.items() if k not in known}
            procs[proc] = ProcessRule(
                min_wall_mm=rules.get("min_wall_mm"),
                min_draft_deg=rules.get("min_draft_deg"),
                undercut_allowed=rules.get("undercut_allowed", False),
                min_fillet_r_mm=rules.get("min_fillet_r_mm"),
                extras=extras,
            )
    return ManufacturingSpec(processes=procs)


def _normalize_history_rules(value: dict[str, str | HistoryRule] | None) -> dict[str, HistoryRule]:
    if not value:
        return {}
    out = {}
    for role, rule in value.items():
        if isinstance(rule, HistoryRule):
            out[role] = rule
        else:
            out[role] = HistoryRule(rule)
    return out


def skill(
    *,
    name: str,
    category: str,
    level: SkillLevel,
    summary: str,
    selector_kinds: list[str] | None = None,
    history_rules: dict[str, str | HistoryRule] | None = None,
    preconditions: list[str] | None = None,
    postconditions: list[str] | None = None,
    post_conditions: list[PostCondition] | None = None,
    produces_features: list[str] | None = None,
    preserves: list[str] | None = None,
    manufacturing: dict | ManufacturingSpec | None = None,
    failure_modes: list[str] | None = None,
    cost_hint: float = 0.5,
    expansion: list[str] | None = None,
):
    """Skill 클래스 데코레이터 — SkillSpec 만들고 registry 등록."""

    def decorator(cls: type[SkillBase]) -> type[SkillBase]:
        if not hasattr(cls, "Args"):
            raise TypeError(f"{cls.__name__} 에 Args (BaseModel) 가 없음")
        spec = SkillSpec(
            name=name,
            category=category,
            level=level,
            summary=summary,
            args_model=cls.Args,
            selector_kinds=selector_kinds or [],
            history_rules=_normalize_history_rules(history_rules),
            preconditions=preconditions or [],
            postconditions=postconditions or [],
            post_conditions=list(post_conditions or []),
            produces_features=produces_features or [],
            preserves=preserves or [],
            manufacturing=_normalize_manufacturing(manufacturing),
            failure_modes=failure_modes or [],
            cost_hint=cost_hint,
            expansion=expansion,
            implementation_class=cls,
        )
        cls.spec = spec
        registry.register(spec)
        return cls

    return decorator
