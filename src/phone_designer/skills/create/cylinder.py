"""cylinder — atomic create skill.

raw OCCT BRepPrimAPI_MakeCylinder. axis: X/Y/Z 선택, base center 점 지정.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="cylinder",
    category="create",
    level="atomic",
    summary="Raw cylinder primitive. Center on (cx, cy, base_z), height along chosen axis (X/Y/Z).",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["cylinder_solid"],
    preserves=[],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["zero_or_negative_dimension"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class Cylinder(SkillBase):
    class Args(BaseModel):
        radius_mm: float = Field(gt=0)
        height_mm: float = Field(gt=0)
        center_x_mm: float = 0.0
        center_y_mm: float = 0.0
        base_z_mm: float = 0.0
        axis: Literal["X", "Y", "Z"] = "Z"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
        from build123d import Part

        if args.axis == "X":
            direction = gp_Dir(1.0, 0.0, 0.0)
        elif args.axis == "Y":
            direction = gp_Dir(0.0, 1.0, 0.0)
        else:
            direction = gp_Dir(0.0, 0.0, 1.0)

        origin = gp_Pnt(args.center_x_mm, args.center_y_mm, args.base_z_mm)
        ax2 = gp_Ax2(origin, direction)
        maker = BRepPrimAPI_MakeCylinder(ax2, args.radius_mm, args.height_mm)
        maker.Build()
        if not maker.IsDone():
            raise RuntimeError("BRepPrimAPI_MakeCylinder failed")
        shape = maker.Shape()

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shape), history=history)
