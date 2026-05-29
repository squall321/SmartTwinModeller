"""cable_routing_channel — atomic. Semi-circular channel along a 2D polyline on a face.

A "cable routing channel" is a half-pipe groove carved into a planar face, used
to seat a cable so it doesn't rattle inside a housing. The path is given as a
list of 2D points in face-local coordinates (relative to the target face's
center). A circular profile of `radius_mm` is swept along the polyline; the
resulting tube is then subtracted from the body.

The cut-tool is built as a full cylinder swept along the path so that the
resulting cavity is a semi-circular groove of radius_mm with its diameter
aligned with the face surface (the upper half of the cylinder sticks above the
face and is naturally trimmed away by the boolean cut — only the part inside
the body remains).

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
    name="cable_routing_channel",
    category="modify/pocket",
    level="atomic",
    summary="Semi-circular cable-routing channel swept along a polyline on a planar "
            "face. Used to seat a cable inside a housing so it doesn't rattle. "
            "v1 supports planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":   HistoryRule.MODIFIED_INHERIT,
        "channel_walls": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.path_has_two_or_more_points",
    ],
    produces_features=["cable_channel"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"ball_endmill_diameter_mm": "2 * radius_mm"}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.path_outside_face",
        "fm.path_self_intersect",
        "fm.radius_too_large_for_wall",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class CableRoutingChannel(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        path_points: list[tuple[float, float]] = Field(
            min_length=2,
            description="≥2 face-local (x, y) points (in mm). Origin at face center.",
        )
        radius_mm: float = Field(gt=0, le=20,
                                  description="cross-section radius (channel half-width)")

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
                f"cable_routing_channel: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "cable_routing_channel v1: planar +Z/-Z target faces only"
            )

        # 1. Build path wire (3D polyline at face-Z plane).
        # path_points are face-local (x, y); convert to world (cx + x, cy + y, face_z).
        face_z = center[2]
        world_pts = [
            (center[0] + px, center[1] + py, face_z) for (px, py) in args.path_points
        ]

        mw = BRepBuilderAPI_MakeWire()
        for i in range(len(world_pts) - 1):
            p1 = gp_Pnt(*world_pts[i])
            p2 = gp_Pnt(*world_pts[i + 1])
            if p1.Distance(p2) < 1e-9:
                continue
            me = BRepBuilderAPI_MakeEdge(p1, p2)
            if not me.IsDone():
                raise RuntimeError(
                    f"cable_routing_channel: MakeEdge(polyline seg {i}) failed"
                )
            mw.Add(me.Edge())
        if not mw.IsDone():
            raise RuntimeError("cable_routing_channel: MakeWire(polyline) failed")
        path_wire = mw.Wire()

        # 2. Build circular profile face at path start, in the plane perpendicular
        # to the initial path tangent. For a polyline parallel to the face plane,
        # the tangent is in XY; the profile circle lives in a plane whose normal
        # is the tangent.
        dx = world_pts[1][0] - world_pts[0][0]
        dy = world_pts[1][1] - world_pts[0][1]
        norm = (dx * dx + dy * dy) ** 0.5
        if norm < 1e-9:
            raise RuntimeError(
                "cable_routing_channel: first path segment is degenerate"
            )
        tx, ty = dx / norm, dy / norm

        # Circle axis: at first point, with normal = tangent direction.
        profile_origin = gp_Pnt(*world_pts[0])
        profile_axis = gp_Ax2(profile_origin, gp_Dir(tx, ty, 0.0))
        circ = gp_Circ(profile_axis, args.radius_mm)
        circle_edge = BRepBuilderAPI_MakeEdge(circ)
        if not circle_edge.IsDone():
            raise RuntimeError("cable_routing_channel: MakeEdge(profile circle) failed")
        circle_wire_mk = BRepBuilderAPI_MakeWire()
        circle_wire_mk.Add(circle_edge.Edge())
        if not circle_wire_mk.IsDone():
            raise RuntimeError("cable_routing_channel: MakeWire(profile) failed")
        circle_wire = circle_wire_mk.Wire()
        circle_face_mk = BRepBuilderAPI_MakeFace(circle_wire, True)
        if not circle_face_mk.IsDone():
            raise RuntimeError("cable_routing_channel: MakeFace(profile) failed")
        profile_face = circle_face_mk.Face()

        # 3. Sweep profile along path to get a tube.
        pipe = BRepOffsetAPI_MakePipe(path_wire, profile_face)
        pipe.Build()
        if not pipe.IsDone():
            raise RuntimeError("cable_routing_channel: BRepOffsetAPI_MakePipe failed")
        tube = pipe.Shape()

        # 4. Cut body by tube — the part above the face is naturally outside the
        # body so the resulting cavity is a semi-circular groove flush with the face.
        cut = BRepAlgoAPI_Cut(shape, tube)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("cable_routing_channel: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":   HistoryRule.MODIFIED_INHERIT,
                "channel_walls": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
