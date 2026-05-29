"""ProcessRegistry + ManufacturingBudget + simpleeval."""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.manufacturing import (
    ManufacturingBudget,
    process_registry,
    safe_eval,
)


def test_registry_loads_5_processes():
    codes = process_registry.codes()
    expected = {"die_cast_al", "injection_mold_pa", "cnc_3axis",
                "cnc_5axis", "sheet_metal_stamp"}
    assert expected <= set(codes)


def test_die_cast_al_applicable_to_box():
    proc = process_registry.get("die_cast_al")
    assert proc.is_applicable("box")
    assert proc.is_applicable("disc_with_dome")


def test_die_cast_al_not_applicable_to_polymer_inlay():
    proc = process_registry.get("die_cast_al")
    assert not proc.is_applicable("polymer_inlay")


def test_sheet_metal_not_applicable_to_disc_dome():
    proc = process_registry.get("sheet_metal_stamp")
    assert not proc.is_applicable("disc_with_dome")


def test_cnc_5axis_universal():
    proc = process_registry.get("cnc_5axis")
    assert proc.is_applicable("loft_side_profile")
    assert proc.is_applicable("polymer_inlay")


def test_safe_eval_constants():
    assert safe_eval(5.0) == 5.0
    assert safe_eval("3 + 2") == 5


def test_safe_eval_with_args_object():
    class Args:
        radius_mm = 2.0
    assert safe_eval("args.radius_mm * 0.5", args=Args()) == 1.0


def test_safe_eval_rejects_unsafe():
    """eval() 등 위험 함수는 호출 불가."""
    # __import__ 같은 dunder 가 evaluator 의 names 에 없어 NameError → None
    assert safe_eval("__import__('os')") is None


def test_budget_from_yaml():
    p = Path(__file__).parent.parent.parent / "catalogs" / "budgets" / "watch_al_unibody.yaml"
    if not p.exists():
        pytest.skip("budget YAML 없음")
    budget = ManufacturingBudget.from_yaml(p)
    assert "die_cast_al" in budget.allowed_processes


def test_budget_allows_skill_in_budget():
    budget = ManufacturingBudget(
        allowed_processes=["die_cast_al", "cnc_3axis"],
        complexity_budget="high",
    )
    ok, codes = budget.allows_skill("box")
    assert ok
    assert "die_cast_al" in codes


def test_budget_rejects_skill_not_in_any_process():
    budget = ManufacturingBudget(
        allowed_processes=["sheet_metal_stamp"],
        complexity_budget="medium",
    )
    ok, codes = budget.allows_skill("disc_with_dome")
    assert not ok
    assert codes == []


def test_budget_validate_plan():
    from phone_designer.plan.model import Plan, Step

    plan = Plan(
        schema_version=1, plan_name="t",
        steps=[
            Step(id="s1", skill="box",
                 args={"length_mm": 10, "width_mm": 10, "height_mm": 10}),
            Step(id="s2", skill="polymer_inlay",
                 args={"start": (0, 0, 0), "end": (5, 0, 0),
                       "width_mm": 1, "depth_mm": 5}),
        ],
    )
    budget = ManufacturingBudget(
        allowed_processes=["die_cast_al"],
    )
    result = budget.validate_plan(plan)
    # polymer_inlay 는 die_cast 의 not_applicable_to — violation
    assert not result["ok"]
    violating_skills = {v["skill"] for v in result["violations"]}
    assert "polymer_inlay" in violating_skills
    assert "box" not in violating_skills
