"""subtract — atomic. body - other_body."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.compose._load_other import OtherBodySpec, load_other_shape


@skill(
    name="subtract",
    category="compose",
    level="atomic",
    summary="Boolean subtract — remove 'other' body from current body. "
            "'other' can be a STEP file, an XDE assembly part, or an inline primitive.",
    selector_kinds=[],
    history_rules={
        "body_faces":         HistoryRule.MODIFIED_INHERIT,
        "intersection_faces": HistoryRule.GENERATED_NEW,
        "consumed_volume":    HistoryRule.CONSUMED,
    },
    produces_features=["boolean_diff"],
    preserves=["outer_envelope_outside_other"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.no_intersection", "fm.boolean_failed"],
    cost_hint=0.2,
    # `subtract` with a non-overlapping `other` legitimately yields no
    # volume change. allow_no_change keeps that semantic — it still flags
    # genuine boolean failures because those raise an exception earlier.
    post_conditions=[PostCondition(kind="volume_decreased", allow_no_change=True)],
)
class Subtract(SkillBase):
    class Args(BaseModel):
        other: OtherBodySpec

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        shape = body.wrapped if hasattr(body, "wrapped") else body
        other_shape = load_other_shape(args.other)

        cut = BRepAlgoAPI_Cut(shape, other_shape)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("subtract: BRepAlgoAPI_Cut failed (IsDone=False)")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "body_faces":         HistoryRule.MODIFIED_INHERIT,
                "intersection_faces": HistoryRule.GENERATED_NEW,
                "consumed_volume":    HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
