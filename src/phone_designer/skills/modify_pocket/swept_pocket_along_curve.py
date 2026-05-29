"""swept_pocket_along_curve — atomic.

Sweep a 2D profile sketch along an arbitrary 3D polyline or B-spline path,
subtracting the resulting tube from the body to create a curved channel/pocket.

Use cases: cable channels, cooling fluid passages, decorative grooves that
follow a curve, ergonomic finger groove on the back of a phone, etc.

Args:
    face_selector: SelectorRef       — face where the pocket starts (planar ±Z only for v0)
    profile_sketch: SketchSpec       — 2D cross-section (any SketchSpec kind)
    path_points: list of 3D points   — ≥2 world-coord points; first should be near
                                       the chosen face's center
    path_type:    "polyline" | "bspline"
    floor_blend_r_mm: float = 0.0    — fillet edges at the path-end Z plane

Convention:
    The profile is built in its own local XY plane (Z=0, +Z normal). The skill
    transforms it so its origin sits at path_points[0] and its +Z axis aligns
    with the initial tangent of the path. BRepOffsetAPI_MakePipe then sweeps
    the profile face along the path wire to produce the cut solid.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec


def _build_path_wire(
    points: list[tuple[float, float, float]],
    path_type: Literal["polyline", "bspline"],
):
    """Build a TopoDS_Wire from 3D points (polyline of MakeEdge or interpolated B-spline)."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.gp import gp_Pnt

    if len(points) < 2:
        raise ValueError("path_points must contain >= 2 points")

    if path_type == "polyline":
        mw = BRepBuilderAPI_MakeWire()
        for i in range(len(points) - 1):
            p1 = gp_Pnt(*points[i])
            p2 = gp_Pnt(*points[i + 1])
            if p1.Distance(p2) < 1e-9:
                continue
            me = BRepBuilderAPI_MakeEdge(p1, p2)
            if not me.IsDone():
                raise RuntimeError(f"MakeEdge(polyline seg {i}) failed")
            mw.Add(me.Edge())
        if not mw.IsDone():
            raise RuntimeError("MakeWire(polyline) failed")
        return mw.Wire()

    if path_type == "bspline":
        from OCP.GeomAPI import GeomAPI_Interpolate
        from OCP.TColgp import TColgp_HArray1OfPnt

        arr = TColgp_HArray1OfPnt(1, len(points))
        for i, (x, y, z) in enumerate(points, start=1):
            arr.SetValue(i, gp_Pnt(x, y, z))
        interp = GeomAPI_Interpolate(arr, False, 1e-6)
        interp.Perform()
        if not interp.IsDone():
            raise RuntimeError("GeomAPI_Interpolate(path) failed")
        curve = interp.Curve()
        me = BRepBuilderAPI_MakeEdge(curve)
        if not me.IsDone():
            raise RuntimeError("MakeEdge(bspline path) failed")
        mw = BRepBuilderAPI_MakeWire()
        mw.Add(me.Edge())
        if not mw.IsDone():
            raise RuntimeError("MakeWire(bspline) failed")
        return mw.Wire()

    raise ValueError(f"unknown path_type: {path_type}")


def _initial_tangent(
    points: list[tuple[float, float, float]],
    path_type: Literal["polyline", "bspline"],
) -> tuple[float, float, float]:
    """Initial tangent direction at points[0]. For polyline: p1-p0. For bspline:
    same approximation (first chord) — close enough for orienting the profile
    so the sweep starts cleanly at the host face."""
    p0 = points[0]
    p1 = points[1]
    dx, dy, dz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
    norm = math.sqrt(dx * dx + dy * dy + dz * dz)
    if norm < 1e-12:
        return (0.0, 0.0, 1.0)
    return (dx / norm, dy / norm, dz / norm)


