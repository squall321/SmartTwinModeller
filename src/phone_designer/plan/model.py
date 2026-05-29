"""Plan / Step Pydantic 모델.

[[lat.md/concepts.md#plan]] + [[lat.md/plan-determinism]] 의 schema 구현.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


CURRENT_SCHEMA_VERSION = 1


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


class Plan(BaseModel):
    """Plan 전체."""

    schema_version: int = CURRENT_SCHEMA_VERSION
    plan_name: str
    description: str | None = None
    steps: list[Step]

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
