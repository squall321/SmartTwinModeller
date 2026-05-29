"""helical_thread_external — atomic.

Cut an external (male) helical thread on a cylindrical region of the body.
Real swept-helix thread (NOT a decorative groove) — produces a true V-thread
profile that follows a helix around the axis.

Args:
    axis_origin:       tuple (x,y,z) — center of the cylindrical region
    axis_direction:    tuple (x,y,z) — axis direction (normalized internally)
    outer_radius_mm:   the cylinder's outer radius. The thread profile sits
                       so its base touches r=outer_radius and apex points
                       inward (r=outer_radius - thread_height_mm).
    pitch_mm:          distance per turn along the axis
    turns:             number of turns
    thread_height_mm:  radial depth of the V-thread groove
    thread_angle_deg:  V-thread included angle (60° = ISO metric default)

Implementation:
    1. Build a helix as a B-spline interpolated through N≥10 points/turn:
       (R cos t, R sin t, t·pitch/(2π)), t ∈ [0, turns·2π], in local frame
       (axis = +Z, origin = (0,0,0)). The helix wire is the path.
    2. Build a triangular V-profile FACE in the world XZ plane positioned
       at the helix start (x=R, z=0). Profile lies in the plane containing
       the axis and the start-point radial direction.
    3. BRepBuilderAPI_Transform helix + profile to map +Z → axis_direction
       and translate to axis_origin.
    4. BRepOffsetAPI_MakePipe(helix_wire, profile_face) sweeps the V-profile
       along the helix into a solid that approximates the thread material.
    5. BRepAlgoAPI_Cut(body, swept_thread) removes that solid from the body.

Notes:
    - This is the standard "subtract a helical V-prism" approach used by
      most B-rep CAD systems. The resulting cut is a true 3D helical groove
      (not a decorative engraved spiral) — manufacturable on a thread mill
      or single-point lathe.
    - The geometry can be expensive (slow pipe sweep) for many turns or
      large radii; keep test cases small.
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
    radius: float,
    pitch: float,
    turns: float,
    samples_per_turn: int = 16,
):
    """Build a helix as an interpolated B-spline wire in the local frame
    (axis = +Z, origin = (0,0,0)). Helix parameterised as
    (R cos t, R sin t, t·pitch/(2π)), t ∈ [0, turns·2π].

    Returns the TopoDS_Wire.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.gp import gp_Pnt
    from OCP.TColgp import TColgp_HArray1OfPnt

    n = max(int(math.ceil(turns * samples_per_turn)) + 1, 10)
    arr = TColgp_HArray1OfPnt(1, n)
    total_t = turns * 2.0 * math.pi
    for i in range(n):
        t = (i / (n - 1)) * total_t
        x = radius * math.cos(t)
        y = radius * math.sin(t)
        z = t * pitch / (2.0 * math.pi)
        arr.SetValue(i + 1, gp_Pnt(x, y, z))

    interp = GeomAPI_Interpolate(arr, False, 1e-4)
    interp.Perform()
    if not interp.IsDone():
        raise RuntimeError("helix interpolation failed")
    curve = interp.Curve()
    me = BRepBuilderAPI_MakeEdge(curve)
    if not me.IsDone():
        raise RuntimeError("helix MakeEdge failed")
    mw = BRepBuilderAPI_MakeWire()
    mw.Add(me.Edge())
    if not mw.IsDone():
        raise RuntimeError("helix MakeWire failed")
    return mw.Wire()


