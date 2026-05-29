"""helical_thread_internal — atomic.

Cut an internal (female) helical thread on a cylindrical hole in the body.
Mirror image of helical_thread_external: profile apex points OUTWARD from
axis (into the surrounding material), base sits on the bore radius.

Args:
    axis_origin:       tuple (x,y,z) — center of the hole's axis
    axis_direction:    tuple (x,y,z) — axis direction (normalized internally)
    outer_radius_mm:   the hole's inner radius BEFORE threading. Acts as the
                       "outer" radius of the thread profile from the axis'
                       point of view — the profile sits on x=outer_radius and
                       its apex extends to x=outer_radius+thread_height.
    pitch_mm:          distance per turn along the axis
    turns:             number of turns
    thread_height_mm:  radial depth of the thread (into the wall material)
    thread_angle_deg:  V-thread included angle (60° = ISO metric default)

Pre-requisite:
    The body must already contain a cylindrical hole of radius `outer_radius_mm`
    centered on the given axis. Use `hole` skill first.

Implementation:
    Same as external (see helical_thread_external) but profile apex extends
    radially OUTWARD from the axis. Then the swept solid is subtracted from
    the surrounding body, carving a helical groove into the wall.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket.helical_thread_external import (
    _sweep_thread_solid,
)


@skill(
    name="helical_thread_internal",
    category="modify/pocket",
    level="atomic",
    summary="Cut a real internal (female) helical V-thread inside an existing "
            "cylindrical hole. Builds a helix wire, sweeps a V-profile whose "
            "apex points OUTWARD into the wall, and subtracts. Requires the "
            "hole to be pre-drilled at the same axis/radius (e.g. via `hole`).",
    selector_kinds=[],
    history_rules={
        "thread_flanks":   HistoryRule.GENERATED_NEW,
        "thread_root":     HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.thread_radius_positive",
        "pc.thread_pitch_positive",
        "pc.hole_exists_at_axis",
    ],
    produces_features=["internal_thread", "helical_groove"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_5axis":   {"min_wall_mm": 0.5, "extras": {"thread_mill_required": True}},
        "tapping":     {"min_wall_mm": 0.3, "extras": {"hand_or_machine_tap": True}},
    },
    failure_modes=[
        "fm.thread_solid_misses_wall",
        "fm.helix_intersects_itself_at_steep_pitch",
    ],
    cost_hint=0.55,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class HelicalThreadInternal(SkillBase):
    class Args(BaseModel):
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        outer_radius_mm: float = Field(gt=0.0, le=200.0,
                                        description="Hole inner radius BEFORE threading.")
        pitch_mm: float = Field(gt=0.0, le=50.0)
        turns: float = Field(gt=0.0, le=200.0)
        thread_height_mm: float = Field(gt=0.0, le=20.0)
        thread_angle_deg: float = Field(default=60.0, gt=0.0, lt=180.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        shape = body.wrapped if hasattr(body, "wrapped") else body

        thread_solid = _sweep_thread_solid(
            radius=args.outer_radius_mm,
            pitch=args.pitch_mm,
            turns=args.turns,
            thread_height=args.thread_height_mm,
            thread_angle_deg=args.thread_angle_deg,
            axis_origin=args.axis_origin,
            axis_direction=args.axis_direction,
            external=False,
        )

        cut = BRepAlgoAPI_Cut(shape, thread_solid)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("helical_thread_internal cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "thread_flanks":   HistoryRule.GENERATED_NEW,
                "thread_root":     HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
