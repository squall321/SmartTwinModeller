"""loft_pocket_between_sketches — macro.

Build a tapered pocket whose upper section follows `upper_sketch` and lower
section follows `lower_sketch`, lofted between them via
BRepOffsetAPI_ThruSections. Optional floor fillet.

Use cases: tapered recess for a press-fit insert, draft-friendly pockets for
injection molding (small at top of pocket, larger at floor — undercut-free).
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
    name="loft_pocket_between_sketches",
    category="modify/pocket",
    level="macro",
    summary="Cut a lofted pocket: upper section follows upper_sketch (at host "
            "face) and lower section follows lower_sketch (at depth). Useful for "
            "tapered recesses or draft-friendly injection-mold pockets.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":  HistoryRule.MODIFIED_INHERIT,
        "loft_walls":   HistoryRule.GENERATED_NEW,
        "loft_floor":   HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["pocket"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.0,
                              "extras": {"natural_draft_via_loft": True}},
    },
    failure_modes=["fm.loft_self_intersection", "fm.sketch_outside_face"],
    cost_hint=0.35,
    expansion=["extract_face_plane", "build_two_wires", "thru_sections", "cut"],
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class LoftPocketBetweenSketches(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        upper_sketch: SketchSpec
        lower_sketch: SketchSpec
        depth_mm: float = Field(gt=0, le=200)
        floor_blend_r_mm: float = Field(default=0.0, ge=0.0, le=10.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
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
                f"loft_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        center, normal = _face_plane_normal(target_face)
        nz = normal[2]
        if abs(nz) <= 0.95:
            raise NotImplementedError(
                "loft_pocket: non-Z face not supported (face-local coord transform needed)"
            )
        face_z = center[2]
        cx, cy = center[0], center[1]

        # Build planar faces in local XY (z=0), extract their outer wires.
        upper_face = _build_planar_face(args.upper_sketch)
        lower_face = _build_planar_face(args.lower_sketch)

        def _outer_wire(face):
            it = TopExp_Explorer(face, TopAbs_WIRE)
            if not it.More():
                raise RuntimeError("loft_pocket: face has no wire")
            return TopoDS.Wire_s(it.Current())

        upper_wire = _outer_wire(upper_face)
        lower_wire = _outer_wire(lower_face)

        # Place upper wire at z=face_z+cx,cy; lower wire at z=face_z ± depth.
        # pocket grows opposite of normal.
        floor_z = face_z - args.depth_mm if nz > 0 else face_z + args.depth_mm

        def _translate(wire, dx, dy, dz):
            t = gp_Trsf()
            t.SetTranslation(gp_Vec(dx, dy, dz))
            xf = BRepBuilderAPI_Transform(wire, t, True)
            xf.Build()
            return TopoDS.Wire_s(xf.Shape())

        upper_wire_w = _translate(upper_wire, cx, cy, face_z)
        lower_wire_w = _translate(lower_wire, cx, cy, floor_z)

        loft = BRepOffsetAPI_ThruSections(True, False, 1e-6)  # IsSolid=True
        # IMPORTANT: add in order from opening to floor (or vice versa) consistently.
        # For top face we want upper (face_z) first, lower (floor_z) second.
        loft.AddWire(upper_wire_w)
        loft.AddWire(lower_wire_w)
        try:
            loft.Build()
        except Exception as e:
            raise RuntimeError(f"loft_pocket: ThruSections.Build failed: {e}")
        tool = loft.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("loft_pocket: cut failed")
        new_shape = cut.Shape()

        if args.floor_blend_r_mm > 0:
            new_shape, _ = _fillet_edges_near_z(
                new_shape, floor_z, args.floor_blend_r_mm,
            )

        history = EntityHistoryMap(
            rules={
                "target_face": HistoryRule.MODIFIED_INHERIT,
                "loft_walls":  HistoryRule.GENERATED_NEW,
                "loft_floor":  HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
