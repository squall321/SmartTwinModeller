"""sheet_base — atomic create skill.

Flat sheet aligned with the world XY plane. Bottom face at z = center_z - 0
(or wherever the caller positions it; default Z=0 is the bottom). Length is X,
width is Y, thickness is Z. The starting point for every other sheet-metal
skill in this package.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="sheet_base",
    category="create",
    level="atomic",
    summary="Flat sheet-metal blank (length x width x thickness). XY-centered "
            "by default, bottom at center_z_mm (default Z=0).",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["sheet_blank"],
    preserves=[],
    manufacturing={
        "sheet_metal_stamp": {"min_wall_mm": 0.3},
        "cnc_3axis":         {"min_wall_mm": 0.5},
    },
    failure_modes=["zero_or_negative_dimension"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class SheetBase(SkillBase):
    class Args(BaseModel):
        length_mm: float = Field(gt=0)
        width_mm: float = Field(gt=0)
        thickness_mm: float = Field(gt=0)
        center_x_mm: float = 0.0
        center_y_mm: float = 0.0
        center_z_mm: float = 0.0

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt, gp_Trsf, gp_Vec

        # Build at (-L/2, -W/2, 0)..(+L/2, +W/2, t); then translate by center_*.
        L, W, T = args.length_mm, args.width_mm, args.thickness_mm
        corner = gp_Pnt(-L / 2.0, -W / 2.0, 0.0)
        maker = BRepPrimAPI_MakeBox(corner, L, W, T)
        maker.Build()
        if not maker.IsDone():
            raise RuntimeError("sheet_base: BRepPrimAPI_MakeBox failed")
        shape = maker.Shape()

        if (args.center_x_mm, args.center_y_mm, args.center_z_mm) != (0.0, 0.0, 0.0):
            t = gp_Trsf()
            t.SetTranslation(
                gp_Vec(args.center_x_mm, args.center_y_mm, args.center_z_mm)
            )
            xf = BRepBuilderAPI_Transform(shape, t, True)
            xf.Build()
            if not xf.IsDone():
                raise RuntimeError("sheet_base: translation failed")
            shape = xf.Shape()

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shape), history=history)
