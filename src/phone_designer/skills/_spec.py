"""SkillSpec — 메타데이터 + base 클래스.

[[lat.md/concepts.md#skillspec-메타데이터-스키마]] 구현.

모든 skill 은:
  1. @skill(...) 데코레이터로 SkillSpec 메타데이터 등록
  2. SkillBase 상속 + Args (pydantic) 정의 + _apply() 구현
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap, HistoryRule, SelectorFreeze
from phone_designer.skills._post_conditions import (
    PostCondition,
    PostConditionError,
    _measure,
    check_post_conditions,
)


SkillLevel = Literal["atomic", "macro"]


@dataclass
class ProcessRule:
    """1 공정의 DFM 규칙. simpleeval 로 평가되는 expression 가능."""

    min_wall_mm: float | str | None = None
    min_draft_deg: float | str | None = None
    undercut_allowed: bool = False
    min_fillet_r_mm: float | str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.min_wall_mm is not None:
            out["min_wall_mm"] = self.min_wall_mm
        if self.min_draft_deg is not None:
            out["min_draft_deg"] = self.min_draft_deg
        out["undercut_allowed"] = self.undercut_allowed
        if self.min_fillet_r_mm is not None:
            out["min_fillet_r_mm"] = self.min_fillet_r_mm
        out.update(self.extras)
        return out


@dataclass
class ManufacturingSpec:
    """이 skill 이 어느 공정에서 어떻게 실현되는가."""

    processes: dict[str, ProcessRule] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {p: r.to_dict() for p, r in self.processes.items()}


@dataclass
class SkillSpec:
    """[[lat.md/concepts.md#skillspec-메타데이터-스키마]] 의 dataclass 표현.

    `preconditions`, `failure_modes` 는 함수가 아닌 **named ref** (str). 실제 함수는
    별도 registry. 이렇게 해야 manifest 가 JSON 직렬화 가능 + LLM 가 reference 만 읽음.
    """

    name: str
    category: str
    level: SkillLevel
    summary: str
    args_model: type[BaseModel]
    selector_kinds: list[str] = field(default_factory=list)
    history_rules: dict[str, HistoryRule] = field(default_factory=dict)
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    # Declarative *executable* checks. Distinct from the legacy
    # `postconditions: list[str]` (those are LLM-readable named refs only).
    # SkillBase.apply() evaluates these after `_apply` and raises
    # PostConditionError on failure — caught by PlanExecutor like any other
    # skill exception.
    post_conditions: list[PostCondition] = field(default_factory=list)
    produces_features: list[str] = field(default_factory=list)
    preserves: list[str] = field(default_factory=list)
    manufacturing: ManufacturingSpec = field(default_factory=ManufacturingSpec)
    failure_modes: list[str] = field(default_factory=list)
    cost_hint: float = 0.5             # 0..1, Phase 1 에서 실측 가능
    expansion: list[str] | None = None  # macro only
    implementation_class: type | None = None  # @skill 가 채움

    def to_manifest_dict(self) -> dict[str, Any]:
        """LLM tool schema + manifest export 용."""
        return {
            "name": self.name,
            "category": self.category,
            "level": self.level,
            "summary": self.summary,
            "args_schema": self.args_model.model_json_schema(),
            "selector_kinds": self.selector_kinds,
            "history_rules": {k: v.value for k, v in self.history_rules.items()},
            "preconditions": self.preconditions,
            "postconditions": self.postconditions,
            "produces_features": self.produces_features,
            "preserves": self.preserves,
            "manufacturing": self.manufacturing.to_dict(),
            "failure_modes": self.failure_modes,
            "cost_hint": self.cost_hint,
            "expansion": self.expansion,
        }


@dataclass
class SkillResult:
    """skill.apply() 의 반환 — [[lat.md/persistent-naming]] + [[lat.md/plan-determinism]].

    `extras` 는 read-only / 진단 skill 들이 추가 정보를 실어 보내는 자유공간.
    SkillBase.apply() 의 post-condition / tag propagation 과 무관하다. 기본 빈 dict
    이므로 기존 skill 코드 (29 개) 의 SkillResult(body=..., history=...) 호출은
    그대로 동작한다."""

    body: Any                              # build123d.Part 또는 OCP TopoDS_Shape
    history: EntityHistoryMap
    selector_freeze: SelectorFreeze | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class SkillBase(ABC):
    """모든 skill 은 이를 상속한다.

    Subclass 가 정의해야 할 것:
      Args (BaseModel)  — 인자 스키마
      _apply(body, args) → SkillResult
    """

    spec: ClassVar[SkillSpec]   # @skill 데코레이터가 채움
    Args: ClassVar[type[BaseModel]]

    @abstractmethod
    def _apply(self, body: Any, args: BaseModel) -> SkillResult:
        """실제 OCCT/build123d 호출. SkillResult 반환."""
        ...

    def apply(self, body: Any, args_dict: dict[str, Any] | BaseModel) -> SkillResult:
        """Public entry — args 검증 + precondition 평가 + _apply 호출 + tag chain propagate.

        tag propagation:
          input body._pd_tags 가 있으면 result.body 에 자동 복사. 후속 skill 이
          tagged selector 로 그 tag 참조 가능. 단 형상이 mutate 되면 EntityRef 의
          bbox_center 가 어긋날 수 있음 — tagged resolver 가 nearest-match 로 처리.
        """
        if isinstance(args_dict, dict):
            args = self.Args.model_validate(args_dict)
        else:
            args = args_dict

        # Pre-execution shape metrics (None when create skills receive body=None).
        pre_metrics = _measure(body)

        result = self._apply(body, args)

        # Post-condition verification — catches silent no-ops like an
        # extrude_pocket that removed 0 mm³ due to a face-orientation bug.
        # Skills opt-in via `post_conditions=[...]` in their @skill(...) block.
        spec = getattr(self, "spec", None)
        if spec is not None and getattr(spec, "post_conditions", None):
            post_metrics = _measure(result.body)
            check_post_conditions(
                spec.post_conditions, pre_metrics, post_metrics, spec.name,
            )

        # tag chain propagate v2 — declared history_rules 에 따라 input ref 들을
        # output body 의 face 로 옮긴다. modified_inherit 는 nearest-by-bbox,
        # consumed 는 drop, split_branch 는 TOL 안의 모든 face. helper 가 best-
        # effort 로 실패해도 skill 자체는 정상 종료.
        from phone_designer.skills._tag_propagation import propagate_tags
        from phone_designer.skills.compose.tag_face import get_tags, set_tags

        history_rules = (
            getattr(spec, "history_rules", None) if spec is not None else None
        )
        try:
            propagate_tags(body, result.body, history_rules)
        except Exception:
            # 마지막 방어선 — propagate_tags 가 자체 try/except 가지만 import
            # 단계 실패 등은 잡아낸다.
            pass

        # propagation 후, result 자체가 _apply 안에서 단 tag (예: tag_face 의 신규
        # tag) 가 보존되도록 다시 merge.
        result_tags = get_tags(result.body)
        if not result_tags and body is not None:
            # propagate_tags 가 비워두었거나 (input 도 비어 있었거나) — input 의
            # tag 를 한 번 더 시도해서 inspect 같은 read-only skill 에서도
            # 안전하게 보존되도록.
            input_tags = get_tags(body)
            if input_tags and result.body is not None:
                set_tags(result.body, dict(input_tags))

        return result
