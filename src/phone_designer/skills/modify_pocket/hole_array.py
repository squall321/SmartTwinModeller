"""hole_array — macro. point list × hole.

speaker grille, vent array, mic array 같은 반복 hole 패턴.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="hole_array",
    category="modify/hole",
    level="macro",
    summary="Drill multiple identical holes at the given points. Macro = hole repeated.",
    selector_kinds=[],
    history_rules={
        "result_cylinder_faces": HistoryRule.GENERATED_NEW,
        "consumed_volume":        HistoryRule.CONSUMED,
    },
    produces_features=["cylindrical_hole_array"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.hole_exits_body", "fm.hole_too_close_to_edge"],
    cost_hint=0.2,
    expansion=["hole"] * 4,    # 평균 4개 가정
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class HoleArray(SkillBase):
    class Args(BaseModel):
        points: list[tuple[float, float, float]] = Field(min_length=1)
        diameter_mm: float = Field(gt=0, le=100)
        depth_mm: float | None = Field(default=None, gt=0, le=200,
                                        description="None = through")
        direction: Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"] = "-Z"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.modify_pocket.hole import Hole

        # macro expansion — 각 point 마다 hole 호출
        hole_skill = Hole()
        current = body
        for pt in args.points:
            r = hole_skill.apply(current, {
                "position": pt,
                "diameter_mm": args.diameter_mm,
                "depth_mm": args.depth_mm,
                "direction": args.direction,
            })
            current = r.body

        history = EntityHistoryMap(
            rules={
                "result_cylinder_faces": HistoryRule.GENERATED_NEW,
                "consumed_volume":        HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=current, history=history)
