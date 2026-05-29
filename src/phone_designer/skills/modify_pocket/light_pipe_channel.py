"""light_pipe_channel — atomic. Small circular channel swept along a 2D path.

A "light pipe channel" is a small-diameter cylindrical tunnel carved into the
body following a 2D polyline on the target face. Used to route optical light
pipes from an internal LED to an external indicator window, fibre-optic guides
inside the housing, IR-receiver light tubes, etc.

The channel is built by sweeping a small circular profile along a 3D polyline
constructed from the 2D `path_points` lifted onto the face plane. The resulting
tube is subtracted from the body. Unlike `cable_routing_channel` (a half-pipe
groove flush with the face), this skill places the circular profile fully
inside the body so the result is a true round tunnel.

v1 supports planar ±Z target faces.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._resolvers import (
    _face_center,
    _face_normal_at_center,
    resolve_faces,
)
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="light_pipe_channel",
    category="modify/pocket",
    level="atomic",
    summary="Small-diameter circular channel swept along a 2D polyline on a "
            "planar face. Routes optical light pipes / fibre guides from an "
            "internal LED to an external indicator window. v1 supports planar "
            "+Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":      HistoryRule.MODIFIED_INHERIT,
        "channel_walls":    HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.path_has_two_or_more_points",
        "pc.diameter_positive",
    ],
    produces_features=["light_pipe_channel"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4,
                              "extras": {"min_endmill_diameter_mm": "channel_d_mm"}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.path_outside_face",
        "fm.path_self_intersect",
        "fm.channel_exits_body",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class LightPipeChannel(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        path_points: list[tuple[float, float]] = Field(
            min_length=2,
            description="≥2 face-local (x, y) points (in mm). Origin at face center.",
        )
        channel_d_mm: float = Field(
            default=1.2, gt=0, le=10,
            description="Circular channel diameter (typical light pipe ≈ 1.0–2.0 mm).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeEdge,
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakeWire,
        )
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
        from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"light_pipe_channel: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "light_pipe_channel v1: planar +Z/-Z target faces only"
            )

        # Lift the channel a half-diameter below the face so the circular
        # cross-section is fully embedded → a true round tunnel (not a
        # half-pipe groove). nz>0 → recess in -Z; nz<0 → recess in +Z.
        radius = args.channel_d_mm / 2.0
        nz_sign = 1.0 if normal[2] > 0 else -1.0
        face_z = center[2]
        embed_z = face_z - nz_sign * radius

        # 1. Path wire: 3D polyline at the embed plane.
        world_pts = [
            (center[0] + px, center[1] + py, embed_z) for (px, py) in args.path_points
        ]
        mw = BRepBuilderAPI_MakeWire()
        added = 0
        for i in range(len(world_pts) - 1):
            p1 = gp_Pnt(*world_pts[i])
            p2 = gp_Pnt(*world_pts[i + 1])
            if p1.Distance(p2) < 1e-9:
                continue
            me = BRepBuilderAPI_MakeEdge(p1, p2)
            if not me.IsDone():
                raise RuntimeError(
                    f"light_pipe_channel: MakeEdge(polyline seg {i}) failed"
                )
            mw.Add(me.Edge())
            added += 1
        if added == 0:
            raise RuntimeError(
                "light_pipe_channel: all polyline segments degenerate"
            )
        if not mw.IsDone():
            raise RuntimeError("light_pipe_channel: MakeWire(polyline) failed")
        path_wire = mw.Wire()

        # 2. Circular profile at the path start, in the plane perpendicular to
        # the initial tangent (which is horizontal — XY plane).
        dx = world_pts[1][0] - world_pts[0][0]
        dy = world_pts[1][1] - world_pts[0][1]
        norm = (dx * dx + dy * dy) ** 0.5
        if norm < 1e-9:
            raise RuntimeError(
                "light_pipe_channel: first path segment is degenerate"
            )
        tx, ty = dx / norm, dy / norm

        profile_origin = gp_Pnt(*world_pts[0])
        profile_axis = gp_Ax2(profile_origin, gp_Dir(tx, ty, 0.0))
        circ = gp_Circ(profile_axis, radius)
        circle_edge = BRepBuilderAPI_MakeEdge(circ)
        if not circle_edge.IsDone():
            raise RuntimeError("light_pipe_channel: MakeEdge(profile circle) failed")
        circle_wire_mk = BRepBuilderAPI_MakeWire()
        circle_wire_mk.Add(circle_edge.Edge())
        if not circle_wire_mk.IsDone():
            raise RuntimeError("light_pipe_channel: MakeWire(profile) failed")
        circle_wire = circle_wire_mk.Wire()
        circle_face_mk = BRepBuilderAPI_MakeFace(circle_wire, True)
        if not circle_face_mk.IsDone():
            raise RuntimeError("light_pipe_channel: MakeFace(profile) failed")
        profile_face = circle_face_mk.Face()

        # 3. Sweep profile along path → tube solid.
        pipe = BRepOffsetAPI_MakePipe(path_wire, profile_face)
        pipe.Build()
        if not pipe.IsDone():
            raise RuntimeError("light_pipe_channel: BRepOffsetAPI_MakePipe failed")
        tube = pipe.Shape()

        # 4. Subtract tube from body.
        cut = BRepAlgoAPI_Cut(shape, tube)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("light_pipe_channel: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":   HistoryRule.MODIFIED_INHERIT,
                "channel_walls": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
