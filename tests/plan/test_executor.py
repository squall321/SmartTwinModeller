"""PlanExecutor v1 검증."""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.plan.executor import ExecutionMode, PlanExecutor
from phone_designer.plan.model import FreezeMeta, Plan, Step, StepStatus
from phone_designer.plan.yaml_io import load_plan


def _simple_plan() -> Plan:
    return Plan(
        schema_version=1,
        plan_name="exec_test",
        steps=[
            Step(id="s1", skill="box",
                 args={"length_mm": 20.0, "width_mm": 20.0, "height_mm": 10.0}),
            Step(id="s2", skill="fillet_edges_by_predicate",
                 args={
                     "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
                     "radius_mm": 1.5,
                 }),
        ],
    )


def test_executes_simple_plan_pass():
    plan = _simple_plan()
    result = PlanExecutor(plan).run()
    assert result.outcome == "PASS"
    assert all(s.status == StepStatus.PASS for s in plan.steps)
    assert result.final_body is not None


def test_first_execution_captures_freeze():
    plan = _simple_plan()
    PlanExecutor(plan).run()
    # s2 의 selector_freeze 가 자동 캡처됨
    s2 = plan.find_step("s2")
    assert s2.selector_freeze is not None
    assert s2.selector_freeze.matched_count == 4   # Z-축 edge 4개


def test_repeated_execution_freeze_matches():
    """같은 plan 5회 재실행 → freeze 일치."""
    sigs = []
    for _ in range(5):
        plan = _simple_plan()
        result = PlanExecutor(plan).run()
        s2 = plan.find_step("s2")
        sigs.append(s2.selector_freeze.topology_signature)
    assert len(set(sigs)) == 1, f"signatures: {set(sigs)}"


def test_strict_mode_detects_freeze_mismatch():
    """plan 에 잘못된 freeze 가 있으면 STRICT 모드에서 FAIL."""
    plan = _simple_plan()
    plan.steps[1].selector_freeze = FreezeMeta(
        matched_count=99,    # 일부러 틀린 값
        topology_signature="deadbeef00000000",
    )
    result = PlanExecutor(plan, mode=ExecutionMode.STRICT).run()
    assert result.outcome == "FAIL"
    assert plan.steps[1].status == StepStatus.FAIL
    assert plan.steps[1].failure.error_type == "FreezeMismatch"


def test_loose_mode_warns_on_freeze_mismatch():
    plan = _simple_plan()
    plan.steps[1].selector_freeze = FreezeMeta(
        matched_count=99,
        topology_signature="deadbeef00000000",
    )
    result = PlanExecutor(plan, mode=ExecutionMode.LOOSE).run()
    assert result.outcome == "PASS"   # loose 는 통과
    assert len(result.freeze_mismatches) == 1
    # 새 매칭이 plan 에 반영됨
    assert plan.steps[1].selector_freeze.matched_count == 4


def test_failure_skips_subsequent_steps():
    """1번 step 실패 → 2번부터는 SKIPPED."""
    plan = Plan(
        schema_version=1,
        plan_name="fail_test",
        steps=[
            # 잘못된 args (radius 너무 큼)
            Step(id="bad", skill="rounded_slab",
                 args={"length_mm": 10.0, "width_mm": 10.0, "height_mm": 10.0,
                       "corner_r_mm": 20.0}),
            Step(id="after", skill="box",
                 args={"length_mm": 5.0, "width_mm": 5.0, "height_mm": 5.0}),
        ],
    )
    result = PlanExecutor(plan).run()
    assert result.outcome == "FAIL"
    assert plan.steps[0].status == StepStatus.FAIL
    assert plan.steps[1].status == StepStatus.SKIPPED


def test_failure_captures_traceback():
    plan = Plan(
        schema_version=1, plan_name="x",
        steps=[Step(id="bad", skill="hole",
                    args={"position": (0.0, 0.0, 0.0),
                          "diameter_mm": -1.0,    # invalid
                          "depth_mm": 5.0})],
    )
    result = PlanExecutor(plan).run()
    assert result.outcome == "FAIL"
    f = plan.steps[0].failure
    assert f is not None
    assert f.raw_traceback is not None


def test_simple_watch_outer_yaml_executes():
    """plans/simple_watch_outer.yaml 의 reference plan 이 실행됨."""
    p = Path(__file__).parent.parent.parent / "plans" / "simple_watch_outer.yaml"
    plan = load_plan(p)
    result = PlanExecutor(plan).run()
    # 외피 reproduction 의 모든 step 이 PASS 해야 함
    failing = [s for s in plan.steps if s.status == StepStatus.FAIL]
    assert not failing, f"failing steps: {[(s.id, s.failure.message) for s in failing]}"
    assert result.final_body is not None
