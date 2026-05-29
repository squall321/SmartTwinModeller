"""display_bezel_step_with_adhesive_groove — atomic.

A display bezel step-down: shallow rectangular pocket for the display glass
to seat into, with a thin adhesive channel cut around the outer perimeter of
the step so adhesive can bond display to housing.

Geometry (raw OCCT, ±Z planar host faces v1):
    step    : MakeBox(display_outer_w x display_outer_h x step_depth) cut
              from the body, centered on the face center.
    groove  : ring-shape adhesive channel along the perimeter of the step,
              width adhesive_groove_width_mm, depth adhesive_groove_depth_mm,
              built as (outer_box \\ inner_box) and cut from the step floor.

Result = body \\ (step ∪ groove).
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
    name="display_bezel_step_with_adhesive_groove",
    category="modify/pocket",
    level="atomic",
    summary="Shallow display-bezel step-down with a perimeter adhesive channel "
            "cut into the step floor. Seats display glass + bonds it to the "
            "housing. v1 supports planar ±Z host faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "bezel_step":      HistoryRule.GENERATED_NEW,
        "adhesive_groove": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["display_bezel_step", "adhesive_groove"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3, "min_fillet_r_mm": 0.2,
                              "extras": {"flatness_mm": 0.05}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_flatness": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.groove_outside_step",
        "fm.step_exits_body",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class DisplayBezelStepWithAdhesiveGroove(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        display_outer_w_mm: float = Field(gt=0, le=400,
                                          description="display outer width (X)")
        display_outer_h_mm: float = Field(gt=0, le=400,
                                          description="display outer height (Y)")
        step_depth_mm: float = Field(default=0.6, gt=0, le=10,
                                      description="step-down depth")
        adhesive_groove_width_mm: float = Field(default=1.5, gt=0, le=10,
                                                 description="perimeter groove width")
        adhesive_groove_depth_mm: float = Field(default=0.2, gt=0, le=5,
                                                 description="groove depth below step")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        gw = args.adhesive_groove_width_mm
        if 2.0 * gw >= args.display_outer_w_mm or 2.0 * gw >= args.display_outer_h_mm:
            raise ValueError(
                f"display_bezel_step_with_adhesive_groove: "
                f"adhesive_groove_width_mm ({gw}) is too wide for display outer "
                f"({args.display_outer_w_mm} x {args.display_outer_h_mm})"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"display_bezel_step_with_adhesive_groove: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "display_bezel_step_with_adhesive_groove v1: planar ±Z faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        cx, cy = center[0], center[1]
        face_z = center[2]
        step_bottom_z = face_z - z_sign * args.step_depth_mm
        groove_bottom_z = step_bottom_z - z_sign * args.adhesive_groove_depth_mm

        # Step box
        zmin_s = min(face_z, step_bottom_z)
        zmax_s = max(face_z, step_bottom_z)
        # overshoot above face for a clean opening
        overshoot = 0.05
        zmin_so = zmin_s - overshoot if z_sign < 0 else zmin_s
        zmax_so = zmax_s + overshoot if z_sign > 0 else zmax_s
        hw = args.display_outer_w_mm / 2.0
        hh = args.display_outer_h_mm / 2.0
        step_lo = gp_Pnt(cx - hw, cy - hh, zmin_so)
        step_hi = gp_Pnt(cx + hw, cy + hh, zmax_so)
        step_mk = BRepPrimAPI_MakeBox(step_lo, step_hi)
        step_mk.Build()
        if not step_mk.IsDone():
            raise RuntimeError("display_bezel_step: step box build failed")
        step_shape = step_mk.Shape()

        # Groove ring = outer box \ inner box, sitting at the step floor.
        zmin_g = min(step_bottom_z, groove_bottom_z)
        zmax_g = max(step_bottom_z, groove_bottom_z)
        outer_lo = gp_Pnt(cx - hw, cy - hh, zmin_g)
        outer_hi = gp_Pnt(cx + hw, cy + hh, zmax_g)
        outer_mk = BRepPrimAPI_MakeBox(outer_lo, outer_hi)
        outer_mk.Build()
        if not outer_mk.IsDone():
            raise RuntimeError("display_bezel_step: groove outer box failed")
        outer_g = outer_mk.Shape()

        inner_lo = gp_Pnt(cx - hw + gw, cy - hh + gw, zmin_g - 0.05)
        inner_hi = gp_Pnt(cx + hw - gw, cy + hh - gw, zmax_g + 0.05)
        inner_mk = BRepPrimAPI_MakeBox(inner_lo, inner_hi)
        inner_mk.Build()
        if not inner_mk.IsDone():
            raise RuntimeError("display_bezel_step: groove inner box failed")
        inner_g = inner_mk.Shape()

        gcut = BRepAlgoAPI_Cut(outer_g, inner_g)
        gcut.Build()
        if not gcut.IsDone():
            raise RuntimeError("display_bezel_step: groove ring build failed")
        groove_shape = gcut.Shape()

        # Union step + groove
        fuse = BRepAlgoAPI_Fuse(step_shape, groove_shape)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("display_bezel_step: step+groove fuse failed")
        tool_shape = fuse.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool_shape)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("display_bezel_step: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "bezel_step":      HistoryRule.GENERATED_NEW,
                "adhesive_groove": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
