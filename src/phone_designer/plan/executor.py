"""PlanExecutor — Plan 을 순차 실행 + freeze 검증 + 실패 semantics.

[[lat.md/concepts.md#plan]] + [[lat.md/plan-determinism#실행-모드]] 구현.

v1 의 범위:
  - 순차 실행 (step 1 → N)
  - 각 step 의 SkillResult 캐싱 (step_results)
  - freeze mismatch 시 strict/loose 정책
  - 실패 시 후속 step 자동 SKIPPED
  - 결과 = 마지막 PASS step 의 body + 실행 보고

v2 이후:
  - incremental rebuild (step k 만 다시 실행, k+1..N 재실행)
  - rollback (실패 step 제거 후 자동 재시도)
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from phone_designer.plan.model import (
    FailureMeta,
    FreezeMeta,
    Plan,
    Step,
    StepStatus,
)
from phone_designer.skills._history import SelectorFreeze
from phone_designer.skills._registry import registry
from phone_designer.skills._spec import SkillResult


class ExecutionMode(str, Enum):
    STRICT = "strict"      # freeze mismatch = FAIL
    LOOSE  = "loose"       # freeze mismatch = WARN + 새 매칭 사용


@dataclass
class ExecutionResult:
    plan: Plan
    final_body: Any = None
    step_results: dict[str, SkillResult] = field(default_factory=dict)
    freeze_mismatches: list[dict[str, Any]] = field(default_factory=list)
    error_count: int = 0

    @property
    def outcome(self) -> str:
        return "PASS" if self.error_count == 0 else "FAIL"


def _import_all_skills():
    """모든 skill 모듈을 import 해 registry 에 등록."""
    from phone_designer.skills import export_manifest  # noqa: F401


class PlanExecutor:
    """Plan 을 실행한다.

    Args:
        plan: 실행할 Plan
        mode: STRICT (default, same-machine) | LOOSE (cross-platform)
    """

    def __init__(self, plan: Plan, *, mode: ExecutionMode = ExecutionMode.STRICT):
        _import_all_skills()
        self.plan = plan
        self.mode = mode

    def run(self, *, initial_body: Any = None) -> ExecutionResult:
        """Execute the plan sequentially.

        Args:
            initial_body: optional starting body. Used when the plan was
                produced with ``base_step_kind="preserve_brep"`` and the
                first step expects the original BREP surface as its input
                (rather than constructing it from a placeholder box).
        """
        result = ExecutionResult(plan=self.plan)
        body: Any = initial_body
        previous_pass = True

        for step in self.plan.steps:
            if not previous_pass:
                step.status = StepStatus.SKIPPED
                continue

            step.status = StepStatus.PENDING
            try:
                skill_result = self._apply_step(step, body)
                # Freeze 검증
                if step.selector_freeze is not None:
                    actual = skill_result.selector_freeze
                    if actual is None:
                        # freeze 가 plan 에 있지만 skill 이 반환 안 함 → warning
                        result.freeze_mismatches.append({
                            "step_id": step.id,
                            "reason": "expected_freeze_but_skill_returned_none",
                        })
                    else:
                        if not _freeze_matches(step.selector_freeze, actual):
                            if self.mode == ExecutionMode.STRICT:
                                step.status = StepStatus.FAIL
                                step.failure = FailureMeta(
                                    error_type="FreezeMismatch",
                                    message=(
                                        f"expected count={step.selector_freeze.matched_count} "
                                        f"sig={step.selector_freeze.topology_signature}, "
                                        f"got count={actual.matched_count} "
                                        f"sig={actual.topology_signature}"
                                    ),
                                )
                                result.error_count += 1
                                previous_pass = False
                                continue
                            else:
                                result.freeze_mismatches.append({
                                    "step_id": step.id,
                                    "expected": step.selector_freeze.model_dump(),
                                    "actual": _freeze_to_dict(actual),
                                })
                                # loose: 새 매칭 사용
                                step.selector_freeze = _freeze_to_meta(actual)
                else:
                    # 첫 실행 — freeze 자동 캡처
                    if skill_result.selector_freeze is not None:
                        step.selector_freeze = _freeze_to_meta(skill_result.selector_freeze)

                # 성공
                step.status = StepStatus.PASS
                step.failure = None
                body = skill_result.body
                result.step_results[step.id] = skill_result

            except Exception as e:
                # ``str(e)`` is empty for some OCCT-wrapped exceptions and
                # for bare ``RuntimeError()`` raises; fall back to repr so
                # downstream diagnostics (run_logs / corpus report) never see
                # a null message.
                msg = str(e) or repr(e) or f"{type(e).__name__} raised with no message"

                # PLAN-DEPTH-CEILING fix: when a post-condition fails
                # because the step produced a zero-delta volume change —
                # i.e. the cut landed over already-removed material, or
                # the boss landed inside an existing void — the body is
                # geometrically unchanged. Treat that as a SKIP rather
                # than a hard FAIL so subsequent steps in long plans can
                # continue. The drift gate downstream still rejects bad
                # reconstructions; we just stop early-aborting the entire
                # plan on a single wasted cut. This is NOT a determinism
                # failure (which is what STRICT vs LOOSE is for) so we
                # apply it in both modes.
                if (
                    type(e).__name__ == "PostConditionError"
                    and _is_zero_delta_volume_failure(msg)
                ):
                    step.status = StepStatus.SKIPPED
                    step.failure = FailureMeta(
                        error_type=type(e).__name__,
                        message=(
                            "SKIPPED — zero-delta volume post-condition "
                            "(step produced no geometric change; body "
                            "preserved): " + msg
                        ),
                        raw_traceback=traceback.format_exc(),
                    )
                    # Body unchanged — continue with previous body for the
                    # next step. A wasted cut doesn't tear the plan down.
                    previous_pass = True
                    continue

                step.status = StepStatus.FAIL
                step.failure = FailureMeta(
                    error_type=type(e).__name__,
                    message=msg,
                    raw_traceback=traceback.format_exc(),
                )
                result.error_count += 1
                # PACK F (PLAN-CONTINUE-ON-FAIL) — opt-in graceful
                # degradation. When ``plan.continue_on_step_failure`` is
                # set, a failing step is recorded but subsequent steps
                # still execute against the previous body. This is the
                # right policy for long auto-generated RE plans where a
                # single bad step (Args validation, selector drift, ...)
                # should not mass-SKIP the remaining 100+ steps. The body
                # is left unchanged so downstream steps operate on the
                # last good geometry.
                if getattr(self.plan, "continue_on_step_failure", False):
                    previous_pass = True
                else:
                    previous_pass = False

        # Safety net — any step left in FAIL without a populated failure (e.g.,
        # a future code path that sets status=FAIL but forgets FailureMeta)
        # gets a synthetic message so the corpus report never shows a null
        # error string again. This is the fix for PASS-GATE-EDGE:
        #   "executor_errors = 1 without surfacing which step".
        for step in self.plan.steps:
            if step.status == StepStatus.FAIL and step.failure is None:
                step.failure = FailureMeta(
                    error_type="UnknownFailure",
                    message=(
                        "step marked FAIL without specific error — "
                        "bug in executor's error capture"
                    ),
                )

        result.final_body = body
        return result

    def _apply_step(self, step: Step, body: Any) -> SkillResult:
        spec = registry.get(step.skill)
        cls = spec.implementation_class
        if cls is None:
            raise RuntimeError(f"skill {step.skill} 의 implementation 미등록")
        instance = cls()
        # args 검증
        args = spec.args_model.model_validate(step.args)
        return instance.apply(body, args)


def _freeze_matches(expected: FreezeMeta, actual: SelectorFreeze) -> bool:
    return (
        expected.matched_count == actual.matched_count
        and expected.topology_signature == actual.topology_signature
    )


def _freeze_to_dict(f: SelectorFreeze) -> dict[str, Any]:
    return {
        "matched_count": f.matched_count,
        "sort_key": f.sort_key,
        "topology_signature": f.topology_signature,
    }


def _freeze_to_meta(f: SelectorFreeze) -> FreezeMeta:
    return FreezeMeta(
        matched_count=f.matched_count,
        sort_key=f.sort_key,
        topology_signature=f.topology_signature,
    )


def _is_zero_delta_volume_failure(msg: str) -> bool:
    """True iff ``msg`` is a PostConditionError message for a volume_decreased
    / volume_increased check that failed with a near-zero delta (i.e. the
    step ran without raising an OCCT error but didn't actually change the
    body volume).

    The post_conditions module formats such messages as e.g.::

        extrude_pocket: post_condition 'volume_decreased' failed —
        pre=60099.5301 mm³, post=60099.5301 mm³, delta=0.0000 mm³
        (expected ≤ -0.01)

    We match on the post_condition name + the literal ``delta=`` token + a
    near-zero value (|delta| < 0.05 mm³). That keeps the fast-path for real
    geometric failures (e.g. a pocket that ate the entire body and so
    decreased volume more than expected) while still skipping the wasted-
    cut case that PLAN-DEPTH-CEILING surfaces.
    """
    if not msg:
        return False
    if "volume_decreased" not in msg and "volume_increased" not in msg:
        return False
    import re
    m = re.search(r"delta=(-?\d+(?:\.\d+)?)", msg)
    if m is None:
        # Defensive: if the message format changes, prefer skipping over
        # tearing the plan down — the body was preserved either way.
        return True
    try:
        delta = abs(float(m.group(1)))
    except (TypeError, ValueError):
        return True
    return delta < 0.05
