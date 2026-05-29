"""coil_spring_rectangular — atomic create skill.

Solid coil spring formed by sweeping a RECTANGULAR wire cross-section along a
helical path. Geometry is identical to `helical_spring` except the cross-
section is a `wire_width_mm × wire_thickness_mm` rectangle instead of a
circle.

Profile orientation (so the rectangle "stands up" naturally on the coil):
    - `wire_thickness_mm` extends in the RADIAL direction (from helix axis
      outward), so the inside / outside coil radii sit at coil_radius ± t/2.
    - `wire_width_mm` extends in the direction perpendicular to BOTH the
      radial and the tangent vectors — i.e. roughly along the helix axis.
      (This matches how flat rectangular springs are typically wound — the
      flat face faces the axial direction.)

Implementation mirrors `helical_spring`: helix wire (≥16 pts/turn) → planar
rectangular profile wire → BRepOffsetAPI_MakePipe → world transform.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _build_helix_wire_local(
    coil_radius: float,
    pitch: float,
    turns: float,
    samples_per_turn: int = 16,
):
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.gp import gp_Pnt
    from OCP.TColgp import TColgp_HArray1OfPnt

    n = max(int(math.ceil(turns * samples_per_turn)) + 1, 16)
    arr = TColgp_HArray1OfPnt(1, n)
    total_t = turns * 2.0 * math.pi
    for i in range(n):
        t = (i / (n - 1)) * total_t
        x = coil_radius * math.cos(t)
        y = coil_radius * math.sin(t)
        z = t * pitch / (2.0 * math.pi)
        arr.SetValue(i + 1, gp_Pnt(x, y, z))

    interp = GeomAPI_Interpolate(arr, False, 1e-5)
    interp.Perform()
    if not interp.IsDone():
        raise RuntimeError("coil_spring_rectangular: helix interpolation failed")
    curve = interp.Curve()
    me = BRepBuilderAPI_MakeEdge(curve)
    if not me.IsDone():
        raise RuntimeError("coil_spring_rectangular: helix edge failed")
    mw = BRepBuilderAPI_MakeWire()
    mw.Add(me.Edge())
    if not mw.IsDone():
        raise RuntimeError("coil_spring_rectangular: helix wire failed")
    return mw.Wire()


def _build_rect_profile_wire_local(
    coil_radius: float,
    wire_width: float,
    wire_thickness: float,
    pitch: float,
):
    """Build a closed rectangular wire (4 edges) in the plane perpendicular to
    the helix tangent at t=0, centered at (coil_radius, 0, 0).

    Local helix tangent at t=0: T = (0, coil_r, pitch/(2π)) normalized.
    Radial direction at t=0:   R = (1, 0, 0)  (from helix axis outward).
    B = T × R (right-handed; roughly axial / perpendicular to both).

    Rectangle vertices (in profile plane), starting from +R+B corner, going CCW:
        v1 = center + (t/2)R + (w/2)B
        v2 = center - (t/2)R + (w/2)B
        v3 = center - (t/2)R - (w/2)B
        v4 = center + (t/2)R - (w/2)B
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    tx, ty, tz = 0.0, coil_radius, pitch / (2.0 * math.pi)
    tn = math.sqrt(tx * tx + ty * ty + tz * tz)
    if tn < 1e-12:
        tx, ty, tz = 0.0, 0.0, 1.0
    else:
        tx, ty, tz = tx / tn, ty / tn, tz / tn

    rx, ry, rz = 1.0, 0.0, 0.0
    # B = T × R
    bx = ty * rz - tz * ry
    by = tz * rx - tx * rz
    bz = tx * ry - ty * rx
    bn = math.sqrt(bx * bx + by * by + bz * bz)
    if bn < 1e-12:
        raise RuntimeError("coil_spring_rectangular: degenerate profile basis")
    bx, by, bz = bx / bn, by / bn, bz / bn

    cx, cy, cz = coil_radius, 0.0, 0.0
    hw = wire_width / 2.0
    ht = wire_thickness / 2.0

    def corner(sr: float, sb: float) -> gp_Pnt:
        return gp_Pnt(
            cx + sr * ht * rx + sb * hw * bx,
            cy + sr * ht * ry + sb * hw * by,
            cz + sr * ht * rz + sb * hw * bz,
        )

    v1 = corner(+1.0, +1.0)
    v2 = corner(-1.0, +1.0)
    v3 = corner(-1.0, -1.0)
    v4 = corner(+1.0, -1.0)

    poly = BRepBuilderAPI_MakePolygon()
    poly.Add(v1)
    poly.Add(v2)
    poly.Add(v3)
    poly.Add(v4)
    poly.Close()
    poly.Build()
    if not poly.IsDone():
        raise RuntimeError("coil_spring_rectangular: rectangle profile polygon failed")
    return poly.Wire()


