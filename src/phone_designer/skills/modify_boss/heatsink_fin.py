"""heatsink_fin — atomic. Thin rectangular fin attached to a planar face.

A fin is a thin rectangular boss with `length × width × height` dimensions
(width = thickness across the fin). Optional `top_taper_mm` shrinks the top
edge along the length axis (top length = base length - top_taper_mm) so that
the part can be released from an injection mold.

v1 supports planar +Z/-Z target faces; the fin grows outward along the face
normal. length is along the face's local X (world X), width along world Y.
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
    name="heatsink_fin",
    category="modify/boss",
    level="atomic",
    summary="Thin rectangular heatsink fin attached to a planar face. "
            "Optional top_taper_mm narrows the top edge along the length axis "
            "for mold release. v1 supports +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":    HistoryRule.MODIFIED_INHERIT,
        "fin_solid":      HistoryRule.GENERATED_NEW,
    },
    produces_features=["heatsink_fin"],
    preserves=["outer_envelope_outside_fin"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.6, "min_draft_deg": 0.5,
                              "extras": {"thickness_recommended_mm": "wall * 0.6"}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "cnc_3axis":         {"min_wall_mm": 0.5},
    },
    failure_modes=["fm.taper_exceeds_length", "fm.fin_zero_thickness"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class HeatsinkFin(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        center_x_mm: float = Field(default=0.0,
                                    description="face-local X offset from face center")
        center_y_mm: float = Field(default=0.0,
                                    description="face-local Y offset from face center")
        length_mm: float = Field(gt=0, le=100,
                                  description="fin length along world X")
        width_mm: float = Field(gt=0, le=20,
                                 description="fin width (thickness) along world Y")
        height_mm: float = Field(gt=0, le=50,
                                  description="fin height along face normal")
        top_taper_mm: float = Field(default=0.0, ge=0.0, le=50.0,
                                     description="reduction of top length for mold release "
                                                 "(top_length = length - top_taper)")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
        from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        if args.top_taper_mm >= args.length_mm:
            raise ValueError(
                f"heatsink_fin: top_taper_mm ({args.top_taper_mm}) >= "
                f"length_mm ({args.length_mm}) — top edge degenerate"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"heatsink_fin: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "heatsink_fin v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0

        cx = center[0] + args.center_x_mm
        cy = center[1] + args.center_y_mm
        base_z = center[2]

        if args.top_taper_mm == 0.0:
            # plain rectangular box
            #   X span: [cx - L/2, cx + L/2]
            #   Y span: [cy - W/2, cy + W/2]
            #   Z span: from base_z, height * z_sign
            top_z = base_z + z_sign * args.height_mm
            zmin = min(base_z, top_z)
            zmax = max(base_z, top_z)
            p_lo = gp_Pnt(cx - args.length_mm / 2.0, cy - args.width_mm / 2.0, zmin)
            p_hi = gp_Pnt(cx + args.length_mm / 2.0, cy + args.width_mm / 2.0, zmax)
            mk = BRepPrimAPI_MakeBox(p_lo, p_hi)
            mk.Build()
            if not mk.IsDone():
                raise RuntimeError("heatsink_fin: box build failed")
            fin_shape = mk.Shape()
        else:
            # ThruSections loft between base rectangle and top (narrower) rectangle
            half_lb = args.length_mm / 2.0
            half_lt = (args.length_mm - args.top_taper_mm) / 2.0
            half_w = args.width_mm / 2.0
            top_z = base_z + z_sign * args.height_mm

            def _rect_wire(half_l: float, z: float):
                poly = BRepBuilderAPI_MakePolygon()
                poly.Add(gp_Pnt(cx - half_l, cy - half_w, z))
                poly.Add(gp_Pnt(cx + half_l, cy - half_w, z))
                poly.Add(gp_Pnt(cx + half_l, cy + half_w, z))
                poly.Add(gp_Pnt(cx - half_l, cy + half_w, z))
                poly.Close()
                if not poly.IsDone():
                    raise RuntimeError("heatsink_fin: rect wire build failed")
                return poly.Wire()

            base_wire = _rect_wire(half_lb, base_z)
            top_wire = _rect_wire(half_lt, top_z)

            loft = BRepOffsetAPI_ThruSections(True, True)  # isSolid=True, isRuled=True
            loft.AddWire(base_wire)
            loft.AddWire(top_wire)
            loft.Build()
            if not loft.IsDone():
                raise RuntimeError("heatsink_fin: loft build failed")
            fin_shape = loft.Shape()

        fuse = BRepAlgoAPI_Fuse(shape, fin_shape)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("heatsink_fin: body fuse failed")
        new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face": HistoryRule.MODIFIED_INHERIT,
                "fin_solid":   HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
