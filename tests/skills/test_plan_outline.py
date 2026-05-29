"""Plan YAML 의 schema 검증 — Phase 2 executor 없이도 plan 자체의 정합성 확인."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from phone_designer.skills._registry import registry
# Side-effect: 8 skill 등록
from phone_designer.skills import export_manifest  # noqa: F401


PLANS_DIR = Path(__file__).parent.parent.parent / "plans"


def _all_plan_files():
    return list(PLANS_DIR.glob("*.yaml"))


@pytest.mark.parametrize("plan_path", _all_plan_files(), ids=lambda p: p.stem)
def test_plan_has_required_fields(plan_path):
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    assert "schema_version" in plan
    assert plan["schema_version"] == 1
    assert "plan_name" in plan
    assert "steps" in plan and isinstance(plan["steps"], list)


@pytest.mark.parametrize("plan_path", _all_plan_files(), ids=lambda p: p.stem)
def test_plan_step_skills_in_manifest(plan_path):
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    known_skills = {s.name for s in registry.all()}
    for step in plan["steps"]:
        assert step["skill"] in known_skills, (
            f"unknown skill in {plan_path.name}#{step['id']}: {step['skill']}"
        )


@pytest.mark.parametrize("plan_path", _all_plan_files(), ids=lambda p: p.stem)
def test_plan_step_args_validate(plan_path):
    """각 step 의 args 가 해당 skill 의 Args pydantic 모델과 호환."""
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    for step in plan["steps"]:
        spec = registry.get(step["skill"])
        args = step.get("args", {})
        # pydantic validation — 실패면 ValidationError
        spec.args_model.model_validate(args)


def test_simple_watch_outer_uses_8_skills():
    """simple_watch_outer.yaml 이 Phase 1 의 8 skill 중 다수를 사용."""
    plan = yaml.safe_load((PLANS_DIR / "simple_watch_outer.yaml").read_text(encoding="utf-8"))
    used = {step["skill"] for step in plan["steps"]}
    # 적어도 4종 다른 skill 사용
    assert len(used) >= 4
