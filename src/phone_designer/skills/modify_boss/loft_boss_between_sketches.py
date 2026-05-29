"""loft_boss_between_sketches — macro.

Build a tapered raised boss whose lower section follows `lower_sketch` (sitting
on the chosen host face) and upper section follows `upper_sketch` (at offset
`height_mm` along the face's outward normal), lofted between them via
BRepOffsetAPI_ThruSections and fused onto the body.

Use cases: tapered grip bumps, draft-friendly external bosses for injection
molding (large at base, smaller at top), shape-transition cosmetic features
(circle to rectangle).
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
from phone_designer.skills.modify_pocket.extrude_pocket_blended import (
    _fillet_edges_near_z,
)


@skill(
    name="loft_boss_between_sketches",
    category="modify/boss",
    level="macro",
    summary="Build a lofted boss: lower section follows lower_sketch (on host "
            "face) and upper section follows upper_sketch (at height). Useful "
            "for tapered grip bumps, draft-friendly external bosses, or "
            "shape-transition cosmetic features (e.g., circle → rectangle).",
    selector_kinds=["faces"],
    history_rules={
        "target_face": HistoryRule.MODIFIED_INHERIT,
        "loft_walls":  HistoryRule.GENERATED_NEW,
        "loft_top":    HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.height_positive",
    ],
    produces_features=["plateau", "lofted_boss"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.0,
                              "extras": {"natural_draft_via_loft": True}},
    },
    failure_modes=["fm.loft_self_intersection", "fm.sketch_outside_face"],
    cost_hint=0.35,
    expansion=["extract_face_plane", "build_two_wires", "thru_sections", "fuse"],
    post_conditions=[PostCondition(kind="volume_increased")],
)
class LoftBossBetweenSketches(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        lower_sketch: SketchSpec
        upper_sketch: SketchSpec
        height_mm: float = Field(gt=0, le=200)
        top_blend_r_mm: float = Field(default=0.0, ge=0.0, le=10.0)
        base_blend_r_mm: float = Field(default=0.0, ge=0.0, le=10.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
        from OCP.gp import gp_Trsf, gp_Vec
        from OCP.TopAbs import TopAbs_WIRE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _build_planar_face,
            _face_plane_normal,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"loft_boss: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        center, normal = _face_plane_normal(target_face)
        nz = normal[2]
        if abs(nz) <= 0.95:
            raise NotImplementedError(
                "loft_boss: non-Z face not supported (face-local coord transform needed)"
            )
        face_z = center[2]
        cx, cy = center[0], center[1]

        # Build planar faces in local XY (z=0), extract their outer wires.
        lower_face = _build_planar_face(args.lower_sketch)
        upper_face = _build_planar_face(args.upper_sketch)

        def _outer_wire(face):
            it = TopExp_Explorer(face, TopAbs_WIRE)
            if not it.More():
                raise RuntimeError("loft_boss: face has no wire")
            return TopoDS.Wire_s(it.Current())

        lower_wire = _outer_wire(lower_face)
        upper_wire = _outer_wire(upper_face)

        # Boss grows along face normal:
        #   top face   (nz > 0) → lower at face_z, top at face_z + height
        #   bottom face(nz < 0) → lower at face_z, top at face_z - height
        top_z = face_z + args.height_mm if nz > 0 else face_z - args.height_mm

        def _translate(wire, dx, dy, dz):
            t = gp_Trsf()
            t.SetTranslation(gp_Vec(dx, dy, dz))
            xf = BRepBuilderAPI_Transform(wire, t, True)
            xf.Build()
            return TopoDS.Wire_s(xf.Shape())

        lower_wire_w = _translate(lower_wire, cx, cy, face_z)
        upper_wire_w = _translate(upper_wire, cx, cy, top_z)

        loft = BRepOffsetAPI_ThruSections(True, False, 1e-6)  # IsSolid=True, IsRuled=False
        # Add in consistent base-to-top order.
        loft.AddWire(lower_wire_w)
        loft.AddWire(upper_wire_w)
        try:
            loft.Build()
        except Exception as e:
            raise RuntimeError(f"loft_boss: ThruSections.Build failed: {e}")
        tool = loft.Shape()

        fuse = BRepAlgoAPI_Fuse(shape, tool)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("loft_boss: fuse failed")
        new_shape = fuse.Shape()

        if args.top_blend_r_mm > 0:
            new_shape, _ = _fillet_edges_near_z(
                new_shape, top_z, args.top_blend_r_mm,
            )
        if args.base_blend_r_mm > 0:
            new_shape, _ = _fillet_edges_near_z(
                new_shape, face_z, args.base_blend_r_mm,
            )

        history = EntityHistoryMap(
            rules={
                "target_face": HistoryRule.MODIFIED_INHERIT,
                "loft_walls":  HistoryRule.GENERATED_NEW,
                "loft_top":    HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
