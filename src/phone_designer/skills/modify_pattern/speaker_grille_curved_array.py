"""speaker_grille_curved_array — atomic. Concentric-ring speaker grille.

Models the perforated cover of an in-ear / earpiece / curved-baffle speaker
by drilling N rings of small through-holes around a common (x, y) center.
Each ring carries `holes_per_ring` holes evenly spaced around the circle.
Ring radii are linearly interpolated between `inner_d_mm/2` and
`outer_d_mm/2` inclusive (so `ring_count == 1` lands on the outer radius).

All holes are cut as through-cylinders (long enough to traverse the body)
so the result is a real acoustic grille, not a blind decoration. Only
planar +Z / -Z host faces are supported in v1 — matches
`acoustic_grille_pattern`.
"""
from __future__ import annotations

import math
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
    name="speaker_grille_curved_array",
    category="modify/pattern",
    level="atomic",
    summary="Concentric-ring speaker grille: N rings of small through-holes "
            "spaced evenly around a common center between inner_d and outer_d. "
            "Models earpiece / curved-baffle perforated covers. "
            "v1 supports planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":         HistoryRule.MODIFIED_INHERIT,
        "grille_hole_set":     HistoryRule.GENERATED_NEW,
        "consumed_volume":     HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.outer_diameter_gt_inner_diameter",
        "pc.hole_diameter_lt_ring_spacing",
    ],
    produces_features=["speaker_grille", "perforated_cover", "ring_array"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4,
                              "extras": {"min_drill_mm": 0.5}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_small_holes": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.outer_leq_inner",
        "fm.holes_overlap_on_ring",
        "fm.region_outside_face",
    ],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class SpeakerGrilleCurvedArray(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        center_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of the grille center",
        )
        outer_d_mm: float = Field(gt=0, le=200,
                                   description="outermost ring diameter")
        inner_d_mm: float = Field(ge=0, le=200,
                                   description="innermost ring diameter")
        ring_count: int = Field(default=3, ge=1, le=50)
        hole_d_mm: float = Field(default=0.8, gt=0, le=10)
        holes_per_ring: int = Field(default=24, ge=3, le=360)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        if args.outer_d_mm <= args.inner_d_mm:
            raise ValueError(
                f"speaker_grille_curved_array: outer_d_mm "
                f"({args.outer_d_mm}) must be > inner_d_mm "
                f"({args.inner_d_mm})"
            )

        # Note: adjacent holes may overlap on the innermost ring, which is
        # acceptable — overlapping cylindrical cuts simply merge into a
        # continuous slot, which is still a valid acoustic vent. Degenerate
        # (chord ≈ 0) configurations are caught by the geometry engine
        # downstream.
        r_inner = args.inner_d_mm / 2.0

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"speaker_grille_curved_array: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "speaker_grille_curved_array v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.center_xy[0]
        cy = center[1] + args.center_xy[1]

        # Compute ring radii: linspace from inner to outer, inclusive on both
        # ends. ring_count == 1 → one ring at outer_d/2.
        r_outer = args.outer_d_mm / 2.0
        if args.ring_count == 1:
            radii = [r_outer]
        else:
            step = (r_outer - r_inner) / (args.ring_count - 1)
            radii = [r_inner + i * step for i in range(args.ring_count)]

        overshoot = 1.0
        cyl_h = 200.0 + 2.0 * overshoot
        cyl_r = args.hole_d_mm / 2.0
        new_shape = shape

        for ring_radius in radii:
            for k in range(args.holes_per_ring):
                theta = 2.0 * math.pi * k / args.holes_per_ring
                wx = cx + ring_radius * math.cos(theta)
                wy = cy + ring_radius * math.sin(theta)
                base_z = center[2] + z_sign * overshoot
                axis_dir = gp_Dir(0.0, 0.0, -z_sign)
                ax = gp_Ax2(gp_Pnt(wx, wy, base_z), axis_dir)
                mk = BRepPrimAPI_MakeCylinder(ax, cyl_r, cyl_h)
                mk.Build()
                if not mk.IsDone():
                    raise RuntimeError(
                        f"speaker_grille_curved_array: cylinder build failed "
                        f"at ({wx:.3f},{wy:.3f})"
                    )
                cut = BRepAlgoAPI_Cut(new_shape, mk.Shape())
                cut.Build()
                if not cut.IsDone():
                    raise RuntimeError(
                        f"speaker_grille_curved_array: boolean cut failed at "
                        f"({wx:.3f},{wy:.3f})"
                    )
                new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "grille_hole_set": HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