def _transform_shape_to_axis(
    shape,
    axis_origin: tuple[float, float, float],
    axis_direction: tuple[float, float, float],
):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    ax, ay, az = axis_direction
    n = math.sqrt(ax * ax + ay * ay + az * az)
    if n < 1e-12:
        raise ValueError("axis_direction is zero")
    ax, ay, az = ax / n, ay / n, az / n

    target = gp_Ax3(gp_Pnt(*axis_origin), gp_Dir(ax, ay, az))
    default = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    t = gp_Trsf()
    t.SetTransformation(target, default)
    xf = BRepBuilderAPI_Transform(shape, t, True)
    xf.Build()
    if not xf.IsDone():
        raise RuntimeError("coil_spring_rectangular: axis transform failed")
    return xf.Shape()


@skill(
    name="coil_spring_rectangular",
    category="create",
    level="atomic",
    summary="Solid coil spring with RECTANGULAR wire cross-section "
            "(width × thickness) swept along a helix. Rectangle thickness is "
            "radial; width is axial.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["coil_spring_solid", "rect_helix_sweep"],
    preserves=[],
    manufacturing={
        "wire_forming":      {"min_wall_mm": 0.2,
                              "extras": {"requires_flat_wire_coiler": True}},
        "additive_metal":    {"min_wall_mm": 0.3},
    },
    failure_modes=[
        "fm.spring_thickness_exceeds_coil_radius",
        "fm.spring_self_intersection",
    ],
    cost_hint=0.45,
    post_conditions=[PostCondition(kind="body_present")],
)
class CoilSpringRectangular(SkillBase):
    class Args(BaseModel):
        coil_radius_mm: float = Field(gt=0)
        wire_width_mm: float = Field(gt=0)
        wire_thickness_mm: float = Field(gt=0)
        pitch_mm: float = Field(gt=0)
        turns: float = Field(gt=0)
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
        from build123d import Part

        if args.wire_thickness_mm >= 2.0 * args.coil_radius_mm:
            raise ValueError(
                f"coil_spring_rectangular: wire_thickness_mm "
                f"({args.wire_thickness_mm}) >= 2·coil_radius_mm "
                f"({2 * args.coil_radius_mm}); inner edge would cross axis"
            )
        if args.pitch_mm < args.wire_width_mm:
            raise ValueError(
                f"coil_spring_rectangular: pitch_mm ({args.pitch_mm}) must be "
                f"≥ wire_width_mm ({args.wire_width_mm}) so adjacent coils "
                f"do not overlap"
            )

        helix = _build_helix_wire_local(
            args.coil_radius_mm, args.pitch_mm, args.turns,
        )
        profile = _build_rect_profile_wire_local(
            args.coil_radius_mm,
            args.wire_width_mm,
            args.wire_thickness_mm,
            args.pitch_mm,
        )

        pipe = BRepOffsetAPI_MakePipe(helix, profile)
        pipe.Build()
        if not pipe.IsDone():
            raise RuntimeError("coil_spring_rectangular: pipe sweep failed")
        swept_local = pipe.Shape()

        shape = _transform_shape_to_axis(
            swept_local, args.axis_origin, args.axis_direction,
        )

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shape), history=history)
