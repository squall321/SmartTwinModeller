"""revolve_boss — atomic.

Revolve a 2D profile around an arbitrary axis and FUSE the resulting solid
of revolution into the body. Used for raised ring features, lathe-style
external bosses, decorative collars around shafts.

Args:
    profile_sketch: SketchSpec       — 2D profile in local XY
    axis_origin:    tuple (x,y,z)    — world point on the revolution axis
    axis_direction: tuple (x,y,z)    — world axis direction (normalized internally)
    angle_deg:      float = 360.0    — sweep angle
    top_blend_r_mm: float = 0.0      — fillet edges near the profile-top Z plane

Convention (profile orientation):
    Same as revolve_pocket — local XY is mapped to world XZ (local x -> world x,
    local y -> world z), then translated by axis_origin, then revolved around
    `axis_direction`. The profile must lie on the +x half-plane (x >= 0) before
    translation so it does NOT cross the axis.

    LLM users: for a raised ring on top of a box centered at origin, with
    box top at z=10:
        profile = {kind: rectangle, length_mm: 2, width_mm: 1.5,
                   center_x_mm: 8, center_y_mm: 0.75}
        axis_origin   = (0, 0, 10)
        axis_direction= (0, 0, 1)
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec
from phone_designer.skills.modify_pocket.revolve_pocket import (
    _build_revolved_solid,
)


@skill(
    name="revolve_boss",
    category="modify/boss",
    level="atomic",
    summary="Revolve a 2D profile around an arbitrary axis and fuse the "
            "resulting solid of revolution to the body. Standard pattern for "
            "raised rings/collars, lathe-style external bosses.",
    selector_kinds=[],
    history_rules={
        "revolve_walls":  HistoryRule.GENERATED_NEW,
        "revolve_top":    HistoryRule.GENERATED_NEW,
        "revolve_blends": HistoryRule.GENERATED_NEW,
    },
    preconditions=[],
    produces_features=["plateau", "ring_boss"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":   {"min_wall_mm": 0.5},
        "die_cast_al": {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "turning":     {"min_wall_mm": 0.3},
    },
    failure_modes=[
        "fm.profile_crosses_axis",
        "fm.revolved_solid_misses_body",
    ],
    cost_hint=0.30,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class RevolveBoss(SkillBase):
    class Args(BaseModel):
        profile_sketch: SketchSpec
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        angle_deg: float = Field(default=360.0, gt=0.0, le=360.0)
        top_blend_r_mm: float = Field(default=0.0, ge=0.0, le=10.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

        from phone_designer.skills.modify_pocket.extrude_pocket_blended import (
            _fillet_edges_near_z,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body

        revolved = _build_revolved_solid(
            args.profile_sketch,
            args.axis_origin,
            args.axis_direction,
            args.angle_deg,
        )

        fuse = BRepAlgoAPI_Fuse(shape, revolved)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("revolve_boss fuse failed")
        new_shape = fuse.Shape()

        if args.top_blend_r_mm > 0:
            # Top of the boss ring ~ axis_origin.z + (profile center_y + half height)
            # For a rectangle profile center_y_mm + width_mm/2 gives the top in
            # local y, which is world z after orientation. For other sketches we
            # take center_y_mm as an approximation.
            center_y = getattr(args.profile_sketch, "center_y_mm", 0.0)
            half_h = 0.0
            for attr in ("width_mm", "radius_y_mm"):
                if hasattr(args.profile_sketch, attr):
                    half_h = float(getattr(args.profile_sketch, attr)) / 2.0
                    break
            target_z = args.axis_origin[2] + center_y + half_h
            new_shape, _ = _fillet_edges_near_z(
                new_shape, target_z, args.top_blend_r_mm,
            )

        history = EntityHistoryMap(
            rules={
                "revolve_walls":  HistoryRule.GENERATED_NEW,
                "revolve_top":    HistoryRule.GENERATED_NEW,
                "revolve_blends": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
