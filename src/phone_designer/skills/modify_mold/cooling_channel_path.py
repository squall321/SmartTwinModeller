"""cooling_channel_path — atomic. Drill straight-line cooling channels.

For each consecutive pair of ``path_seed_points`` build a cylinder of
``channel_d_mm`` diameter aligned to the segment and subtract it from the
body via BRepAlgoAPI_Cut. The ``target_distance_to_cavity_mm`` is recorded
as a design intent (offset from cavity boundary the mold engineer asked for)
and reported in ``extras['cooling_report']``; the actual point coordinates
are taken from ``path_seed_points`` (caller is responsible for placing them
at the target distance from the cavity).

extras:
    cooling_report = {
        "channel_count": int,
        "channel_d_mm": float,
        "target_distance_to_cavity_mm": float,
        "segments": [{"start":[x,y,z], "end":[x,y,z], "length_mm":L}, ...],
        "total_length_mm": float,
    }
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _segment_cylinder(p0, p1, radius: float):
    """Build a TopoDS cylinder whose axis runs from p0 to p1."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    dz = p1[2] - p0[2]
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length < 1e-9:
        return None, 0.0
    axis = gp_Ax2(
        gp_Pnt(p0[0], p0[1], p0[2]),
        gp_Dir(dx / length, dy / length, dz / length),
    )
    return BRepPrimAPI_MakeCylinder(axis, radius, length).Shape(), length


@skill(
    name="cooling_channel_path",
    category="modify/mold",
    level="atomic",
    summary="Drill straight-line cooling channels through the mold body. "
            "Each consecutive pair in path_seed_points is connected by a "
            "cylindrical bore of channel_d_mm diameter cut from the body. "
            "target_distance_to_cavity_mm is an engineering intent recorded "
            "to extras['cooling_report'].",
    selector_kinds=[],
    history_rules={},
    produces_features=["cooling_channels"],
    preserves=["outer_envelope"],
    manufacturing={
        "die_cast_al":       {"min_wall_mm": 1.5},
        "injection_mold_pa": {"min_wall_mm": 1.0},
    },
    failure_modes=["fm.cooling_cut_failed", "fm.path_too_short"],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class CoolingChannelPath(SkillBase):
    class Args(BaseModel):
        channel_d_mm: float = Field(default=8.0, gt=0.0, le=50.0)
        target_distance_to_cavity_mm: float = Field(default=15.0, gt=0.0, le=500.0)
        path_seed_points: list[tuple[float, float, float]] = Field(
            ..., min_length=2,
            description="Ordered drill-path waypoints in body coordinates."
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        shape = _occt_shape(body)
        if shape is None:
            raise RuntimeError("cooling_channel_path: body is None")

        radius = args.channel_d_mm / 2.0
        result_shape = shape
        segments: list[dict[str, Any]] = []
        total_length = 0.0

        pts = [tuple(p) for p in args.path_seed_points]
        for p0, p1 in zip(pts[:-1], pts[1:]):
            cyl, length = _segment_cylinder(p0, p1, radius)
            if cyl is None:
                raise RuntimeError(
                    f"cooling_channel_path: zero-length segment {p0} -> {p1}"
                )
            cut = BRepAlgoAPI_Cut(result_shape, cyl)
            cut.Build()
            if not cut.IsDone():
                raise RuntimeError(
                    f"cooling_channel_path: boolean cut failed at "
                    f"segment {p0} -> {p1}"
                )
            result_shape = cut.Shape()
            segments.append({
                "start": [round(c, 4) for c in p0],
                "end":   [round(c, 4) for c in p1],
                "length_mm": round(length, 4),
            })
            total_length += length

        report = {
            "channel_count": len(segments),
            "channel_d_mm": args.channel_d_mm,
            "target_distance_to_cavity_mm": args.target_distance_to_cavity_mm,
            "segments": segments,
            "total_length_mm": round(total_length, 4),
        }

        return SkillResult(
            body=Part(result_shape),
            history=EntityHistoryMap(),
            extras={"cooling_report": report},
        )
