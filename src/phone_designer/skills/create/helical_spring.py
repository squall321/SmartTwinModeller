"""helical_spring — atomic create skill.

Solid coil spring formed by sweeping a CIRCULAR wire cross-section along a
helical path.

Helix parameterization (in the local frame, axis = +Z, origin = (0,0,0)):
    x(t) = coil_radius · cos(t)
    y(t) = coil_radius · sin(t)
    z(t) = t · pitch / (2π)
    t ∈ [0, 2π · turns]

The helix wire is built by interpolating a B-spline through ≥16 samples per
turn (mirroring the pattern used by `helical_thread_external`).

The cross-section is a circular wire of radius `wire_radius_mm` placed at the
helix start point and oriented perpendicular to the helix tangent at that
point (this is what `BRepOffsetAPI_MakePipe` expects for a smooth sweep).

The entire helix + cross-section is built in the local frame, the pipe is
swept locally, and the resulting solid is then transformed so that the helix
axis (local +Z) maps to `axis_direction` at `axis_origin`.
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
    """Helix wire as a B-spline interpolated through N≥16 pts/turn (local frame)."""
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
        raise RuntimeError("helical_spring: helix interpolation failed")
    curve = interp.Curve()

    me = BRepBuilderAPI_MakeEdge(curve)
    if not me.IsDone():
        raise RuntimeError("helical_spring: helix edge failed")
    mw = BRepBuilderAPI_MakeWire()
    mw.Add(me.Edge())
    if not mw.IsDone():
        raise RuntimeError("helical_spring: helix wire failed")
    return mw.Wire()


def _build_circular_profile_wire_local(
    coil_radius: float,
    wire_radius: float,
    pitch: float,
):
    """Circular profile wire of radius `wire_radius` placed at the helix start
    (coil_radius, 0, 0), with its plane normal aligned with the helix tangent
    at t=0.

    Helix tangent at t=0:
        dr/dt = (-coil_radius·sin t, coil_radius·cos t, pitch/2π)
        at t=0: (0, coil_radius, pitch/(2π))  (un-normalized)
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    tx, ty, tz = 0.0, coil_radius, pitch / (2.0 * math.pi)
    tn = math.sqrt(tx * tx + ty * ty + tz * tz)
    if tn < 1e-12:
        # Degenerate helix (zero radius and zero pitch) — fall back to +Z.
        tx, ty, tz = 0.0, 0.0, 1.0
    else:
        tx, ty, tz = tx / tn, ty / tn, tz / tn

    center = gp_Pnt(coil_radius, 0.0, 0.0)
    axis = gp_Ax2(center, gp_Dir(tx, ty, tz))
    circ = gp_Circ(axis, wire_radius)
    me = BRepBuilderAPI_MakeEdge(circ)
    me.Build()
    if not me.IsDone():
        raise RuntimeError("helical_spring: profile circle edge failed")
    mw = BRepBuilderAPI_MakeWire()
    mw.Add(me.Edge())
    mw.Build()
    if not mw.IsDone():
        raise RuntimeError("helical_spring: profile circle wire failed")
    return mw.Wire()


def _transform_shape_to_axis(
    shape,
    axis_origin: tuple[float, float, float],
    axis_direction: tuple[float, float, float],
):
    """Map local frame (origin=0,0,0; +Z=0,0,1) → target axis."""
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
        raise RuntimeError("helical_spring: axis transform failed")
    return xf.Shape()


@skill(
    name="helical_spring",
    category="create",
    level="atomic",
    summary="Solid coil spring: circular wire cross-section swept along a helix. "
            "Standard kinematic parameters (coil radius, wire radius, pitch, turns).",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["coil_spring_solid", "helix_sweep"],
    preserves=[],
    manufacturing={
        "wire_forming":      {"min_wall_mm": 0.2,
                              "extras": {"requires_coiling_machine": True}},
        "additive_metal":    {"min_wall_mm": 0.3},
    },
    failure_modes=[
        "fm.spring_wire_exceeds_coil_radius",
        "fm.spring_self_intersection",
    ],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="body_present")],
)
class HelicalSpring(SkillBase):
    class Args(BaseModel):
        coil_radius_mm: float = Field(gt=0)
        wire_radius_mm: float = Field(gt=0)
        pitch_mm: float = Field(gt=0)
        turns: float = Field(gt=0)
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
        from build123d import Part

        if args.wire_radius_mm >= args.coil_radius_mm:
            raise ValueError(
                f"helical_spring: wire_radius_mm ({args.wire_radius_mm}) must be "
                f"< coil_radius_mm ({args.coil_radius_mm}) to avoid self-intersection"
            )
        # Coarse adjacent-coil collision guard.
        if args.pitch_mm < 2.0 * args.wire_radius_mm:
            raise ValueError(
                f"helical_spring: pitch_mm ({args.pitch_mm}) must be ≥ "
                f"2·wire_radius_mm ({2 * args.wire_radius_mm}) so adjacent "
                f"coils do not overlap"
            )

        helix = _build_helix_wire_local(
            args.coil_radius_mm, args.pitch_mm, args.turns,
        )
        profile = _build_circular_profile_wire_local(
            args.coil_radius_mm, args.wire_radius_mm, args.pitch_mm,
        )

        pipe = BRepOffsetAPI_MakePipe(helix, profile)
        pipe.Build()
        if not pipe.IsDone():
            raise RuntimeError("helical_spring: pipe sweep failed")
        swept_local = pipe.Shape()

        shape = _transform_shape_to_axis(
            swept_local, args.axis_origin, args.axis_direction,
        )

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shape), history=history)
