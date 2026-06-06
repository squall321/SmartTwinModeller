"""plan_from_scaled_catalog — thin macro wrapper.

Run :func:`vary_catalog` on the supplied feature_catalog, then drive
:class:`PlanFromFeatureCatalog` with the varied result. Useful for
"give me a 2× scaled variant of this part" prompts where the caller wants
both the varied catalog AND the generated plan in a single skill call.

extras carries:
    - ``varied_catalog`` — the dict produced by vary_catalog
    - ``generated_plan`` — the plan dict produced by PlanFromFeatureCatalog
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
    PlanFromFeatureCatalog,
)
from phone_designer.skills.reverse_engineer.vary_feature_catalog import vary_catalog


@skill(
    name="plan_from_scaled_catalog",
    category="reverse_engineer",
    level="atomic",
    summary="Vary a feature_catalog (uniform scale_factor + per_feature_scale "
            "+ absolute_overrides) and then convert the varied catalog into a "
            "Plan YAML via plan_from_feature_catalog. Body unchanged. "
            "extras carries both 'varied_catalog' and 'generated_plan'.",
    selector_kinds=[],
    history_rules={},
    produces_features=["varied_catalog", "generated_plan"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class PlanFromScaledCatalog(SkillBase):
    class Args(BaseModel):
        catalog: dict = Field(
            default_factory=dict,
            description="The feature_catalog dict to vary and re-plan.",
        )
        scale_factor: float | None = Field(
            default=None,
            description="Uniform multiplier on every dimensional field (see "
                        "vary_feature_catalog for the full field list).",
        )
        per_feature_scale: dict = Field(
            default_factory=dict,
            description="Dotted-key → multiplier overrides applied after "
                        "scale_factor.",
        )
        absolute_overrides: dict = Field(
            default_factory=dict,
            description="Dotted-key → absolute value overrides applied LAST.",
        )
        base_step_kind: Literal["box", "import_step", "preserve_brep"] = "box"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        varied = vary_catalog(
            args.catalog,
            scale_factor=args.scale_factor,
            per_feature_scale=args.per_feature_scale,
            absolute_overrides=args.absolute_overrides,
        )
        plan_res = PlanFromFeatureCatalog().apply(body, {
            "catalog": varied,
            "base_step_kind": args.base_step_kind,
        })
        generated_plan = plan_res.extras.get("generated_plan")
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "varied_catalog": varied,
                "generated_plan": generated_plan,
            },
        )