def _build_v_profile_face_local(
    radius: float,
    thread_height: float,
    thread_angle_deg: float,
    external: bool,
):
    """Build a triangular V-thread profile FACE in the local frame, positioned
    at the helix start. The profile lies in the world XZ plane.

    External thread:
        Base is the segment on x=radius from z=-w/2 to z=+w/2 (width w on the
        cylinder surface), and the apex points INWARD to x=radius-thread_height.

    Internal thread:
        Base on x=radius (inner cylinder surface), apex points OUTWARD to
        x=radius+thread_height.

    w = 2·thread_height·tan(angle/2).

    The profile face's plane normal is +Y (since the profile lies in the XZ
    plane). BRepOffsetAPI_MakePipe will then sweep it along the helix,
    re-orienting the profile to remain perpendicular to the helix tangent at
    each point. The helix's initial tangent at z=0 is (0, R, pitch/(2π))
    normalized — not aligned with +Y exactly, but Pipe's default Frenet-style
    frame handles the alignment.

    Robustness bias:
        The profile base is shifted by `BIAS` (=0.05 mm) away from the cylinder
        surface so it does NOT lie exactly tangent to the body's cylindrical
        boundary. Tangent face-vs-cylinder intersections are numerically
        fragile under BRepAlgoAPI_Cut — without the bias, internal threads
        (apex outward into a concave bore wall) silently fail to subtract.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
    )
    from OCP.gp import gp_Pnt

    BIAS = 0.05   # mm — small embed of base into the cylindrical surface
    w = 2.0 * thread_height * math.tan(math.radians(thread_angle_deg) / 2.0)
    if external:
        # Base slightly OUTSIDE the cylinder (x_base > radius), apex inward.
        x_base = radius + BIAS
        x_apex = radius - thread_height
    else:
        # Base slightly INSIDE the bore (x_base < radius), apex outward into wall.
        x_base = radius - BIAS
        x_apex = radius + thread_height

    # Triangle vertices in XZ plane (y=0).
    p_base_lo = gp_Pnt(x_base, 0.0, -w / 2.0)
    p_base_hi = gp_Pnt(x_base, 0.0,  w / 2.0)
    p_apex    = gp_Pnt(x_apex, 0.0,  0.0)

    # Closed polygon wire.
    poly = BRepBuilderAPI_MakePolygon()
    poly.Add(p_base_lo)
    poly.Add(p_base_hi)
    poly.Add(p_apex)
    poly.Close()
    if not poly.IsDone():
        raise RuntimeError("V-profile polygon failed")
    wire = poly.Wire()

    mf = BRepBuilderAPI_MakeFace(wire, True)
    if not mf.IsDone():
        raise RuntimeError("V-profile face failed")
    return mf.Face()


def _transform_shape_to_axis(
    shape,
    axis_origin: tuple[float, float, float],
    axis_direction: tuple[float, float, float],
):
    """Transform `shape` from local frame (origin=0,0,0; +Z=0,0,1) to target
    frame (origin=axis_origin; +Z=axis_direction)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    # Normalize axis_direction.
    ax, ay, az = axis_direction
    norm = math.sqrt(ax * ax + ay * ay + az * az)
    if norm < 1e-12:
        raise ValueError("axis_direction is zero")
    ax, ay, az = ax / norm, ay / norm, az / norm

    target = gp_Ax3(gp_Pnt(*axis_origin), gp_Dir(ax, ay, az))
    default = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    t = gp_Trsf()
    # SetTransformation(toSys, fromSys): fromSys -> toSys
    t.SetTransformation(target, default)
    xf = BRepBuilderAPI_Transform(shape, t, True)
    xf.Build()
    if not xf.IsDone():
        raise RuntimeError("axis transform failed")
    return xf.Shape()


def _sweep_thread_solid(
    radius: float,
    pitch: float,
    turns: float,
    thread_height: float,
    thread_angle_deg: float,
    axis_origin: tuple[float, float, float],
    axis_direction: tuple[float, float, float],
    external: bool,
):
    """Build the helical V-thread solid in the world frame."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe

    helix_local = _build_helix_wire_local(radius, pitch, turns)
    profile_local = _build_v_profile_face_local(
        radius, thread_height, thread_angle_deg, external,
    )

    pipe = BRepOffsetAPI_MakePipe(helix_local, profile_local)
    pipe.Build()
    if not pipe.IsDone():
        raise RuntimeError("helical thread pipe sweep failed")
    swept_local = pipe.Shape()

    return _transform_shape_to_axis(swept_local, axis_origin, axis_direction)


@skill(
    name="helical_thread_external",
    category="modify/pocket",
    level="atomic",
    summary="Cut a real external (male) helical V-thread on a cylindrical region "
            "of the body. Builds a helix wire, sweeps a triangular V-profile "
            "along it, and subtracts. NOT a decorative engraving — produces "
            "a true 3D thread groove manufacturable on a thread mill or lathe.",
    selector_kinds=[],
    history_rules={
        "thread_flanks": HistoryRule.GENERATED_NEW,
        "thread_root":   HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.thread_radius_positive",
        "pc.thread_pitch_positive",
    ],
    produces_features=["external_thread", "helical_groove"],
    preserves=["axis_envelope"],
    manufacturing={
        "cnc_5axis":   {"min_wall_mm": 0.5, "extras": {"thread_mill_required": True}},
        "turning":     {"min_wall_mm": 0.3, "extras": {"single_point": True}},
    },
    failure_modes=[
        "fm.helix_intersects_itself_at_steep_pitch",
        "fm.thread_height_exceeds_radius",
    ],
    cost_hint=0.55,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class HelicalThreadExternal(SkillBase):
    class Args(BaseModel):
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        outer_radius_mm: float = Field(gt=0.0, le=200.0)
        pitch_mm: float = Field(gt=0.0, le=50.0)
        turns: float = Field(gt=0.0, le=200.0)
        thread_height_mm: float = Field(gt=0.0, le=20.0)
        thread_angle_deg: float = Field(default=60.0, gt=0.0, lt=180.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        if args.thread_height_mm >= args.outer_radius_mm:
            raise ValueError(
                f"helical_thread_external: thread_height_mm ({args.thread_height_mm}) "
                f">= outer_radius_mm ({args.outer_radius_mm}); apex would cross axis"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body

        thread_solid = _sweep_thread_solid(
            radius=args.outer_radius_mm,
            pitch=args.pitch_mm,
            turns=args.turns,
            thread_height=args.thread_height_mm,
            thread_angle_deg=args.thread_angle_deg,
            axis_origin=args.axis_origin,
            axis_direction=args.axis_direction,
            external=True,
        )

        cut = BRepAlgoAPI_Cut(shape, thread_solid)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("helical_thread_external cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "thread_flanks":   HistoryRule.GENERATED_NEW,
                "thread_root":     HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
