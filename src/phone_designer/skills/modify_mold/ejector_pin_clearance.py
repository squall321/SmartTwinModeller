"""ejector_pin_clearance — atomic. Drill ejector pin holes at specified XY
positions (typically on the cavity-side face).

For each (x, y) in `pin_positions`, build a cylinder of `pin_diameter_mm`
diameter, `pin_depth_mm` deep, with its top at z = z_start_mm, then subtract
it from the body via BRepAlgoAPI_Cut.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


@skill(
    name="ejector_pin_clearance",
    category="modify/mold",
    level="atomic",
    summary="Drill ejector pin holes (cylindrical clearances) at the given "
            "XY positions on the cavity-side face. Each pin is a cylinder of "
            "pin_diameter_mm diameter, pin_depth_mm deep, with its top at "
            "z = z_start_mm, removed from the body via boolean cut.",
    selector_kinds=[],
    history_rules={},
    produces_features=["ejector_pin_holes"],
    preserves=["outer_envelope"],
    manufacturing={
        "die_cast_al":       {"min_wall_mm": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8},
    },
    failure_modes=["fm.pin_outside_body", "fm.boolean_cut_failed"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class EjectorPinClearance(SkillBase):
    class Args(BaseModel):
        pin_positions: list[tuple[float, float]]
        pin_diameter_mm: float = Field(default=3.0, gt=0.0, le=50.0)
        pin_depth_mm: float = Field(default=5.0, gt=0.0, le=200.0)
        z_start_mm: float

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        if not args.pin_positions:
            raise ValueError("ejector_pin_clearance: pin_positions is empty")

        shape = _occt_shape(body)
        if shape is None:
            raise RuntimeError("ejector_pin_clearance: body is None")

        radius = args.pin_diameter_mm / 2.0
        depth = args.pin_depth_mm
        # Cylinder grows along +Z; we want its top at z_start_mm so base is
        # z_start_mm - depth.
        z_base = args.z_start_mm - depth

        result_shape = shape
        drilled = 0
        for (x, y) in args.pin_positions:
            axis = gp_Ax2(gp_Pnt(x, y, z_base), gp_Dir(0.0, 0.0, 1.0))
            cyl = BRepPrimAPI_MakeCylinder(axis, radius, depth).Shape()
            cut = BRepAlgoAPI_Cut(result_shape, cyl)
            cut.Build()
            if not cut.IsDone():
                raise RuntimeError(
                    f"ejector_pin_clearance: cut failed at ({x}, {y})"
                )
            result_shape = cut.Shape()
            drilled += 1

        return SkillResult(
            body=Part(result_shape),
            history=EntityHistoryMap(),
        )
