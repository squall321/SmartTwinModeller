"""extrude_pocket_blended — atomic.

Like extrude_pocket but with FLOOR and OPENING fillets baked into the same
operation. Saves the user from manually computing edge sets after the cut.

Args:
    face_selector: SelectorRef           — host face (top/bottom planar)
    sketch:        SketchSpec            — any supported sketch type
    depth_mm:      float                  — pocket depth
    floor_blend_r_mm:   float = 0.0       — fillet on bottom inner edges
    opening_blend_r_mm: float = 0.0       — fillet on opening edges
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec


# Tolerance for selecting edges near a given Z plane (mm).
_EDGE_Z_EPS = 0.05


def _fillet_edges_near_z(shape, target_z: float, radius: float):
    """Apply BRepFilletAPI_MakeFillet to all edges whose endpoints both lie
    within _EDGE_Z_EPS of `target_z`. Returns the new shape, or `shape`
    unchanged if no edges match / fillet fails.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_MapOfShape

    seen = TopTools_MapOfShape()
    candidates = []
    it = TopExp_Explorer(shape, TopAbs_EDGE)
    while it.More():
        e = it.Current()
        if not seen.Contains(e):
            seen.Add(e)
            edge = TopoDS.Edge_s(e)
            v1 = TopExp.FirstVertex_s(edge)
            v2 = TopExp.LastVertex_s(edge)
            try:
                p1 = BRep_Tool.Pnt_s(v1)
                p2 = BRep_Tool.Pnt_s(v2)
            except Exception:
                it.Next()
                continue
            if (abs(p1.Z() - target_z) < _EDGE_Z_EPS
                    and abs(p2.Z() - target_z) < _EDGE_Z_EPS):
                candidates.append(edge)
        it.Next()

    if not candidates:
        return shape, 0

    # bulk attempt first
    try:
        maker = BRepFilletAPI_MakeFillet(shape)
        for e in candidates:
            maker.Add(radius, e)
        maker.Build()
        if maker.IsDone():
            return maker.Shape(), len(candidates)
    except Exception:
        pass

    # individual fallback
    cur = shape
    ok = 0
    for e in candidates:
        try:
            m = BRepFilletAPI_MakeFillet(cur)
            m.Add(radius, e)
            m.Build()
            if m.IsDone():
                cur = m.Shape()
                ok += 1
        except Exception:
            continue
    return cur, ok


@skill(
    name="extrude_pocket_blended",
    category="modify/pocket",
    level="atomic",
    summary="Extrude a 2D sketch into a face, with floor and/or opening blend "
            "(fillet) radii baked into the same operation. Removes the need for "
            "a separate final_fillet call to soften pocket edges.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "pocket_walls":    HistoryRule.GENERATED_NEW,
        "pocket_bottom":   HistoryRule.GENERATED_NEW,
        "pocket_blends":   HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["pocket", "fillet_faces_array"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5, "min_fillet_r_mm": 0.3,
                              "extras": {"max_aspect_ratio": 4.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "min_fillet_r_mm": 0.5},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "min_fillet_r_mm": 0.3},
    },
    failure_modes=["fm.pocket_exits_body", "fm.sketch_outside_face",
                   "fm.fillet_radius_too_large"],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class ExtrudePocketBlended(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        sketch: SketchSpec
        depth_mm: float = Field(gt=0, le=200)
        floor_blend_r_mm: float = Field(default=0.0, ge=0.0, le=10.0)
        opening_blend_r_mm: float = Field(default=0.0, ge=0.0, le=10.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _face_plane_normal,
            build_pocket_tool,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"extrude_pocket_blended: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        center, normal = _face_plane_normal(target_face)
        face_z = center[2]
        nz = normal[2]

        tool = build_pocket_tool(target_face, args.sketch, args.depth_mm, direction="into")

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("extrude_pocket_blended cut 실패")
        new_shape = cut.Shape()

        # Compute pocket floor & opening Z planes from face_z + face normal sign.
        # pocket grows opposite of face normal:
        #   top face   (nz > 0)  → opening at face_z, floor at face_z - depth
        #   bottom face(nz < 0)  → opening at face_z, floor at face_z + depth
        opening_z = face_z
        floor_z = face_z - args.depth_mm if nz > 0 else face_z + args.depth_mm

        if args.floor_blend_r_mm > 0:
            new_shape, _ = _fillet_edges_near_z(
                new_shape, floor_z, args.floor_blend_r_mm,
            )
        if args.opening_blend_r_mm > 0:
            new_shape, _ = _fillet_edges_near_z(
                new_shape, opening_z, args.opening_blend_r_mm,
            )

        history = EntityHistoryMap(
            rules={
                "target_face":   HistoryRule.MODIFIED_INHERIT,
                "pocket_walls":  HistoryRule.GENERATED_NEW,
                "pocket_bottom": HistoryRule.GENERATED_NEW,
                "pocket_blends": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
