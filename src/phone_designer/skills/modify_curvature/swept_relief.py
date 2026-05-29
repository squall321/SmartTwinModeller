"""swept_relief — atomic. linear path 의 사각 profile sweep cutout.

Phase 4 v1: 직선 path + 직사각 profile (cross-section). subtract.
Phase 4 v2: 곡선 path, 임의 profile.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="swept_relief",
    category="modify/curvature",
    level="atomic",
    summary="Linear swept relief (cutout) — rectangular cross-section swept along a "
            "straight path. v1: straight path only. v2: arbitrary curve path.",
    selector_kinds=[],
    history_rules={
        "body_faces":      HistoryRule.MODIFIED_INHERIT,
        "sweep_walls":     HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    produces_features=["sweep_relief"],
    preserves=["outer_envelope_outside_relief"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.path_zero_length", "fm.cross_section_outside_body"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class SweptRelief(SkillBase):
    class Args(BaseModel):
        start: tuple[float, float, float]
        end: tuple[float, float, float]
        width_mm: float = Field(gt=0, le=50,
                                 description="path 의 수직 방향 폭")
        depth_mm: float = Field(gt=0, le=50,
                                 description="path 의 위/아래 방향 깊이")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Align, Axis, Box, Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        import math

        dx = args.end[0] - args.start[0]
        dy = args.end[1] - args.start[1]
        dz = args.end[2] - args.start[2]
        L = (dx ** 2 + dy ** 2 + dz ** 2) ** 0.5
        if L < 1e-3:
            raise ValueError("swept_relief: start == end")

        # 단순화 v1: path 가 XY plane 위라 가정 (dz=0). dz!=0 이면 NotImplementedError.
        if abs(dz) > 0.01:
            raise NotImplementedError(
                "swept_relief v1: XY-plane path 만 지원 (start.z == end.z)"
            )

        # box: length=L (path), width=width_mm (수직 in XY), height=depth_mm (Z)
        tool = Box(L, args.width_mm, args.depth_mm,
                   align=(Align.MIN, Align.CENTER, Align.CENTER))

        # X 축에서 path 방향으로 회전
        angle = math.degrees(math.atan2(dy, dx))
        tool = tool.rotate(Axis.Z, angle)

        # path 의 시작점에 위치
        tool.position = args.start

        shape = body.wrapped if hasattr(body, "wrapped") else body
        cut = BRepAlgoAPI_Cut(shape, tool.wrapped)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("swept_relief cut 실패")

        history = EntityHistoryMap(
            rules={
                "body_faces":      HistoryRule.MODIFIED_INHERIT,
                "sweep_walls":     HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(cut.Shape()), history=history)
