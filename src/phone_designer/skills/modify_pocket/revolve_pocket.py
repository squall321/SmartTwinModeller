"""revolve_pocket — atomic.

Revolve a 2D profile around an arbitrary axis, subtracting the resulting
solid of revolution from the body. Used for annular grooves, internal O-ring
seats, lathe-style profile removals.

Args:
    profile_sketch: SketchSpec       — 2D profile in local XY
    axis_origin:    tuple (x,y,z)    — world point on the revolution axis
    axis_direction: tuple (x,y,z)    — world axis direction (normalized internally)
    angle_deg:      float = 360.0    — sweep angle
    floor_blend_r_mm: float = 0.0    — fillet edges near the profile-center Z plane

Convention (profile orientation):
    The profile is given in its own local XY plane. Before revolution, it is
    rotated so local-X -> world-X and local-Y -> world-Z (i.e. mapped onto
    the world XZ half-plane). For a Z-axis revolution (axis through origin)
    this means the profile must have x >= 0 (it lies on the +x half of the
    XZ plane, which contains the Z axis). After this implicit XZ placement,
    `axis_origin` is applied as a translation. The result is revolved around
    `axis_direction` through `axis_origin`.

    LLM users: if you want an annular groove on top of a box centered at the
    origin, pass a small rectangle profile like
        {kind: rectangle, length_mm: 1, width_mm: 1, center_x_mm: 8}
    with axis_origin=(0,0,box_top_z) and axis_direction=(0,0,1).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec


def _orient_profile_to_world_xz(face):
    """Rotate face from local XY (+Z normal) to world XZ.

    Mapping: local x -> world x, local y -> world z, local z -> world -y.
    Equivalent to a +90 deg rotation around the world X axis (right-handed).
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)), math.pi / 2.0)
    xf = BRepBuilderAPI_Transform(face, t, True)
    xf.Build()
    if not xf.IsDone():
        raise RuntimeError("revolve profile orientation rotation failed")
    return xf.Shape()


def _translate_shape(shape, dx: float, dy: float, dz: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(dx, dy, dz))
    xf = BRepBuilderAPI_Transform(shape, t, True)
    xf.Build()
    if not xf.IsDone():
        raise RuntimeError("revolve profile translate failed")
    return xf.Shape()


def _build_revolved_solid(
    profile_sketch,
    axis_origin: tuple[float, float, float],
    axis_direction: tuple[float, float, float],
    angle_deg: float,
):
    """Build a TopoDS_Shape (solid) by revolving `profile_sketch` around the
    axis (axis_origin, axis_direction) by `angle_deg` degrees.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt

    from phone_designer.skills.modify_pocket._sketch_to_solid import (
        _build_planar_face,
    )

    # 1. profile face in local XY
    face = _build_planar_face(profile_sketch)
    # 2. local XY -> world XZ
    face = _orient_profile_to_world_xz(face)
    # 3. translate by axis_origin
    if axis_origin != (0.0, 0.0, 0.0):
        face = _translate_shape(face, axis_origin[0], axis_origin[1], axis_origin[2])

    # 4. revolve
    axis = gp_Ax1(gp_Pnt(*axis_origin), gp_Dir(*axis_direction))
    angle_rad = math.radians(angle_deg)
    mk = BRepPrimAPI_MakeRevol(face, axis, angle_rad, True)
    # Note: copy=True keeps `face` untouched. The 3-arg form is also valid.
    if not mk.IsDone():
        raise RuntimeError("BRepPrimAPI_MakeRevol failed")
    return mk.Shape()


@skill(
    name="revolve_pocket",
    category="modify/pocket",
    level="atomic",
    summary="Revolve a 2D profile around an arbitrary axis and subtract the "
            "resulting solid of revolution from the body. Standard pattern for "
            "annular grooves, internal O-ring seats, lathe-style cuts.",
    selector_kinds=[],
    history_rules={
        "revolve_walls":  HistoryRule.GENERATED_NEW,
        "revolve_floor":  HistoryRule.GENERATED_NEW,
        "revolve_blends": HistoryRule.GENERATED_NEW,
    },
    preconditions=[],
    produces_features=["pocket", "annular_groove"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":   {"min_wall_mm": 0.5},
        "die_cast_al": {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "turning":     {"min_wall_mm": 0.3},
    },
    failure_modes=[
        "fm.profile_crosses_axis",
        "fm.revolved_solid_misses_body",
    ],
    cost_hint=0.30,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class RevolvePocket(SkillBase):
    class Args(BaseModel):
        profile_sketch: SketchSpec
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        angle_deg: float = Field(default=360.0, gt=0.0, le=360.0)
        floor_blend_r_mm: float = Field(default=0.0, ge=0.0, le=10.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        from phone_designer.skills.modify_pocket.extrude_pocket_blended import (
            _fillet_edges_near_z,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body

        revolved = _build_revolved_solid(
            args.profile_sketch,
            args.axis_origin,
            args.axis_direction,
            args.angle_deg,
        )

        cut = BRepAlgoAPI_Cut(shape, revolved)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("revolve_pocket cut failed")
        new_shape = cut.Shape()

        if args.floor_blend_r_mm > 0:
            # Floor of the groove ~ axis_origin.z + profile center_y_mm
            # (profile y -> world z after orientation).
            center_y = getattr(args.profile_sketch, "center_y_mm", 0.0)
            target_z = args.axis_origin[2] + center_y
            new_shape, _ = _fillet_edges_near_z(
                new_shape, target_z, args.floor_blend_r_mm,
            )

        history = EntityHistoryMap(
            rules={
                "revolve_walls":  HistoryRule.GENERATED_NEW,
                "revolve_floor":  HistoryRule.GENERATED_NEW,
                "revolve_blends": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
