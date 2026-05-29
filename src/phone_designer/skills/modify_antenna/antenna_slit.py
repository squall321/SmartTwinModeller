"""antenna_slit — atomic. 금속 unibody 의 안테나 절연 슬릿.

알루미늄 unibody 폰의 가는 슬릿 — RF 단절을 위해. linear path 의 폭은 보통 0.5-1mm,
깊이는 body 두께를 거의 관통.

Phase 4 v1: linear (start-end) slit. polymer 충진은 별도 polymer_inlay.
Phase 4 v2: 곡선 path (sweep), 다중 segment.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="antenna_slit",
    category="modify/antenna",
    level="atomic",
    summary="Linear thin slit for antenna isolation on metallic unibody. "
            "Cuts through the body along start→end with a narrow rectangular cross-section. "
            "v1: linear path only. v2: curve sweep + multi-segment.",
    selector_kinds=[],
    history_rules={
        "body_faces":   HistoryRule.MODIFIED_INHERIT,
        "slit_walls":   HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    produces_features=["antenna_slit"],
    preserves=["outer_envelope_outside_slit"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3,
                              "extras": {"slit_width_recommended_mm": 0.8}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_recommended": True}},
        # injection_mold 는 안테나 슬릿 적용 안 함 (plastic body 이미 절연)
    },
    failure_modes=["fm.slit_outside_body", "fm.slit_zero_length"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class AntennaSlit(SkillBase):
    class Args(BaseModel):
        start: tuple[float, float, float]
        end: tuple[float, float, float]
        width_mm: float = Field(default=0.8, gt=0, le=3,
                                 description="슬릿 폭 (path 의 수직)")
        depth_mm: float = Field(default=10.0, gt=0, le=20,
                                 description="슬릿 깊이 (body 의 Z 두께를 넘으면 through)")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Align, Axis, Box, Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        dx = args.end[0] - args.start[0]
        dy = args.end[1] - args.start[1]
        L = (dx ** 2 + dy ** 2) ** 0.5
        if L < 1e-3:
            raise ValueError("antenna_slit: start == end (zero length)")

        # path 가 XY 평면. slit box = L × width × depth, +Z 로 자라남
        tool = Box(L, args.width_mm, args.depth_mm,
                   align=(Align.MIN, Align.CENTER, Align.CENTER))
        angle = math.degrees(math.atan2(dy, dx))
        tool = tool.rotate(Axis.Z, angle)
        # 시작점에 위치, Z = start.z
        tool.position = args.start

        shape = body.wrapped if hasattr(body, "wrapped") else body
        cut = BRepAlgoAPI_Cut(shape, tool.wrapped)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("antenna_slit cut 실패")

        history = EntityHistoryMap(
            rules={
                "body_faces":      HistoryRule.MODIFIED_INHERIT,
                "slit_walls":      HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(cut.Shape()), history=history)
