"""sphere — atomic create skill.

raw OCCT BRepPrimAPI_MakeSphere. 전체 sphere 기본, partial sphere 도 가능
(angle1/angle2: latitude bounds, angle3: longitude span).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="sphere",
    category="create",
    level="atomic",
    summary="Raw sphere primitive at given center. Optional partial sphere via lat/long angles.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["sphere_solid"],
    preserves=[],
    manufacturing={
        "cnc_5axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"undercut_required": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["zero_or_negative_dimension"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class Sphere(SkillBase):
    class Args(BaseModel):
        radius_mm: float = Field(gt=0)
        center: tuple[float, float, float] = (0.0, 0.0, 0.0)
        angle1_deg: float = -90.0
        angle2_deg: float = 90.0
        angle3_deg: float = 360.0

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
        from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
        from build123d import Part

        cx, cy, cz = args.center
        ax2 = gp_Ax2(gp_Pnt(cx, cy, cz), gp_Dir(0.0, 0.0, 1.0))
        a1 = math.radians(args.angle1_deg)
        a2 = math.radians(args.angle2_deg)
        a3 = math.radians(args.angle3_deg)
        maker = BRepPrimAPI_MakeSphere(ax2, args.radius_mm, a1, a2, a3)
        maker.Build()
        if not maker.IsDone():
            raise RuntimeError("BRepPrimAPI_MakeSphere failed")
        shape = maker.Shape()

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shape), history=history)
