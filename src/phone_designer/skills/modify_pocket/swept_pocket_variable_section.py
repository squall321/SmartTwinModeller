"""swept_pocket_variable_section — atomic.

Sweep a profile along a 3D path where the profile cross-section changes
between the start (`start_sketch`) and the end (`end_sketch`). Subtract the
resulting tube from the body.

Use cases: a cable channel that funnels from a round entry to a flat exit,
an ergonomic finger groove that widens from one end to the other, a hidden
antenna trough whose cross-section morphs along the spine of the phone.

Args:
    face_selector: SelectorRef         — face where the pocket starts (planar ±Z)
    start_sketch:  SketchSpec          — cross-section at path start
    end_sketch:    SketchSpec          — cross-section at path end
    path_points:   list of 3D points (≥2)
    interpolation: "linear" | "spline" — how OCCT blends between profiles

Implementation:
    Build the two profile wires in local XY (z=0, +Z normal); transform each
    to its end of the path with the local +Z aligned to the path tangent at
    that endpoint. Use `BRepOffsetAPI_MakePipeShell` with both wires added
    (`SetMode` defaults to "tangent + first profile" which interpolates
    intermediate sections). Cut the resulting solid from the body.

    `interpolation="spline"` builds a spline path (and BRepOffsetAPI smooths
    the section blending); "linear" uses a polyline path.
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


# ── path + tangent helpers (kept self-contained — do not import from
# swept_pocket_along_curve to avoid a registry duplicate) ────────────────────


def _build_pipe_path_wire(
    points: list[tuple[float, float, float]],
    interpolation: Literal["linear", "spline"],
):
    """Build a TopoDS_Wire from 3D points (polyline or interpolated B-spline)."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.gp import gp_Pnt

    if len(points) < 2:
        raise ValueError("path_points must contain >= 2 points")

    if interpolation == "linear":
        mw = BRepBuilderAPI_MakeWire()
        for i in range(len(points) - 1):
            p1 = gp_Pnt(*points[i])
            p2 = gp_Pnt(*points[i + 1])
            if p1.Distance(p2) < 1e-9:
                continue
            me = BRepBuilderAPI_MakeEdge(p1, p2)
            if not me.IsDone():
                raise RuntimeError(
                    f"swept_pocket_variable_section: MakeEdge(polyline {i}) failed"
                )
            mw.Add(me.Edge())
        if not mw.IsDone():
            raise RuntimeError(
                "swept_pocket_variable_section: MakeWire(polyline) failed"
            )
        return mw.Wire()

    if interpolation == "spline":
        from OCP.GeomAPI import GeomAPI_Interpolate
        from OCP.TColgp import TColgp_HArray1OfPnt

        arr = TColgp_HArray1OfPnt(1, len(points))
        for i, (x, y, z) in enumerate(points, start=1):
            arr.SetValue(i, gp_Pnt(x, y, z))
        interp = GeomAPI_Interpolate(arr, False, 1e-6)
        interp.Perform()
        if not interp.IsDone():
            raise RuntimeError(
                "swept_pocket_variable_section: GeomAPI_Interpolate failed"
            )
        curve = interp.Curve()
        me = BRepBuilderAPI_MakeEdge(curve)
        if not me.IsDone():
            raise RuntimeError(
                "swept_pocket_variable_section: MakeEdge(bspline) failed"
            )
        mw = BRepBuilderAPI_MakeWire()
        mw.Add(me.Edge())
        if not mw.IsDone():
            raise RuntimeError(
                "swept_pocket_variable_section: MakeWire(bspline) failed"
            )
        return mw.Wire()

    raise ValueError(f"unknown interpolation: {interpolation}")


def _tangent_between(
    p_prev: tuple[float, float, float],
    p_next: tuple[float, float, float],
) -> tuple[float, float, float]:
    dx = p_next[0] - p_prev[0]
    dy = p_next[1] - p_prev[1]
    dz = p_next[2] - p_prev[2]
    L = math.sqrt(dx * dx + dy * dy + dz * dz)
    if L < 1e-12:
        return (0.0, 0.0, 1.0)
    return (dx / L, dy / L, dz / L)


