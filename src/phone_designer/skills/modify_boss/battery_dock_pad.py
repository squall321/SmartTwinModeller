"""battery_dock_pad — atomic. Flat pad with 2 parallel contact ribs.

A battery-dock pad is a flat rectangular pad (length × width × height) sitting
on a planar face, topped by two thin parallel ribs running along its length.
The ribs are spaced `contact_pitch_mm` apart center-to-center and serve as
the contact strips against which battery terminals press.

Geometry (raw OCCT):
    pad        = MakeBox of (length, width, height)
    rib_a/b    = MakeBox of (length, rib_width, rib_height) — small thin strips
                 on top of the pad, ±contact_pitch/2 along width
    dock       = pad ∪ rib_a ∪ rib_b
    result     = body ∪ dock

v1 supports planar +Z/-Z target faces. Ribs run along the local X (length) axis.
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
    name="battery_dock_pad",
    category="modify/boss",
    level="atomic",
    summary="Flat battery-dock pad + 2 parallel contact ribs along its length. "
            "Pad seats the battery; ribs press against the terminals. "
            "v1 supports planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":          HistoryRule.MODIFIED_INHERIT,
        "battery_pad_solid":    HistoryRule.GENERATED_NEW,
        "battery_contact_ribs": HistoryRule.GENERATED_NEW,
    },
    produces_features=["battery_dock_pad", "contact_ribs"],
    preserves=["outer_envelope_outside_pad"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"rib_flatness_mm": 0.05}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_rib_flatness": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.ribs_outside_pad_width",
        "fm.contact_pitch_zero",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class BatteryDockPad(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of pad center",
        )
        length_mm: float = Field(gt=0, le=200,
                                  description="pad length along world X (rib axis)")
        width_mm: float = Field(gt=0, le=100,
                                 description="pad width along world Y")
        height_mm: float = Field(gt=0, le=20,
                                  description="pad height along face normal")
        contact_pitch_mm: float = Field(gt=0, le=80,
                                         description="rib center-to-center distance "
                                                     "across width")
        rib_width_mm: float = Field(default=0.6, gt=0, le=5,
                                     description="cross-section width of each rib (Y)")
        rib_height_mm: float = Field(default=0.4, gt=0, le=3,
                                      description="rib height above pad top")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        # validations
        if args.contact_pitch_mm + args.rib_width_mm > args.width_mm:
            raise ValueError(
                f"battery_dock_pad: contact_pitch_mm ({args.contact_pitch_mm}) + "
                f"rib_width_mm ({args.rib_width_mm}) > pad width "
                f"({args.width_mm}) — ribs spill outside pad"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"battery_dock_pad: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "battery_dock_pad v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0

        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        base_z = center[2]
        top_z = base_z + z_sign * args.height_mm
        rib_top_z = top_z + z_sign * args.rib_height_mm

        # pad box
        zmin_p = min(base_z, top_z)
        zmax_p = max(base_z, top_z)
        pad_lo = gp_Pnt(cx - args.length_mm / 2.0, cy - args.width_mm / 2.0, zmin_p)
        pad_hi = gp_Pnt(cx + args.length_mm / 2.0, cy + args.width_mm / 2.0, zmax_p)
        pad_mk = BRepPrimAPI_MakeBox(pad_lo, pad_hi)
        pad_mk.Build()
        if not pad_mk.IsDone():
            raise RuntimeError("battery_dock_pad: pad box build failed")
        pad_shape = pad_mk.Shape()

        # ribs (two boxes along length, centered at ±contact_pitch/2 in Y)
        zmin_r = min(top_z, rib_top_z)
        zmax_r = max(top_z, rib_top_z)
        rib_half_w = args.rib_width_mm / 2.0
        rib_a_y = cy - args.contact_pitch_mm / 2.0
        rib_b_y = cy + args.contact_pitch_mm / 2.0

        rib_a_lo = gp_Pnt(cx - args.length_mm / 2.0, rib_a_y - rib_half_w, zmin_r)
        rib_a_hi = gp_Pnt(cx + args.length_mm / 2.0, rib_a_y + rib_half_w, zmax_r)
        rib_a_mk = BRepPrimAPI_MakeBox(rib_a_lo, rib_a_hi)
        rib_a_mk.Build()
        if not rib_a_mk.IsDone():
            raise RuntimeError("battery_dock_pad: rib_a build failed")
        rib_a_shape = rib_a_mk.Shape()

        rib_b_lo = gp_Pnt(cx - args.length_mm / 2.0, rib_b_y - rib_half_w, zmin_r)
        rib_b_hi = gp_Pnt(cx + args.length_mm / 2.0, rib_b_y + rib_half_w, zmax_r)
        rib_b_mk = BRepPrimAPI_MakeBox(rib_b_lo, rib_b_hi)
        rib_b_mk.Build()
        if not rib_b_mk.IsDone():
            raise RuntimeError("battery_dock_pad: rib_b build failed")
        rib_b_shape = rib_b_mk.Shape()

        # pad + rib_a + rib_b
        f1 = BRepAlgoAPI_Fuse(pad_shape, rib_a_shape)
        f1.Build()
        if not f1.IsDone():
            raise RuntimeError("battery_dock_pad: pad+rib_a fuse failed")
        pa = f1.Shape()
        f2 = BRepAlgoAPI_Fuse(pa, rib_b_shape)
        f2.Build()
        if not f2.IsDone():
            raise RuntimeError("battery_dock_pad: pad+ribs fuse failed")
        dock_shape = f2.Shape()

        # body + dock
        f3 = BRepAlgoAPI_Fuse(shape, dock_shape)
        f3.Build()
        if not f3.IsDone():
            raise RuntimeError("battery_dock_pad: body fuse failed")
        new_shape = f3.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":           HistoryRule.MODIFIED_INHERIT,
                "battery_pad_solid":     HistoryRule.GENERATED_NEW,
                "battery_contact_ribs":  HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
