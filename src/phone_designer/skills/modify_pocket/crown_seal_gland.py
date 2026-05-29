"""crown_seal_gland — atomic. Cylindrical O-ring groove around a crown shaft.

Cuts a coaxial annular groove around the crown shaft of a watch/wearable
crown so that an O-ring seated between the shaft and the bore seals the
crown opening. The groove is on the bore wall (inside the housing): a
shallow ring of inner diameter gland_id_mm and outer diameter gland_od_mm
centered axially at a depth = gland_depth_mm below the outer surface.

Geometry tool (relative to axis_origin, along axis_direction):
  outer_cyl = Cylinder(d = gland_od_mm, h = gland_depth_mm)
  inner_cyl = Cylinder(d = gland_id_mm, h = gland_depth_mm + slop)
  ring_tool = outer_cyl - inner_cyl                  (annulus)

The annular tool is positioned with its near end at axis_origin and grows
along axis_direction. Subtracting from the body opens a ring-shaped relief
in the bore wall.

Args:
    axis_origin:        (x, y, z) point on the crown axis, at the *outer*
                        surface of the housing (entry of the bore).
    axis_direction:     unit-ish (x, y, z) — direction *into* the housing.
    shaft_diameter_mm:  nominal crown shaft diameter (informational; the
                        catalog-free pocket form uses gland_id_mm directly).
    gland_id_mm:        groove inner diameter (≈ shaft + clearance).
    gland_od_mm:        groove outer diameter (= bore + ring squeeze).
    gland_depth_mm:     groove axial depth (axial width of the ring relief).

Default values follow a small-watch crown shaft ≈ 2.5 mm with a small AS568
ring (cs ≈ 0.5 mm).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, model_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = v
    n = math.sqrt(x * x + y * y + z * z)
    if n < 1e-12:
        raise ValueError("axis_direction is zero")
    return x / n, y / n, z / n


@skill(
    name="crown_seal_gland",
    category="modify/pocket",
    level="atomic",
    summary="Cylindrical O-ring gland (annular relief) cut around a crown shaft "
            "bore so that a ring squeezed between the shaft and the bore wall "
            "seals the crown opening. Coaxial with the given axis.",
    selector_kinds=[],
    history_rules={
        "gland_walls":     HistoryRule.GENERATED_NEW,
        "gland_floor":     HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.axis_direction_nonzero",
        "pc.gland_od_greater_than_id",
    ],
    produces_features=["o_ring_groove", "crown_seal", "annular_groove"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":   {"min_wall_mm": 0.3, "min_fillet_r_mm": 0.2},
        "turning":     {"min_wall_mm": 0.3,
                        "extras": {"internal_groove_tool": True}},
    },
    failure_modes=[
        "fm.gland_misses_body",
        "fm.gland_id_smaller_than_shaft",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class CrownSealGland(SkillBase):
    class Args(BaseModel):
        axis_origin: tuple[float, float, float]
        axis_direction: tuple[float, float, float]
        shaft_d_mm: float = Field(default=2.5, gt=0, le=10,
                                   description="nominal crown shaft (informational)")
        gland_id_mm: float = Field(default=2.6, gt=0, le=15,
                                    description="groove inner diameter (≈ shaft + clearance)")
        gland_od_mm: float = Field(default=3.6, gt=0, le=20,
                                    description="groove outer diameter (sets squeeze on the ring)")
        gland_d_mm: float = Field(default=0.85, gt=0, le=5,
                                   description="groove axial depth")

        @model_validator(mode="after")
        def _od_greater_than_id(self):
            if self.gland_od_mm <= self.gland_id_mm:
                raise ValueError(
                    f"gland_od_mm ({self.gland_od_mm}) must be > gland_id_mm "
                    f"({self.gland_id_mm})"
                )
            if self.gland_id_mm < self.shaft_d_mm:
                raise ValueError(
                    f"gland_id_mm ({self.gland_id_mm}) must be >= "
                    f"shaft_d_mm ({self.shaft_d_mm})"
                )
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        dx, dy, dz = _normalize(args.axis_direction)
        origin = gp_Pnt(*args.axis_origin)
        direction = gp_Dir(dx, dy, dz)
        axis = gp_Ax2(origin, direction)

        # Outer cylinder — defines the OD of the gland (large solid).
        outer_mk = BRepPrimAPI_MakeCylinder(
            axis, args.gland_od_mm / 2.0, args.gland_d_mm,
        )
        outer_mk.Build()
        if not outer_mk.IsDone():
            raise RuntimeError("crown_seal_gland: outer cylinder build failed")
        outer_shape = outer_mk.Shape()

        # Inner cylinder — slightly longer so the subtraction cleanly pierces
        # both ends. Anchored a hair before axis_origin (along -direction).
        slop = 1.0
        # Move origin back by slop along -direction so the inner cyl extends
        # past both faces of the outer cyl.
        inner_origin = gp_Pnt(
            args.axis_origin[0] - slop * dx,
            args.axis_origin[1] - slop * dy,
            args.axis_origin[2] - slop * dz,
        )
        inner_axis = gp_Ax2(inner_origin, direction)
        inner_mk = BRepPrimAPI_MakeCylinder(
            inner_axis, args.gland_id_mm / 2.0,
            args.gland_d_mm + 2.0 * slop,
        )
        inner_mk.Build()
        if not inner_mk.IsDone():
            raise RuntimeError("crown_seal_gland: inner cylinder build failed")
        inner_shape = inner_mk.Shape()

        # Annular ring tool = outer - inner.
        ring_cut = BRepAlgoAPI_Cut(outer_shape, inner_shape)
        ring_cut.Build()
        if not ring_cut.IsDone():
            raise RuntimeError("crown_seal_gland: ring tool build failed")
        ring_tool = ring_cut.Shape()

        # Body - ring.
        cut = BRepAlgoAPI_Cut(shape, ring_tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("crown_seal_gland: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "gland_walls":     HistoryRule.GENERATED_NEW,
                "gland_floor":     HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