def _transform_wire_to_frame(
    wire,
    origin: tuple[float, float, float],
    new_z: tuple[float, float, float],
):
    """Transform `wire` from local (origin=(0,0,0), +Z=(0,0,1)) to target frame."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf
    from OCP.TopoDS import TopoDS

    target = gp_Ax3(gp_Pnt(*origin), gp_Dir(*new_z))
    default = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    t = gp_Trsf()
    t.SetTransformation(target, default)
    xf = BRepBuilderAPI_Transform(wire, t, True)
    xf.Build()
    if not xf.IsDone():
        raise RuntimeError(
            "swept_pocket_variable_section: profile wire transform failed"
        )
    return TopoDS.Wire_s(xf.Shape())


def _outer_wire_of_face(face):
    """Extract the outer TopoDS_Wire from a planar face."""
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    it = TopExp_Explorer(face, TopAbs_WIRE)
    if not it.More():
        raise RuntimeError(
            "swept_pocket_variable_section: profile face has no wire"
        )
    return TopoDS.Wire_s(it.Current())


@skill(
    name="swept_pocket_variable_section",
    category="modify/pocket",
    level="atomic",
    summary="Sweep a profile along a 3D path with a cross-section that varies "
            "between start and end sketches. The resulting tube is subtracted "
            "from the body — used for tapered channels, funneled cable "
            "passages, or ergonomic grooves that morph along their length.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":   HistoryRule.MODIFIED_INHERIT,
        "swept_walls":   HistoryRule.GENERATED_NEW,
        "swept_floor":   HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
    ],
    produces_features=["pocket", "swept_variable_channel"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_5axis":         {"min_wall_mm": 0.5, "extras": {"requires_5axis": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.sweep_self_intersect",
        "fm.path_exits_body",
        "fm.profile_too_large_for_curvature",
        "fm.pipeshell_build_failed",
    ],
    cost_hint=0.45,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class SweptPocketVariableSection(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        start_sketch: SketchSpec
        end_sketch: SketchSpec
        path_points: list[tuple[float, float, float]] = Field(min_length=2)
        interpolation: Literal["linear", "spline"] = "linear"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepBuilderAPI import BRepBuilderAPI_TransitionMode
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _build_planar_face,
            _face_plane_normal,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"swept_pocket_variable_section: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        _center, normal = _face_plane_normal(target_face)
        if abs(normal[2]) <= 0.95:
            raise NotImplementedError(
                "swept_pocket_variable_section v0: host face must be planar ±Z"
            )

        # 1. start + end profile wires in local XY
        start_face = _build_planar_face(args.start_sketch)
        end_face = _build_planar_face(args.end_sketch)
        start_wire_local = _outer_wire_of_face(start_face)
        end_wire_local = _outer_wire_of_face(end_face)

        # 2. tangent at start and end of the path
        pts = args.path_points
        tangent_start = _tangent_between(pts[0], pts[1])
        tangent_end = _tangent_between(pts[-2], pts[-1])

        # 3. transform profiles to their endpoints
        start_wire = _transform_wire_to_frame(
            start_wire_local, pts[0], tangent_start,
        )
        end_wire = _transform_wire_to_frame(
            end_wire_local, pts[-1], tangent_end,
        )

        # 4. path wire
        path_wire = _build_pipe_path_wire(pts, args.interpolation)

        # 5. MakePipeShell with two profiles
        pipe = BRepOffsetAPI_MakePipeShell(path_wire)
        pipe.SetTransitionMode(
            BRepBuilderAPI_TransitionMode.BRepBuilderAPI_RightCorner
        )   # right-corner; fine for this v0
        pipe.Add(start_wire, False, False)
        pipe.Add(end_wire, False, False)
        if not pipe.IsReady():
            raise RuntimeError(
                "swept_pocket_variable_section: MakePipeShell IsReady=False"
            )
        try:
            pipe.Build()
        except Exception as e:
            raise RuntimeError(
                f"swept_pocket_variable_section: pipe.Build failed: {e}"
            ) from e
        if not pipe.IsDone():
            raise RuntimeError(
                "swept_pocket_variable_section: pipe IsDone=False"
            )
        if not pipe.MakeSolid():
            raise RuntimeError(
                "swept_pocket_variable_section: pipe.MakeSolid() failed"
            )
        sweep_solid = pipe.Shape()

        # 6. cut from body
        cut = BRepAlgoAPI_Cut(shape, sweep_solid)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError(
                "swept_pocket_variable_section: cut failed"
            )
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":  HistoryRule.MODIFIED_INHERIT,
                "swept_walls":  HistoryRule.GENERATED_NEW,
                "swept_floor":  HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
