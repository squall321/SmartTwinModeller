"""union — atomic. body ∪ other_body."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.compose._load_other import OtherBodySpec, load_other_shape


@skill(
    name="union",
    category="compose",
    level="atomic",
    summary="Boolean union — merge 'other' body into current body.",
    selector_kinds=[],
    history_rules={
        "body_faces":  HistoryRule.MODIFIED_INHERIT,
        "other_faces": HistoryRule.MODIFIED_INHERIT,
        "seam_edges":  HistoryRule.GENERATED_NEW,
    },
    produces_features=["boolean_union"],
    preserves=["outer_envelope_outer"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.boolean_failed"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class Union(SkillBase):
    class Args(BaseModel):
        other: OtherBodySpec

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

        shape = body.wrapped if hasattr(body, "wrapped") else body
        other_shape = load_other_shape(args.other)

        fuse = BRepAlgoAPI_Fuse(shape, other_shape)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("union: BRepAlgoAPI_Fuse failed (IsDone=False)")
        new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={
                "body_faces":  HistoryRule.MODIFIED_INHERIT,
                "other_faces": HistoryRule.MODIFIED_INHERIT,
                "seam_edges":  HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
