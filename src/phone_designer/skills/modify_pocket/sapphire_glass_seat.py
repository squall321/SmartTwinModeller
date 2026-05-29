"""sapphire_glass_seat — atomic. Stepped circular seat for a sapphire crystal.

Wearable watch / wearable case top normally seats a sapphire crystal glass
into a shallow circular recess. The pocket is stepped so the crystal sits
above an adhesive bond ring and below a gasket lip, giving:

  - a top circular pocket of diameter ``glass_d_mm`` and depth ``glass_t_mm``
    that captures the crystal itself,
  - an inner adhesive step of slightly smaller diameter
    (= glass_d_mm - 2·gasket_step_mm) cut a further ``adhesive_step_d_mm``
    deeper than the glass floor, so adhesive can sit on the step,
  - an even smaller central gasket step of diameter
    (= glass_d_mm - 4·gasket_step_mm) cut a further ``gasket_step_d_mm``
    below the adhesive floor — this is where a thin gasket / O-ring or
    EMI-foam ring is squeezed by the glass.

v1: planar ±Z host faces (typical watch top / bottom).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

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
    name="sapphire_glass_seat",
    category="modify/pocket",
    level="atomic",
    summary="Stepped circular seat for a sapphire crystal: glass pocket on top, "
            "adhesive bond ring below it, gasket step at the bottom. v1 supports "
            "planar ±Z host faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "glass_pocket":    HistoryRule.GENERATED_NEW,
        "adhesive_step":   HistoryRule.GENERATED_NEW,
        "gasket_step":     HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["sapphire_glass_seat", "stepped_pocket"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3, "min_fillet_r_mm": 0.2,
                              "extras": {"flatness_mm": 0.03}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_flatness": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.glass_seat_exits_body",
        "fm.gasket_step_collapses_to_zero",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class SapphireGlassSeat(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        glass_d_mm: float = Field(gt=0, le=80,
                                   description="sapphire crystal outer diameter (top pocket)")
        glass_t_mm: float = Field(default=0.8, gt=0, le=5,
                                   description="depth of the top crystal pocket = glass thickness")
        adhesive_step_d_mm: float = Field(default=0.4, gt=0, le=5,
                                           description="extra depth of the adhesive step below the glass floor")
        gasket_step_d_mm: float = Field(default=0.15, gt=0, le=5,
                                         description="extra depth of the gasket step below the adhesive floor")

        @model_validator(mode="after")
        def _steps_fit_in_glass(self):
            # Each radial step shrinks the pocket diameter by 2 * gasket_step_d_mm
            # (used as a radial shoulder width for both inner steps). The
            # innermost diameter must remain positive.
            if self.glass_d_mm - 4.0 * self.gasket_step_d_mm <= 0.0:
                raise ValueError(
                    f"sapphire_glass_seat: gasket_step_d_mm "
                    f"({self.gasket_step_d_mm}) too large for "
                    f"glass_d_mm ({self.glass_d_mm}); inner step collapses"
                )
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"sapphire_glass_seat: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "sapphire_glass_seat v1: planar ±Z host faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        cx, cy = center[0], center[1]
        face_z = center[2]
        # cylinder axis points INTO the body (opposite of outward normal)
        direction = gp_Dir(0.0, 0.0, -z_sign)

        overshoot = 0.05

        # ── top glass pocket ────────────────────────────────────────────────
        glass_r = args.glass_d_mm / 2.0
        # Anchor cylinder a hair above face so its top face is outside body —
        # gives a clean opening when we subtract.
        glass_top_z = face_z + z_sign * overshoot
        glass_ax = gp_Ax2(gp_Pnt(cx, cy, glass_top_z), direction)
        glass_mk = BRepPrimAPI_MakeCylinder(
            glass_ax, glass_r, args.glass_t_mm + overshoot,
        )
        glass_mk.Build()
        if not glass_mk.IsDone():
            raise RuntimeError("sapphire_glass_seat: glass pocket cyl failed")
        glass_pocket = glass_mk.Shape()

        # ── adhesive step (inner ring under glass floor) ────────────────────
        adh_r = (args.glass_d_mm - 2.0 * args.gasket_step_d_mm) / 2.0
        adh_top_z = face_z - z_sign * args.glass_t_mm
        adh_ax = gp_Ax2(gp_Pnt(cx, cy, adh_top_z), direction)
        adh_mk = BRepPrimAPI_MakeCylinder(
            adh_ax, adh_r, args.adhesive_step_d_mm + overshoot,
        )
        adh_mk.Build()
        if not adh_mk.IsDone():
            raise RuntimeError("sapphire_glass_seat: adhesive step cyl failed")
        adh_step = adh_mk.Shape()

        # ── gasket step (innermost, deepest) ────────────────────────────────
        gsk_r = (args.glass_d_mm - 4.0 * args.gasket_step_d_mm) / 2.0
        gsk_top_z = face_z - z_sign * (args.glass_t_mm + args.adhesive_step_d_mm)
        gsk_ax = gp_Ax2(gp_Pnt(cx, cy, gsk_top_z), direction)
        gsk_mk = BRepPrimAPI_MakeCylinder(
            gsk_ax, gsk_r, args.gasket_step_d_mm + overshoot,
        )
        gsk_mk.Build()
        if not gsk_mk.IsDone():
            raise RuntimeError("sapphire_glass_seat: gasket step cyl failed")
        gsk_step = gsk_mk.Shape()

        # Fuse the three cylinders into one stepped tool, then subtract once.
        f1 = BRepAlgoAPI_Fuse(glass_pocket, adh_step)
        f1.Build()
        if not f1.IsDone():
            raise RuntimeError("sapphire_glass_seat: glass+adh fuse failed")
        f2 = BRepAlgoAPI_Fuse(f1.Shape(), gsk_step)
        f2.Build()
        if not f2.IsDone():
            raise RuntimeError("sapphire_glass_seat: +gasket fuse failed")
        tool = f2.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("sapphire_glass_seat: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "glass_pocket":    HistoryRule.GENERATED_NEW,
                "adhesive_step":   HistoryRule.GENERATED_NEW,
                "gasket_step":     HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
