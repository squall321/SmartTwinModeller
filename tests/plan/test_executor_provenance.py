"""V5 — executor per-step provenance + strict_cuts.

Covers:
  (a) per-step volume deltas in the report sum to total volume drift
  (b) an out-of-body cut lands in skipped_steps; strict_cuts=true in the
      plan dict flips the outcome to FAIL
  (c) an unparseable volume-measure failure ("could not measure volume")
      routes to FAIL, not SKIP
  (d) without the flag, the same plan passes exactly as before
"""
from __future__ import annotations

import math

import pytest

from phone_designer.plan.executor import (
    PlanExecutor,
    _import_all_skills,
    _is_zero_delta_volume_failure,
)
from phone_designer.plan.model import Plan, Step, StepStatus
from phone_designer.skills._post_conditions import PostConditionError
from phone_designer.skills._registry import registry


BOX_ARGS = {"length_mm": 20.0, "width_mm": 20.0, "height_mm": 10.0}
BOX_VOLUME = 20.0 * 20.0 * 10.0
# Box is XY-centered with Z bottom at 0 → spans x/y in [-10, 10], z in [0, 10].
HOLE_1 = {"position": (5.0, 5.0, 10.0), "diameter_mm": 4.0, "depth_mm": 5.0}
HOLE_2 = {"position": (-5.0, -5.0, 10.0), "diameter_mm": 3.0, "depth_mm": None}
# Far outside the box — the boolean cut succeeds but removes 0 mm³.
HOLE_OUT_OF_BODY = {"position": (500.0, 500.0, 10.0),
                    "diameter_mm": 4.0, "depth_mm": 5.0}


def _three_step_plan() -> Plan:
    return Plan(
        schema_version=1,
        plan_name="provenance_3step",
        steps=[
            Step(id="s1", skill="box", args=BOX_ARGS),
            Step(id="s2", skill="hole", args=HOLE_1),
            Step(id="s3", skill="hole", args=HOLE_2),
        ],
    )


# ---------------------------------------------------------------------------
# (a) per-step metrics + deltas sum to total drift
# ---------------------------------------------------------------------------

def test_report_contains_per_step_metrics():
    plan = _three_step_plan()
    result = PlanExecutor(plan).run()
    assert result.outcome == "PASS"

    report = result.to_report_json()
    assert report["plan_name"] == "provenance_3step"
    assert report["outcome"] == "PASS"
    assert report["error_count"] == 0
    assert report["skipped_steps"] == []
    assert [s["id"] for s in report["steps"]] == ["s1", "s2", "s3"]

    for entry in report["steps"]:
        assert entry["status"] == "pass"
        assert entry["failure_reason"] is None
        m = entry["metrics"]
        assert m is not None
        for key in ("pre_volume_mm3", "post_volume_mm3", "delta_mm3",
                    "pre_face_count", "post_face_count", "duration_ms"):
            assert key in m
        assert m["duration_ms"] >= 0.0

    s1, s2, s3 = report["steps"]
    # Create step: no input body → pre metrics / delta are None.
    assert s1["metrics"]["pre_volume_mm3"] is None
    assert s1["metrics"]["delta_mm3"] is None
    assert s1["metrics"]["post_volume_mm3"] == pytest.approx(BOX_VOLUME, rel=1e-6)

    # Hole steps remove the expected cylinder volumes.
    expected_d2 = -math.pi * (HOLE_1["diameter_mm"] / 2) ** 2 * HOLE_1["depth_mm"]
    expected_d3 = -math.pi * (HOLE_2["diameter_mm"] / 2) ** 2 * 10.0  # through 10 mm
    assert s2["metrics"]["delta_mm3"] == pytest.approx(expected_d2, rel=1e-4)
    assert s3["metrics"]["delta_mm3"] == pytest.approx(expected_d3, rel=1e-4)

    # Deltas sum to the total volume drift from first to last body.
    total_drift = s3["metrics"]["post_volume_mm3"] - s1["metrics"]["post_volume_mm3"]
    delta_sum = sum(
        s["metrics"]["delta_mm3"]
        for s in report["steps"]
        if s["metrics"]["delta_mm3"] is not None
    )
    assert delta_sum == pytest.approx(total_drift, rel=1e-9, abs=1e-6)

    # Metrics are also persisted on the Step models themselves.
    assert plan.find_step("s2").metrics["delta_mm3"] == pytest.approx(
        expected_d2, rel=1e-4)


# ---------------------------------------------------------------------------
# (b) out-of-body cut → skipped_steps; strict_cuts flips outcome to FAIL
# ---------------------------------------------------------------------------

