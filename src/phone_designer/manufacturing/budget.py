"""ManufacturingBudget — 사용자가 허용하는 공정 + 복잡도.

[[lat.md/manufacturing#manufacturingbudget]] 의 구현.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class ManufacturingBudget(BaseModel):
    """공정 + 복잡도 제약. plan 검증과 DFM 의 입력."""

    allowed_processes: list[str] = Field(
        default_factory=lambda: ["die_cast_al", "cnc_3axis", "injection_mold_pa"],
        description="허용 process code list",
    )
    complexity_budget: Literal["low", "medium", "high"] = "medium"
    draft_relaxation: Literal["strict", "moderate", "lenient"] = "strict"
    slide_core: bool = False
    extras: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "ManufacturingBudget":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def save(self, path: Path | str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            yaml.safe_dump(self.model_dump(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def allows_skill(self, skill_name: str) -> tuple[bool, list[str]]:
        """skill 이 budget 내 어느 process 에서 가능한지.

        Returns:
            (allowed, applicable_process_codes)
        """
        from phone_designer.manufacturing.processes import registry
        proc_codes = []
        for code in self.allowed_processes:
            try:
                proc = registry.get(code)
                if proc.is_applicable(skill_name):
                    proc_codes.append(code)
            except KeyError:
                continue
        return (len(proc_codes) > 0, proc_codes)

    def validate_plan(self, plan) -> dict[str, Any]:
        """plan 의 모든 step skill 이 budget 안의 공정에서 가능한지.

        Args:
            plan: phone_designer.plan.model.Plan

        Returns:
            {"ok": bool, "violations": [{step_id, skill, reason}]}
        """
        violations = []
        for step in plan.steps:
            ok, codes = self.allows_skill(step.skill)
            if not ok:
                violations.append({
                    "step_id": step.id,
                    "skill": step.skill,
                    "reason": (f"skill '{step.skill}' is not applicable to any "
                                f"of allowed_processes={self.allowed_processes}"),
                })
        return {"ok": len(violations) == 0, "violations": violations}
