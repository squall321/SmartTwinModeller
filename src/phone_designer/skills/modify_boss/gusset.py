"""gusset — atomic. Triangular reinforcement web between two perpendicular faces.

A gusset is a right-triangle prism that bridges two perpendicular faces of a
body, adding rigidity at the shared edge. The triangle's catheti lie in the
two face planes; the prism is extruded along the shared edge.

v1: face_a and face_b must be planar, perpendicular, and share a common edge.
The shared edge must be axis-aligned (X/Y/Z) so the prism extrusion direction
is well-defined; the gusset is then a triangle in the plane orthogonal to that
edge with cathetes = (length_mm in face_a's plane) and (height_mm in face_b's
plane), and thickness_mm along the edge axis. position_along_edge_mm shifts
the gusset along the shared edge from the natural midpoint.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._resolvers import _face_center, _face_normal_at_center, resolve_faces
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="gusset",
    category="modify/boss",
    level="atomic",
    summary="Triangular reinforcement web between two perpendicular planar faces. "
            "Right-triangle prism extruded along the shared (axis-aligned) edge. "
            "Cathetes lie in the two face planes.",
    selector_kinds=["faces"],
    history_rules={
        "face_a":         HistoryRule.MODIFIED_INHERIT,
        "face_b":         HistoryRule.MODIFIED_INHERIT,
        "gusset_solid":   HistoryRule.GENERATED_NEW,
    },
    produces_features=["gusset"],
    preserves=["outer_envelope_outside_gusset"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.6, "min_draft_deg": 0.5,
                              "extras": {"thickness_recommended": "wall * 0.6"}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "cnc_3axis":         {"min_wall_mm": 0.5},
    },
    failure_modes=["fm.faces_not_perpendicular", "fm.no_shared_edge"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class Gusset(SkillBase):
    class Args(BaseModel):
        face_a_selector: SelectorRef
        face_b_selector: SelectorRef
        length_mm: float = Field(gt=0, le=50,
                                  description="cathetus along face_a's plane")
        height_mm: float = Field(gt=0, le=50,
                                  description="cathetus along face_b's plane")
        thickness_mm: float = Field(gt=0, le=20,
                                     description="extrusion along the shared edge")
        position_along_edge_mm: float = Field(default=0.0,
                                                description="offset from the natural midpoint along "
                                                            "the shared edge")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakePolygon,
        )
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.gp import gp_Pnt, gp_Vec

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces_a = resolve_faces(shape, args.face_a_selector, body=body)
        faces_b = resolve_faces(shape, args.face_b_selector, body=body)
        if not faces_a:
            raise RuntimeError(
                f"gusset: face_a_selector matched 0 — {args.face_a_selector.model_dump()}"
            )
        if not faces_b:
            raise RuntimeError(
                f"gusset: face_b_selector matched 0 — {args.face_b_selector.model_dump()}"
            )
        fa, fb = faces_a[0], faces_b[0]

        na = _face_normal_at_center(fa)
        nb = _face_normal_at_center(fb)
        if na == (0.0, 0.0, 0.0) or nb == (0.0, 0.0, 0.0):
            raise NotImplementedError("gusset v1: planar faces only")

        # perpendicularity check
        dot = na[0] * nb[0] + na[1] * nb[1] + na[2] * nb[2]
        if abs(dot) > 0.1:
            raise ValueError(
                f"gusset: face_a and face_b not perpendicular (cos={dot:.3f})"
            )

        # Determine the shared-edge axis: the unique axis (X/Y/Z) where both
        # normals are ~ zero. e.g. na=+Z (0,0,1), nb=+X (1,0,0) → shared edge is Y.
        ca = _face_center(fa)
        cb = _face_center(fb)

        idx_a = max(range(3), key=lambda i: abs(na[i]))
        idx_b = max(range(3), key=lambda i: abs(nb[i]))
        edge_idx = next((i for i in range(3) if i not in (idx_a, idx_b)), None)
        if edge_idx is None:
            raise ValueError(
                f"gusset: cannot identify shared-edge axis from normals {na} / {nb}"
            )

        # The corner (apex of the right-triangle) is the point that lies on
        # BOTH planes: along face_a's normal axis it takes face_a's plane
        # value (ca[idx_a]); along face_b's normal axis it takes face_b's
        # plane value (cb[idx_b]). Along the shared-edge axis, take the
        # midpoint of the two face centers + the user offset.
        corner = [0.0, 0.0, 0.0]
        corner[idx_a] = ca[idx_a]
        corner[idx_b] = cb[idx_b]
        corner[edge_idx] = (ca[edge_idx] + cb[edge_idx]) / 2.0 + args.position_along_edge_mm
        # shift back by half the extrusion thickness so the gusset is centered
        # on the chosen position
        corner[edge_idx] -= args.thickness_mm / 2.0

        # The gusset is an EXTERNAL bracket on the convex edge: it extends
        # outward from both faces, with the right-angle apex at the shared
        # edge. The two cathetes lie:
        #   - leg_a: in face_a's plane, along face_b's outward normal direction
        #     (i.e. tangent to face_a, away from the body along the face_b axis)
        #   - leg_b: in face_b's plane, along face_a's outward normal direction
        # This places the triangle in the empty (outside-the-body) quadrant
        # adjacent to the shared edge, so the prism extrusion adds material.
        leg_a_dir = [nb[0], nb[1], nb[2]]
        leg_b_dir = [na[0], na[1], na[2]]

        p0 = gp_Pnt(corner[0], corner[1], corner[2])
        p1 = gp_Pnt(
            corner[0] + leg_a_dir[0] * args.length_mm,
            corner[1] + leg_a_dir[1] * args.length_mm,
            corner[2] + leg_a_dir[2] * args.length_mm,
        )
        p2 = gp_Pnt(
            corner[0] + leg_b_dir[0] * args.height_mm,
            corner[1] + leg_b_dir[1] * args.height_mm,
            corner[2] + leg_b_dir[2] * args.height_mm,
        )

        # triangle wire
        poly = BRepBuilderAPI_MakePolygon()
        poly.Add(p0)
        poly.Add(p1)
        poly.Add(p2)
        poly.Close()
        if not poly.IsDone():
            raise RuntimeError("gusset: triangle wire build failed")
        wire = poly.Wire()

        face_mk = BRepBuilderAPI_MakeFace(wire)
        face_mk.Build()
        if not face_mk.IsDone():
            raise RuntimeError("gusset: triangle face build failed")
        tri_face = face_mk.Face()

        # prism vector: along edge axis, length = thickness
        vec = [0.0, 0.0, 0.0]
        vec[edge_idx] = args.thickness_mm
        prism_mk = BRepPrimAPI_MakePrism(tri_face, gp_Vec(vec[0], vec[1], vec[2]))
        prism_mk.Build()
        if not prism_mk.IsDone():
            raise RuntimeError("gusset: prism extrusion failed")
        prism = prism_mk.Shape()

        fuse = BRepAlgoAPI_Fuse(shape, prism)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("gusset: body fuse failed")
        new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={
                "face_a":       HistoryRule.MODIFIED_INHERIT,
                "face_b":       HistoryRule.MODIFIED_INHERIT,
                "gusset_solid": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