def _out_of_body_plan_dict(*, strict_cuts: bool | None) -> dict:
    d = {
        "schema_version": 1,
        "plan_name": "wasted_cut",
        "steps": [
            {"id": "s1", "skill": "box", "args": BOX_ARGS},
            {"id": "s2", "skill": "hole", "args": HOLE_OUT_OF_BODY},
            {"id": "s3", "skill": "hole", "args": HOLE_1},
        ],
    }
    if strict_cuts is not None:
        d["strict_cuts"] = strict_cuts
    return d


def test_out_of_body_cut_recorded_in_skipped_steps():
    plan = Plan.model_validate(_out_of_body_plan_dict(strict_cuts=None))
    result = PlanExecutor(plan).run()

    assert plan.find_step("s2").status == StepStatus.SKIPPED
    skipped_ids = [s["step_id"] for s in result.skipped_steps]
    assert skipped_ids == ["s2"]
    assert "zero_delta_volume" in result.skipped_steps[0]["reason"]

    report = result.to_report_json()
    assert [s["step_id"] for s in report["skipped_steps"]] == ["s2"]


def test_strict_cuts_true_in_plan_dict_flips_outcome_to_fail():
    plan = Plan.model_validate(_out_of_body_plan_dict(strict_cuts=True))
    assert plan.strict_cuts is True
    result = PlanExecutor(plan).run()

    # Step stays SKIPPED (body preserved, plan continues) but the wasted
    # material-removal cut is counted as an error → outcome FAIL.
    assert plan.find_step("s2").status == StepStatus.SKIPPED
    assert result.error_count == 1
    assert result.outcome == "FAIL"
    # Subsequent step still executed against the preserved body.
    assert plan.find_step("s3").status == StepStatus.PASS
    assert result.final_body is not None


# ---------------------------------------------------------------------------
# (c) unparseable volume-measure failure routes to FAIL, not SKIP
# ---------------------------------------------------------------------------

COULD_NOT_MEASURE_MSG = (
    "hole: post_condition 'volume_decreased' failed — "
    "could not measure volume (pre=None, post=None)"
)


def test_unparseable_message_is_not_zero_delta():
    assert _is_zero_delta_volume_failure(COULD_NOT_MEASURE_MSG) is False
    # Genuine zero-delta messages still parse and skip.
    assert _is_zero_delta_volume_failure(
        "hole: post_condition 'volume_decreased' failed — "
        "pre=4000.0000 mm³, post=4000.0000 mm³, "
        "delta=0.0000 mm³ (expected ≤ -0.0100)"
    ) is True


def test_could_not_measure_volume_routes_to_fail(monkeypatch):
    _import_all_skills()
    hole_cls = registry.get("hole").implementation_class

    def _raise_unmeasurable(self, body, args_dict):
        raise PostConditionError(COULD_NOT_MEASURE_MSG)

    monkeypatch.setattr(hole_cls, "apply", _raise_unmeasurable)

    plan = Plan(
        schema_version=1,
        plan_name="unmeasurable",
        steps=[
            Step(id="s1", skill="box", args=BOX_ARGS),
            Step(id="s2", skill="hole", args=HOLE_1),
        ],
    )
    result = PlanExecutor(plan).run()

    assert plan.find_step("s2").status == StepStatus.FAIL
    assert result.error_count == 1
    assert result.outcome == "FAIL"
    # It is a FAIL, not a zero-delta skip.
    assert all(s["step_id"] != "s2" for s in result.skipped_steps)
    assert "could not measure volume" in plan.find_step("s2").failure.message


# ---------------------------------------------------------------------------
# (d) existing behavior without the flag — unchanged
# ---------------------------------------------------------------------------

def test_without_flag_same_plan_passes_as_before():
    plan = Plan.model_validate(_out_of_body_plan_dict(strict_cuts=None))
    assert plan.strict_cuts is False  # default
    result = PlanExecutor(plan).run()

    # Pre-V5 semantics: wasted cut = SKIP, no error, plan continues, PASS.
    assert result.error_count == 0
    assert result.outcome == "PASS"
    assert plan.find_step("s1").status == StepStatus.PASS
    assert plan.find_step("s2").status == StepStatus.SKIPPED
    assert plan.find_step("s3").status == StepStatus.PASS
    assert result.final_body is not None


def test_upstream_failure_skips_are_recorded():
    plan = Plan(
        schema_version=1,
        plan_name="cascade",
        steps=[
            Step(id="bad", skill="hole",
                 args={"position": (0.0, 0.0, 0.0),
                       "diameter_mm": -1.0,   # invalid → FAIL
                       "depth_mm": 5.0}),
            Step(id="after", skill="box", args=BOX_ARGS),
        ],
    )
    result = PlanExecutor(plan).run()
    assert result.outcome == "FAIL"
    assert plan.find_step("after").status == StepStatus.SKIPPED
    assert {"step_id": "after", "reason": "upstream_failure"} in result.skipped_steps
