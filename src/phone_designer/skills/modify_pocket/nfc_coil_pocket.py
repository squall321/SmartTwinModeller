"""nfc_coil_pocket — atomic. Annular pocket for an NFC antenna coil + ferrite step.

NFC antennas are flat copper-wound coils backed by a thin sintered ferrite
sheet that focuses flux through the rear cover. This skill cuts:

    1. **coil ring** — an annular pocket (outer d − inner d) of depth
       ``depth_mm`` for the wound coil itself.
    2. **ferrite step** (optional) — a wider, shallower coaxial step pocket
       sitting INSIDE the coil ring's inner cylinder. It is half the coil
       depth so the ferrite sheet seats above the coil opening. Only built
       when ``with_ferrite`` is true.

Geometry (raw OCCT, planar ±Z host faces v1):
    coil_pocket    = MakeCylinder(outer_r) − MakeCylinder(inner_r)  (annular)
    ferrite_pocket = MakeCylinder(inner_r) at half depth     (with_ferrite)
    tool           = fuse(coil_pocket, ferrite_pocket)
    result         = body \\ tool

The ferrite pocket is a *step* — wider would risk consuming the coil wall, so
it is constrained to inner_r and sits axially shallower than the coil.
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
    name="nfc_coil_pocket",
    category="modify/pocket",
    level="atomic",
    summary="Annular pocket for an NFC antenna coil with an optional inner "
            "ferrite step. v1 supports planar ±Z host faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "coil_well":       HistoryRule.GENERATED_NEW,
        "ferrite_step":    HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.coil_inner_lt_outer",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["nfc_coil_pocket", "annular_pocket"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4,
                              "extras": {"flat_bottom_endmill": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.coil_inner_ge_outer",
        "fm.coil_pocket_exits_body",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class NfcCoilPocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        center_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of coil center",
        )
        coil_outer_d_mm: float = Field(default=30.0, gt=0, le=200,
                                        description="coil outer diameter")
        coil_inner_d_mm: float = Field(default=20.0, gt=0, le=200,
                                        description="coil inner diameter")
        depth_mm: float = Field(default=0.4, gt=0, le=10,
                                 description="coil pocket depth (axial)")
        with_ferrite: bool = Field(default=True,
                                    description="if True, add a coaxial "
                                                "ferrite step pocket inside "
                                                "the coil inner radius")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        if args.coil_inner_d_mm >= args.coil_outer_d_mm:
            raise ValueError(
                f"nfc_coil_pocket: coil_inner_d ({args.coil_inner_d_mm}) >= "
                f"coil_outer_d ({args.coil_outer_d_mm}) — coil ring has no "
                f"radial width"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"nfc_coil_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "nfc_coil_pocket v1: planar ±Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.center_xy[0]
        cy = center[1] + args.center_xy[1]
        face_z = center[2]
        # axis points INTO the body (opposite face normal)
        direction = gp_Dir(0.0, 0.0, -z_sign)

        overshoot = 0.05
        outer_r = args.coil_outer_d_mm / 2.0
        inner_r = args.coil_inner_d_mm / 2.0

        # 1) Coil ring = outer cyl − inner cyl. Anchor base slightly above face
        base_z = face_z + z_sign * overshoot
        outer_ax = gp_Ax2(gp_Pnt(cx, cy, base_z), direction)
        outer_mk = BRepPrimAPI_MakeCylinder(
            outer_ax, outer_r, args.depth_mm + 2.0 * overshoot,
        )
        outer_mk.Build()
        if not outer_mk.IsDone():
            raise RuntimeError("nfc_coil_pocket: outer cylinder failed")

        inner_ax = gp_Ax2(gp_Pnt(cx, cy, base_z), direction)
        inner_mk = BRepPrimAPI_MakeCylinder(
            inner_ax, inner_r, args.depth_mm + 4.0 * overshoot,
        )
        inner_mk.Build()
        if not inner_mk.IsDone():
            raise RuntimeError("nfc_coil_pocket: inner cylinder failed")

        ring_cut = BRepAlgoAPI_Cut(outer_mk.Shape(), inner_mk.Shape())
        ring_cut.Build()
        if not ring_cut.IsDone():
            raise RuntimeError("nfc_coil_pocket: annular ring build failed")
        tool = ring_cut.Shape()

        # 2) Optional ferrite step: coaxial inner-r cylinder, half depth
        if args.with_ferrite:
            ferr_depth = args.depth_mm * 0.5
            ferr_ax = gp_Ax2(gp_Pnt(cx, cy, base_z), direction)
            ferr_mk = BRepPrimAPI_MakeCylinder(
                ferr_ax, inner_r, ferr_depth + overshoot,
            )
            ferr_mk.Build()
            if not ferr_mk.IsDone():
                raise RuntimeError("nfc_coil_pocket: ferrite step build failed")
            fuse = BRepAlgoAPI_Fuse(tool, ferr_mk.Shape())
            fuse.Build()
            if not fuse.IsDone():
                raise RuntimeError("nfc_coil_pocket: ring+ferrite fuse failed")
            tool = fuse.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("nfc_coil_pocket: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "coil_well":       HistoryRule.GENERATED_NEW,
                "ferrite_step":    HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
