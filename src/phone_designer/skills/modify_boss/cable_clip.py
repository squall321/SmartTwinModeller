"""cable_clip — atomic. Small L-shaped clip on a face for holding a cable.

An L-shaped clip is two thin walls meeting at right angles. A cable lays in
the corner pocket and is retained by the inward-facing lip of the L. Geometry:
two box prisms, fused, attached to a planar face.

Layout (face-local +X axis points along the clip "opening"):
    - vertical wall (base): a thin box of size (wall_thickness, opening_width, height)
      sitting on the face with its -X face at face-local x = 0.
    - top lip:     a thin box of size (opening_width + wall_thickness, opening_width, wall_thickness)
      sitting at the top of the vertical wall, offset back toward -X so it
      overhangs the corner.

Cable cross-section diameter fits within `opening_width_mm`.

v1 supports planar +Z/-Z target faces.
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
    name="cable_clip",
    category="modify/boss",
    level="atomic",
    summary="Small L-shaped cable clip — a vertical wall + overhanging lip — attached "
            "to a planar face. Cable nests in the L corner and is retained by the "
            "lip. v1 supports planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":    HistoryRule.MODIFIED_INHERIT,
        "cable_clip_solid": HistoryRule.GENERATED_NEW,
    },
    produces_features=["cable_clip"],
    preserves=["outer_envelope_outside_clip"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"undercut_warning": "lip is an undercut "
                                          "— requires side-action mold or flex lip"}},
        "cnc_3axis":         {"min_wall_mm": 0.5},
    },
    failure_modes=["fm.opening_too_narrow_for_cable", "fm.lip_too_thin"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class CableClip(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of clip corner (anchor of vertical wall)",
        )
        opening_width_mm: float = Field(gt=0, le=15,
                                          description="cable-cavity width (Y) and "
                                                      "lip overhang depth (X)")
        height_mm: float = Field(gt=0, le=20,
                                  description="vertical wall height along face normal")
        wall_thickness_mm: float = Field(default=1.0, gt=0, le=5,
                                          description="vertical wall and lip thickness")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        if args.wall_thickness_mm >= args.opening_width_mm:
            raise ValueError(
                f"cable_clip: wall_thickness_mm ({args.wall_thickness_mm}) >= "
                f"opening_width_mm ({args.opening_width_mm}) — no cable cavity"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"cable_clip: face_selector matched 0 — {args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "cable_clip v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0

        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        base_z = center[2]
        top_z = base_z + z_sign * args.height_mm
        lip_top_z = top_z + z_sign * args.wall_thickness_mm

        wt = args.wall_thickness_mm
        ow = args.opening_width_mm

        # Vertical wall: spans X = [cx, cx + wt], Y = [cy - ow/2, cy + ow/2],
        # Z = [base_z, top_z]. Box construction needs ordered points.
        zmin_w = min(base_z, top_z)
        zmax_w = max(base_z, top_z)
        wall_lo = gp_Pnt(cx, cy - ow / 2.0, zmin_w)
        wall_hi = gp_Pnt(cx + wt, cy + ow / 2.0, zmax_w)
        wall_mk = BRepPrimAPI_MakeBox(wall_lo, wall_hi)
        wall_mk.Build()
        if not wall_mk.IsDone():
            raise RuntimeError("cable_clip: vertical wall build failed")
        wall_shape = wall_mk.Shape()

        # Top lip: overhangs in -X over the corner, spans X = [cx - ow, cx + wt],
        # Y same as wall, Z = [top_z, lip_top_z].
        zmin_l = min(top_z, lip_top_z)
        zmax_l = max(top_z, lip_top_z)
        lip_lo = gp_Pnt(cx - ow, cy - ow / 2.0, zmin_l)
        lip_hi = gp_Pnt(cx + wt, cy + ow / 2.0, zmax_l)
        lip_mk = BRepPrimAPI_MakeBox(lip_lo, lip_hi)
        lip_mk.Build()
        if not lip_mk.IsDone():
            raise RuntimeError("cable_clip: lip build failed")
        lip_shape = lip_mk.Shape()

        # wall + lip → clip solid
        f1 = BRepAlgoAPI_Fuse(wall_shape, lip_shape)
        f1.Build()
        if not f1.IsDone():
            raise RuntimeError("cable_clip: wall+lip fuse failed")
        clip_shape = f1.Shape()

        # body + clip
        f2 = BRepAlgoAPI_Fuse(shape, clip_shape)
        f2.Build()
        if not f2.IsDone():
            raise RuntimeError("cable_clip: body fuse failed")
        new_shape = f2.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":      HistoryRule.MODIFIED_INHERIT,
                "cable_clip_solid": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
