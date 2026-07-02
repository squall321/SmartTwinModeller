"""Plan / Step Pydantic 모델.

[[lat.md/concepts.md#plan]] + [[lat.md/plan-determinism]] 의 schema 구현.
"""
from __future__ import annotations

import math
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


#: Highest plan schema version this build can LOAD. Note that param-less
#: plans still SERIALIZE as v1 (see ``Plan.schema_version``) — v2 is purely
#: additive (optional top-level ``parameters`` table).
CURRENT_SCHEMA_VERSION = 2


class ParameterDef(BaseModel):
    """Schema v2 named parameter — ``parameters: {name: ParameterDef}``.

    ``value`` is either a float LITERAL or a str EXPRESSION over *other*
    parameter names (a derived parameter, e.g. ``"housing_length/2 - wall"``).
    Expressions use the same simpleeval whitelist as
    ``manufacturing/string_eval.py`` (arithmetic + min/max/abs/round) and are
    resolved by :mod:`phone_designer.plan.params` with undefined-name and
    cycle detection (structured ``fm.expr_error`` refusals).
    """

    value: float | str
    unit: str = "mm"
    description: str | None = None

    @field_validator("value")
    @classmethod
    def _value_sane(cls, v: float | str) -> float | str:
        # strict-JSON-safe: inf/nan may never enter the parameter table.
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError(
                "parameter value must be finite (strict-JSON-safe: no inf/nan)")
        if isinstance(v, str) and not v.strip():
            raise ValueError(
                "derived parameter expression must be a non-empty string")
        return v


class StepStatus(str, Enum):
    PENDING  = "pending"
    PASS     = "pass"
    FAIL     = "fail"
    SKIPPED  = "skipped"    # 앞 step 실패로 인해 비활성


class FreezeMeta(BaseModel):
    """[[lat.md/plan-determinism#selectorfreeze-구조]]."""

    matched_count: int
    sort_key: str = "lexicographic_bbox_center"
    topology_signature: str = ""


class FailureMeta(BaseModel):
    """Step 실패 메타데이터."""

    error_type: str
    message: str
    mapped_message: str | None = None     # [[lat.md/ui#error-mapping]] 의 친절 메시지
    raw_traceback: str | None = None


class Step(BaseModel):
    """Plan 의 1 step."""

    id: str
    skill: str
    args: dict[str, Any] = Field(default_factory=dict)
    selector_freeze: FreezeMeta | None = None
    status: StepStatus = StepStatus.PENDING
    failure: FailureMeta | None = None
    notes: str | None = None       # 사용자 코멘트
    # V5 per-step provenance — populated by PlanExecutor after each PASS step
    # from SkillResult.extras['_step_metrics']:
    #   {pre_volume_mm3, post_volume_mm3, delta_mm3,
    #    pre_face_count, post_face_count, duration_ms}
    # None for steps that never ran (SKIPPED/FAIL before _apply returned).
    metrics: dict[str, Any] | None = None


class Plan(BaseModel):
    """Plan 전체."""

    # BYTE-STABILITY (corpus-regress blocking gate): the default stays 1, NOT
    # CURRENT_SCHEMA_VERSION. Schema v2 is purely additive (optional
    # `parameters` table), so a plan that uses no parameters keeps the exact
    # v1 wire format — `schema_version: 1`, no `parameters:` key — and every
    # pre-v2 plan/baseline round-trips byte-identically. The after-validator
    # below bumps the version to 2 automatically the moment a parameter table
    # is actually present, so the version honestly reflects the features used.
    schema_version: int = 1
    plan_name: str
    description: str | None = None
    steps: list[Step]
    # Schema v2 — named parameter table. Any step arg (nested at any depth in
    # dicts/lists) may then be an expression node ``{"$expr": "length/2 -
    # wall"}`` resolved against this table by plan/params.py (see
    # ``resolve_plan``). None ⇒ no key serialized (save_plan excludes None).
    parameters: dict[str, ParameterDef] | None = None
    # PACK F (PLAN-CONTINUE-ON-FAIL) — opt-in graceful degradation. When
    # True, a failing step is recorded with status=FAIL + FailureMeta but
    # subsequent steps still execute against the LAST KNOWN good body
    # instead of being mass-SKIPPED. Default False keeps the strict
    # fail-fast semantics that hand-authored plans rely on. Reverse-
    # engineering pipelines (long auto-generated plans where a single bad
    # step from catalog noise shouldn't tear down 119 others) opt in.
    continue_on_step_failure: bool = False
    # V5 strict_cuts — opt-in hard gate on wasted material-removal steps.
    # When True, a zero-delta SKIP (the executor's PLAN-DEPTH-CEILING
    # leniency for cuts that removed no material) on a material-removal
    # step (category modify/* or hole/extrude_pocket*/counterbore*/
    # countersink*/tap_drill*/clearance* skills) increments error_count so
    # the plan outcome is FAIL instead of silently passing. Default False
    # keeps existing corpus behavior bit-identical when the flag is absent.
    strict_cuts: bool = False

    @model_validator(mode="after")
    def _schema_version_reflects_features(self) -> "Plan":
        """v2 마킹은 parameters 를 실제로 쓸 때만.

        - ``parameters == {}`` 는 None 으로 정규화 — param-less plan 이
          ``parameters: {}`` 키를 직렬화해 corpus byte-identity 를 깨는 일이
          없도록.
        - parameters 가 실제로 있으면 schema_version 을 최소 2 로 승격
          (명시적으로 더 높은 버전을 준 경우는 존중).
        """
        if not self.parameters:
            self.parameters = None
        elif self.schema_version < 2:
            self.schema_version = 2
        return self

    def find_step(self, step_id: str) -> Step | None:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def index_of(self, step_id: str) -> int:
        for i, s in enumerate(self.steps):
            if s.id == step_id:
                return i
        return -1
