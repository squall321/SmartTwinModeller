"""wedge — atomic create skill.

raw OCCT BRepPrimAPI_MakeWedge(dx, dy, dz, ltx).
ltx = top X length, must be ≤ dx (0 → degenerate ridge).
optional 평행이동 (center_x, center_y, base_z).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="wedge",
    category="create",
    level="atomic",
    summary="Raw wedge primitive. dx/dy/dz extents + ltx (top X length). Origin at (0,0,0) + offset.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["wedge_solid"],
    preserves=[],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["zero_or_negative_dimension", "ltx_exceeds_dx"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class Wedge(SkillBase):
    class Args(BaseModel):
        dx: float = Field(gt=0)
        dy: float = Field(gt=0)
        dz: float = Field(gt=0)
        ltx_mm: float = Field(ge=0)
        center_x_mm: float = 0.0
        center_y_mm: float = 0.0
        base_z_mm: float = 0.0

        @model_validator(mode="after")
        def _ltx_le_dx(self) -> "Wedge.Args":
            if self.ltx_mm > self.dx:
                raise ValueError(
                    f"ltx_mm ({self.ltx_mm}) must be <= dx ({self.dx})"
                )
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeWedge
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.gp import gp_Trsf, gp_Vec
        from build123d import Part

        maker = BRepPrimAPI_MakeWedge(args.dx, args.dy, args.dz, args.ltx_mm)
        maker.Build()
        if not maker.IsDone():
            raise RuntimeError("BRepPrimAPI_MakeWedge failed")
        shape = maker.Shape()

        # optional translation (default identity)
        if (args.center_x_mm, args.center_y_mm, args.base_z_mm) != (0.0, 0.0, 0.0):
            trsf = gp_Trsf()
            trsf.SetTranslation(
                gp_Vec(args.center_x_mm, args.center_y_mm, args.base_z_mm)
            )
            t = BRepBuilderAPI_Transform(shape, trsf, True)
            t.Build()
            if not t.IsDone():
                raise RuntimeError("Wedge translation failed")
            shape = t.Shape()

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shape), history=history)
