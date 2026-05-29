"""sim_tray_pocket — atomic. SIM-tray cavity + eject pin-hole.

Mobile phones carry a small rectangular pocket on the side housing into which
a SIM tray slides. Next to it, offset `eject_offset_mm` from one short edge,
is a tiny through-hole that an eject pin can press to pop the tray out.

Geometry (raw OCCT, ±Z planar host faces v1):
    pocket : MakeBox(tray_l x tray_w x tray_d) centered at face-local
             position_xy, sunk along the face normal.
    eject  : MakeCylinder(eject_hole_d/2, traverse) offset `eject_offset_mm`
             outside the +X short edge of the pocket, along the face normal.

Result = body \\ (pocket ∪ eject).
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
    name="sim_tray_pocket",
    category="modify/pocket",
    level="atomic",
    summary="SIM-tray rectangular cavity + tiny eject pin-hole offset from "
            "one short edge. v1 supports planar ±Z host faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "sim_tray_pocket": HistoryRule.GENERATED_NEW,
        "eject_pin_hole":  HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["sim_tray_pocket", "eject_pin_hole"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3,
                              "extras": {"min_drill_mm": 0.5}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_tray_fit": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.tray_pocket_exits_body",
        "fm.eject_hole_too_close_to_pocket_edge",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class SimTrayPocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of pocket center",
        )
        tray_l_mm: float = Field(default=12.3, gt=0, le=50,
                                  description="tray pocket length along world X")
        tray_w_mm: float = Field(default=8.8, gt=0, le=40,
                                  description="tray pocket width along world Y")
        tray_d_mm: float = Field(default=4.5, gt=0, le=20,
                                  description="tray pocket depth along face normal")
        eject_hole_d_mm: float = Field(default=0.8, gt=0, le=3,
                                        description="eject pin-hole diameter")
        eject_offset_mm: float = Field(default=1.0, gt=0, le=10,
                                        description="eject hole offset from "
                                                    "pocket +X short edge")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"sim_tray_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "sim_tray_pocket v1: planar ±Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        face_z = center[2]
        pocket_bottom_z = face_z - z_sign * args.tray_d_mm

        # Rectangular pocket tool
        zmin = min(face_z, pocket_bottom_z)
        zmax = max(face_z, pocket_bottom_z)
        # Overshoot above the face so the boolean cut produces a clean opening.
        overshoot = 0.05
        zmin_o = zmin - overshoot if z_sign < 0 else zmin
        zmax_o = zmax + overshoot if z_sign > 0 else zmax

        pocket_lo = gp_Pnt(cx - args.tray_l_mm / 2.0,
                           cy - args.tray_w_mm / 2.0,
                           zmin_o)
        pocket_hi = gp_Pnt(cx + args.tray_l_mm / 2.0,
                           cy + args.tray_w_mm / 2.0,
                           zmax_o)
        pocket_mk = BRepPrimAPI_MakeBox(pocket_lo, pocket_hi)
        pocket_mk.Build()
        if not pocket_mk.IsDone():
            raise RuntimeError("sim_tray_pocket: tray box build failed")
        pocket_shape = pocket_mk.Shape()

        # Eject hole: offset outside the +X (short) edge of the pocket.
        eject_x = cx + args.tray_l_mm / 2.0 + args.eject_offset_mm
        eject_y = cy
        # Through-hole down the face normal; cylinder goes from above the face
        # straight through any reasonable body.
        overshoot_e = 1.0
        cyl_h = 200.0 + overshoot_e
        base_z = face_z + z_sign * overshoot_e
        ax = gp_Ax2(gp_Pnt(eject_x, eject_y, base_z),
                    gp_Dir(0.0, 0.0, -z_sign))
        cyl_mk = BRepPrimAPI_MakeCylinder(ax, args.eject_hole_d_mm / 2.0, cyl_h)
        cyl_mk.Build()
        if not cyl_mk.IsDone():
            raise RuntimeError("sim_tray_pocket: eject cylinder build failed")
        eject_shape = cyl_mk.Shape()

        # Union pocket + eject, then cut from body.
        fuse = BRepAlgoAPI_Fuse(pocket_shape, eject_shape)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("sim_tray_pocket: pocket+eject fuse failed")
        tool_shape = fuse.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool_shape)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("sim_tray_pocket: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "sim_tray_pocket": HistoryRule.GENERATED_NEW,
                "eject_pin_hole":  HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
