"""torus — atomic create skill.

raw OCCT BRepPrimAPI_MakeTorus. major > minor (self-intersection 방지).
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="torus",
    category="create",
    level="atomic",
    summary="Raw torus primitive. major_radius > minor_radius. Optional partial (angle_deg).",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["torus_solid"],
    preserves=[],
    manufacturing={
        "cnc_5axis":         {"min_wall_mm": 0.5},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"undercut_required": True}},
    },
    failure_modes=["zero_or_negative_dimension", "major_le_minor"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class Torus(SkillBase):
    class Args(BaseModel):
        major_radius_mm: float = Field(gt=0)
        minor_radius_mm: float = Field(gt=0)
        center: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis: Literal["X", "Y", "Z"] = "Z"
        angle_deg: float = Field(default=360.0, gt=0, le=360.0)

        @model_validator(mode="after")
        def _major_gt_minor(self) -> "Torus.Args":
            if self.major_radius_mm <= self.minor_radius_mm:
                raise ValueError(
                    f"major_radius_mm ({self.major_radius_mm}) must be > "
                    f"minor_radius_mm ({self.minor_radius_mm}) to avoid self-intersection"
                )
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeTorus
        from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
        from build123d import Part

        if args.axis == "X":
            direction = gp_Dir(1.0, 0.0, 0.0)
        elif args.axis == "Y":
            direction = gp_Dir(0.0, 1.0, 0.0)
        else:
            direction = gp_Dir(0.0, 0.0, 1.0)

        cx, cy, cz = args.center
        ax2 = gp_Ax2(gp_Pnt(cx, cy, cz), direction)
        angle_rad = math.radians(args.angle_deg)
        maker = BRepPrimAPI_MakeTorus(
            ax2, args.major_radius_mm, args.minor_radius_mm, angle_rad,
        )
        maker.Build()
        if not maker.IsDone():
            raise RuntimeError("BRepPrimAPI_MakeTorus failed")
        shape = maker.Shape()

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shape), history=history)
