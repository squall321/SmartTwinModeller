"""esim_chip_pocket — atomic. Pocket for an embedded SIM (eUICC) chip.

An eSIM chip is a small surface-mount MFF2 part that lives on the PCB but,
on mid-frame mounted PCBs, is recessed into the metal frame so its top sits
flush with the surrounding shielding. This skill cuts:

    1. **chip pocket** — rectangular cavity matching the chip footprint,
       sunk to ``depth_mm`` along the face normal.
    2. **bonding pads** (optional) — two thin coplanar rectangular reliefs
       on the +X and -X sides of the chip body. They share the chip floor
       and extend half the chip width laterally. ``with_bonding_pads``
       gates this stage; default True.

Geometry (raw OCCT, planar ±Z host faces v1):
    chip_box = MakeBox(chip_l × chip_w × depth)         (centered at xy)
    pad_l_box, pad_r_box = MakeBox(chip_w/2 × chip_w × depth)   (optional)
    tool = fuse(chip_box, pad_l_box, pad_r_box)
    result = body \\ tool
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
    name="esim_chip_pocket",
    category="modify/pocket",
    level="atomic",
    summary="Rectangular pocket for an embedded-SIM (eUICC / MFF2) chip with "
            "optional bonding-pad reliefs on +X and -X sides. v1 supports "
            "planar ±Z host faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":       HistoryRule.MODIFIED_INHERIT,
        "chip_well":         HistoryRule.GENERATED_NEW,
        "bonding_pad_well":  HistoryRule.GENERATED_NEW,
        "consumed_volume":   HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["esim_chip_pocket"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3,
                              "extras": {"flat_bottom_endmill": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_chip_seat": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.esim_pocket_exits_body",
        "fm.esim_pocket_too_close_to_edge",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class EsimChipPocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of chip pocket center",
        )
        chip_l_mm: float = Field(default=5.0, gt=0, le=30,
                                  description="chip length along world X")
        chip_w_mm: float = Field(default=6.0, gt=0, le=30,
                                  description="chip width along world Y")
        depth_mm: float = Field(default=0.6, gt=0, le=10,
                                 description="pocket depth (axial)")
        with_bonding_pads: bool = Field(default=True,
                                         description="if True, add lateral "
                                                     "bonding-pad reliefs on "
                                                     "the ±X sides of the chip")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"esim_chip_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "esim_chip_pocket v1: planar ±Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        face_z = center[2]
        floor_z = face_z - z_sign * args.depth_mm
        overshoot = 0.05
        zmin = min(face_z, floor_z)
        zmax = max(face_z, floor_z)
        if z_sign > 0:
            zmax += overshoot
        else:
            zmin -= overshoot

        L = args.chip_l_mm
        W = args.chip_w_mm

        # 1) Chip rectangular pocket
        chip_lo = gp_Pnt(cx - L / 2.0, cy - W / 2.0, zmin)
        chip_hi = gp_Pnt(cx + L / 2.0, cy + W / 2.0, zmax)
        chip_mk = BRepPrimAPI_MakeBox(chip_lo, chip_hi)
        chip_mk.Build()
        if not chip_mk.IsDone():
            raise RuntimeError("esim_chip_pocket: chip box build failed")
        tool = chip_mk.Shape()

        # 2) Bonding-pad reliefs on +X and -X — each pad_l_mm = chip_w/2,
        #    chip_w wide (same Y extent), same depth.
        if args.with_bonding_pads:
            pad_l = W / 2.0
            # Right pad (+X)
            r_lo = gp_Pnt(cx + L / 2.0, cy - W / 2.0, zmin)
            r_hi = gp_Pnt(cx + L / 2.0 + pad_l, cy + W / 2.0, zmax)
            r_mk = BRepPrimAPI_MakeBox(r_lo, r_hi)
            r_mk.Build()
            if not r_mk.IsDone():
                raise RuntimeError("esim_chip_pocket: +X pad build failed")
            fuse_r = BRepAlgoAPI_Fuse(tool, r_mk.Shape())
            fuse_r.Build()
            if not fuse_r.IsDone():
                raise RuntimeError("esim_chip_pocket: chip+R-pad fuse failed")
            tool = fuse_r.Shape()
            # Left pad (-X)
            l_lo = gp_Pnt(cx - L / 2.0 - pad_l, cy - W / 2.0, zmin)
            l_hi = gp_Pnt(cx - L / 2.0, cy + W / 2.0, zmax)
            l_mk = BRepPrimAPI_MakeBox(l_lo, l_hi)
            l_mk.Build()
            if not l_mk.IsDone():
                raise RuntimeError("esim_chip_pocket: -X pad build failed")
            fuse_l = BRepAlgoAPI_Fuse(tool, l_mk.Shape())
            fuse_l.Build()
            if not fuse_l.IsDone():
                raise RuntimeError("esim_chip_pocket: chip+L-pad fuse failed")
            tool = fuse_l.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("esim_chip_pocket: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":      HistoryRule.MODIFIED_INHERIT,
                "chip_well":        HistoryRule.GENERATED_NEW,
                "bonding_pad_well": HistoryRule.GENERATED_NEW,
                "consumed_volume":  HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
