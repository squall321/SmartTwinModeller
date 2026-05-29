"""rib — atomic. 직선 segment 의 stiffening rib (사출 보강).

start → end 의 직선 경로에 width 폭 × height 높이 + draft.
Phase 4 v1: 직선 only. v2: 곡선 path (sweep).
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="rib",
    category="modify/boss",
    level="atomic",
    summary="Straight stiffening rib — extruded box from start to end with given width/height. "
            "Optional draft angle for injection molding. v1 supports straight path only.",
    selector_kinds=[],
    history_rules={"rib_solid": HistoryRule.GENERATED_NEW},
    produces_features=["rib"],
    preserves=["outer_envelope_outside_rib"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.6, "min_draft_deg": 0.5,
                              "extras": {"width_recommended": "wall_thickness * 0.6"}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "cnc_3axis":         {"min_wall_mm": 0.5},
    },
    failure_modes=["fm.rib_zero_length", "fm.rib_outside_body"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class Rib(SkillBase):
    class Args(BaseModel):
        start: tuple[float, float, float] = Field(description="rib 시작점")
        end: tuple[float, float, float] = Field(description="rib 끝점")
        width_mm: float = Field(default=1.0, gt=0, le=20,
                                 description="rib 두께 (path 의 수직 방향)")
        height_mm: float = Field(default=3.0, gt=0, le=30,
                                  description="rib 의 +Z 높이")
        draft_deg: float = Field(default=0.0, ge=0, le=5,
                                  description="draft angle (사출용)")
        up_axis: Literal["+Z", "-Z"] = Field(
            default="+Z",
            description="rib 가 자라나는 방향 (housing 내부에 따라)",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Align, Box, Axis, Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

        # 직선 path
        dx = args.end[0] - args.start[0]
        dy = args.end[1] - args.start[1]
        path_length = (dx ** 2 + dy ** 2) ** 0.5
        if path_length < 1e-3:
            raise ValueError("rib: start == end (zero length)")

        # rib box 만들기 — X 방향 길이 = path_length, Y 방향 = width, Z 방향 = height
        # 그 다음 path 방향으로 회전 + 평행이동
        # Z direction
        z_sign = 1.0 if args.up_axis == "+Z" else -1.0

        rib_box = Box(
            length=path_length,
            width=args.width_mm,
            height=args.height_mm,
            align=(Align.MIN, Align.CENTER, Align.MIN if z_sign > 0 else Align.MAX),
        )

        # 회전: X 축 (Box 의 length 방향) → path 방향
        angle = math.degrees(math.atan2(dy, dx))
        rib_box = rib_box.rotate(Axis.Z, angle)

        # 위치: start
        rib_box.position = (args.start[0], args.start[1], args.start[2])

        # union
        shape = body.wrapped if hasattr(body, "wrapped") else body
        fuse = BRepAlgoAPI_Fuse(shape, rib_box.wrapped)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("rib fuse 실패")
        new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={"rib_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(new_shape), history=history)
