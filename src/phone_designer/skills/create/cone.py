"""cone — atomic create skill (frustum 도 지원).

raw OCCT BRepPrimAPI_MakeCone. r1=lower, r2=upper.
r1=r2 → cylinder, r2=0 → 진짜 cone, 둘 다 양수 → frustum.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="cone",
    category="create",
    level="atomic",
    summary="Raw cone/frustum primitive. r1=lower, r2=upper; at least one > 0.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["cone_solid"],
    preserves=[],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["zero_or_negative_dimension", "both_radii_zero"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class Cone(SkillBase):
    class Args(BaseModel):
        radius_lower_mm: float = Field(ge=0)
        radius_upper_mm: float = Field(ge=0)
        height_mm: float = Field(gt=0)
        center_x_mm: float = 0.0
        center_y_mm: float = 0.0
        base_z_mm: float = 0.0
        axis: Literal["X", "Y", "Z"] = "Z"

        @model_validator(mode="after")
        def _at_least_one_positive(self) -> "Cone.Args":
            if self.radius_lower_mm <= 0 and self.radius_upper_mm <= 0:
                raise ValueError("at least one of radius_lower_mm / radius_upper_mm must be > 0")
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
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
        maker = BRepPrimAPI_MakeCone(
            ax2, args.radius_lower_mm, args.radius_upper_mm, args.height_mm,
        )
        maker.Build()
        if not maker.IsDone():
            raise RuntimeError("BRepPrimAPI_MakeCone failed")
        shape = maker.Shape()

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shape), history=history)
