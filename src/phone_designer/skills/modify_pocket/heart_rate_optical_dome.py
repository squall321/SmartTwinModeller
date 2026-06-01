"""heart_rate_optical_dome — atomic.

Photo-plethysmography (PPG / heart-rate) optical window pocket. Carves a
central circular pocket for the optical window cover plus two adjacent
small cylindrical pockets for the green / IR LED emitter and photodiode
receiver. The central window is a shallow `dome_height_mm` recess sized
to `optical_window_d_mm`; the two LED clearance pockets sit on the +X /
-X axis of the window center at one window-radius + LED-radius outward.

v1 supports planar +Z / -Z target faces.
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
    name="heart_rate_optical_dome",
    category="modify/pocket",
    level="atomic",
    summary="PPG heart-rate optical window + two LED clearance pockets. "
            "Central optical_window_d_mm circular recess at dome_height_mm "
            "depth, flanked by two led_clearance_d_mm × dome_height_mm "
            "circular pockets on the ±X axis. v1 supports planar +Z/-Z "
            "faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "window_wall":     HistoryRule.GENERATED_NEW,
        "window_floor":    HistoryRule.GENERATED_NEW,
        "led_pockets":     HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["heart_rate_optical_dome"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"flat_floor_required": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.led_overlaps_window",
        "fm.pocket_exits_body",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class HeartRateOpticalDome(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of window center, offset from face centroid.",
        )
        optical_window_d_mm: float = Field(default=7.0, gt=0, le=50,
                                            description="central optical window diameter")
        dome_height_mm: float = Field(default=0.4, gt=0, le=5.0,
                                       description="window/LED recess depth (axial)")
        led_clearance_d_mm: float = Field(default=2.5, gt=0, le=20,
                                           description="LED clearance pocket diameter")
        led_gap_mm: float = Field(
            default=0.5, ge=0.0, le=20.0,
            description="extra clearance between window edge and LED edge",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"heart_rate_optical_dome: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "heart_rate_optical_dome v1: planar +Z/-Z target faces only"
            )
        nz_sign = 1.0 if normal[2] > 0 else -1.0
        axis_dir = gp_Dir(0.0, 0.0, -nz_sign)

        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        cz = center[2]

        # Stage 1 — central optical window
        win_base = gp_Pnt(cx, cy, cz)
        win_ax = gp_Ax2(win_base, axis_dir)
        win_mk = BRepPrimAPI_MakeCylinder(
            win_ax, args.optical_window_d_mm / 2.0, args.dome_height_mm,
        )
        win_mk.Build()
        if not win_mk.IsDone():
            raise RuntimeError("heart_rate_optical_dome: window build failed")
        cut_a = BRepAlgoAPI_Cut(shape, win_mk.Shape())
        cut_a.Build()
        if not cut_a.IsDone():
            raise RuntimeError("heart_rate_optical_dome: window cut failed")
        new_shape = cut_a.Shape()

        # Stages 2-3 — two LED pockets on ±X axis.
        led_offset = (
            args.optical_window_d_mm / 2.0
            + args.led_gap_mm
            + args.led_clearance_d_mm / 2.0
        )
        for sign in (+1.0, -1.0):
            led_base = gp_Pnt(cx + sign * led_offset, cy, cz)
            led_ax = gp_Ax2(led_base, axis_dir)
            led_mk = BRepPrimAPI_MakeCylinder(
                led_ax, args.led_clearance_d_mm / 2.0, args.dome_height_mm,
            )
            led_mk.Build()
            if not led_mk.IsDone():
                raise RuntimeError(
                    f"heart_rate_optical_dome: LED build failed (sign={sign})"
                )
            cut_b = BRepAlgoAPI_Cut(new_shape, led_mk.Shape())
            cut_b.Build()
            if not cut_b.IsDone():
                raise RuntimeError(
                    f"heart_rate_optical_dome: LED cut failed (sign={sign})"
                )
            new_shape = cut_b.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "window_wall":     HistoryRule.GENERATED_NEW,
                "window_floor":    HistoryRule.GENERATED_NEW,
                "led_pockets":     HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