def _transform_face_to_frame(
    face,
    origin: tuple[float, float, float],
    new_z: tuple[float, float, float],
):
    """Transform `face` from local (origin=0,0,0; +Z=0,0,1) to target frame
    (origin=`origin`; +Z=`new_z`)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    target = gp_Ax3(gp_Pnt(*origin), gp_Dir(*new_z))
    # SetTransformation(ax3) maps `ax3` -> the global frame; we want the
    # inverse: global (local) -> target. Build that by setting from target
    # to default and then Invert(). Per OCCT docs, SetTransformation(toSys, fromSys)
    # does fromSys -> toSys.
    default = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    t = gp_Trsf()
    t.SetTransformation(target, default)
    xf = BRepBuilderAPI_Transform(face, t, True)
    xf.Build()
    if not xf.IsDone():
        raise RuntimeError("BRepBuilderAPI_Transform(profile face) failed")
    return xf.Shape()


@skill(
    name="swept_pocket_along_curve",
    category="modify/pocket",
    level="atomic",
    summary="Sweep a 2D profile along a 3D polyline or B-spline path and "
            "subtract the resulting tube from the body. Used for curved "
            "channels, ergonomic finger grooves, cable trays, decorative "
            "grooves that follow a free-form curve.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":   HistoryRule.MODIFIED_INHERIT,
        "swept_walls":   HistoryRule.GENERATED_NEW,
        "swept_floor":   HistoryRule.GENERATED_NEW,
        "swept_blends":  HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
    ],
    produces_features=["pocket", "swept_channel"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_5axis":         {"min_wall_mm": 0.5, "extras": {"requires_5axis": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.sweep_self_intersect",
        "fm.path_exits_body",
        "fm.profile_too_large_for_curvature",
    ],
    cost_hint=0.35,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class SweptPocketAlongCurve(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        profile_sketch: SketchSpec
        path_points: list[tuple[float, float, float]] = Field(min_length=2)
        path_type: Literal["polyline", "bspline"] = "polyline"
        floor_blend_r_mm: float = Field(default=0.0, ge=0.0, le=10.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _build_planar_face,
            _face_plane_normal,
        )
        from phone_designer.skills.modify_pocket.extrude_pocket_blended import (
            _fillet_edges_near_z,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"swept_pocket_along_curve: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        # Validate planar ±Z face. (We don't actually need the normal value
        # itself for the sweep, but the v0 contract restricts host faces.)
        _center, normal = _face_plane_normal(target_face)
        if abs(normal[2]) <= 0.95:
            raise NotImplementedError(
                "swept_pocket_along_curve v0: host face must be planar ±Z"
            )

        # 1. profile face in local XY (z=0, +Z normal)
        profile_face = _build_planar_face(args.profile_sketch)

        # 2. transform profile to start of path with +Z aligned to initial tangent
        origin = args.path_points[0]
        tangent = _initial_tangent(args.path_points, args.path_type)
        oriented_face = _transform_face_to_frame(profile_face, origin, tangent)

        # 3. path wire
        path_wire = _build_path_wire(args.path_points, args.path_type)

        # 4. sweep
        pipe = BRepOffsetAPI_MakePipe(path_wire, oriented_face)
        pipe.Build()
        if not pipe.IsDone():
            raise RuntimeError("BRepOffsetAPI_MakePipe failed")
        sweep_solid = pipe.Shape()

        # 5. cut
        cut = BRepAlgoAPI_Cut(shape, sweep_solid)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("swept_pocket_along_curve cut failed")
        new_shape = cut.Shape()

        # 6. optional floor blend at path-end Z plane
        if args.floor_blend_r_mm > 0:
            end_z = args.path_points[-1][2]
            new_shape, _ = _fillet_edges_near_z(
                new_shape, end_z, args.floor_blend_r_mm,
            )

        history = EntityHistoryMap(
            rules={
                "target_face":  HistoryRule.MODIFIED_INHERIT,
                "swept_walls":  HistoryRule.GENERATED_NEW,
                "swept_floor":  HistoryRule.GENERATED_NEW,
                "swept_blends": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
