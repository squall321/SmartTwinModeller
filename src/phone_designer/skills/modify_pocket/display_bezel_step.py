"""display_bezel_step — atomic.

A shallow display-bezel step-down: a rectangular pocket for the display glass
to seat into, with a thin perimeter adhesive channel cut into the step floor
to bond the display to the housing.

Geometry (raw OCCT, ±Z planar host faces v1):
    step    : MakeBox(display_l x display_w x step_depth) cut from the body,
              centered on the face center.
    channel : ring-shape adhesive channel along the perimeter of the step,
              width `adhesive_w_mm`, depth `adhesive_d_mm`, built as
              (outer_box \\ inner_box) and cut below the step floor.

Result = body \\ (step ∪ channel).
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
    name="display_bezel_step",
    category="modify/pocket",
    level="atomic",
    summary="Shallow display-bezel step-down with a perimeter adhesive channel "
            "cut into the step floor. v1 supports planar ±Z host faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":      HistoryRule.MODIFIED_INHERIT,
        "bezel_step":       HistoryRule.GENERATED_NEW,
        "adhesive_channel": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["display_bezel_step", "adhesive_channel"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3, "min_fillet_r_mm": 0.2,
                              "extras": {"flatness_mm": 0.05}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_flatness": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.channel_outside_step",
        "fm.step_exits_body",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class DisplayBezelStep(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        display_l_mm: float = Field(gt=0, le=400,
                                     description="display outer length (X)")
        display_w_mm: float = Field(gt=0, le=400,
                                     description="display outer width (Y)")
        step_depth_mm: float = Field(default=0.6, gt=0, le=10,
                                      description="step-down depth")
        adhesive_w_mm: float = Field(default=1.5, gt=0, le=10,
                                      description="perimeter channel width")
        adhesive_d_mm: float = Field(default=0.2, gt=0, le=5,
                                      description="perimeter channel depth")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        aw = args.adhesive_w_mm
        if 2.0 * aw >= args.display_l_mm or 2.0 * aw >= args.display_w_mm:
            raise ValueError(
                f"display_bezel_step: adhesive_w_mm ({aw}) is too wide for "
                f"display outer ({args.display_l_mm} x {args.display_w_mm})"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"display_bezel_step: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "display_bezel_step v1: planar ±Z faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        cx, cy = center[0], center[1]
        face_z = center[2]
        step_bottom_z = face_z - z_sign * args.step_depth_mm
        channel_bottom_z = step_bottom_z - z_sign * args.adhesive_d_mm

        # Step box
        zmin_s = min(face_z, step_bottom_z)
        zmax_s = max(face_z, step_bottom_z)
        overshoot = 0.05
        zmin_so = zmin_s - overshoot if z_sign < 0 else zmin_s
        zmax_so = zmax_s + overshoot if z_sign > 0 else zmax_s
        hl = args.display_l_mm / 2.0
        hw = args.display_w_mm / 2.0
        step_lo = gp_Pnt(cx - hl, cy - hw, zmin_so)
        step_hi = gp_Pnt(cx + hl, cy + hw, zmax_so)
        step_mk = BRepPrimAPI_MakeBox(step_lo, step_hi)
        step_mk.Build()
        if not step_mk.IsDone():
            raise RuntimeError("display_bezel_step: step box build failed")
        step_shape = step_mk.Shape()

        # Adhesive ring channel = outer box \ inner box, below the step floor.
        zmin_c = min(step_bottom_z, channel_bottom_z)
        zmax_c = max(step_bottom_z, channel_bottom_z)
        outer_lo = gp_Pnt(cx - hl, cy - hw, zmin_c)
        outer_hi = gp_Pnt(cx + hl, cy + hw, zmax_c)
        outer_mk = BRepPrimAPI_MakeBox(outer_lo, outer_hi)
        outer_mk.Build()
        if not outer_mk.IsDone():
            raise RuntimeError("display_bezel_step: channel outer box failed")
        outer_c = outer_mk.Shape()

        inner_lo = gp_Pnt(cx - hl + aw, cy - hw + aw, zmin_c - 0.05)
        inner_hi = gp_Pnt(cx + hl - aw, cy + hw - aw, zmax_c + 0.05)
        inner_mk = BRepPrimAPI_MakeBox(inner_lo, inner_hi)
        inner_mk.Build()
        if not inner_mk.IsDone():
            raise RuntimeError("display_bezel_step: channel inner box failed")
        inner_c = inner_mk.Shape()

        ccut = BRepAlgoAPI_Cut(outer_c, inner_c)
        ccut.Build()
        if not ccut.IsDone():
            raise RuntimeError("display_bezel_step: channel ring build failed")
        channel_shape = ccut.Shape()

        # Union step + channel
        fuse = BRepAlgoAPI_Fuse(step_shape, channel_shape)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("display_bezel_step: step+channel fuse failed")
        tool_shape = fuse.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool_shape)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("display_bezel_step: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":      HistoryRule.MODIFIED_INHERIT,
                "bezel_step":       HistoryRule.GENERATED_NEW,
                "adhesive_channel": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
