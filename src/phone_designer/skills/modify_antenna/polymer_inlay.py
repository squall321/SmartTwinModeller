"""polymer_inlay — atomic. 안테나 슬릿에 폴리머 충진 (union).

antenna_slit 이 만든 슬릿 자리에 폴리머 인레이를 채운다. 형상은 antenna_slit 과 동일
geometry. body 와 함께 단일 part 로 묶이지만 material attribute 로 다른 재료.

Phase 4 v1: linear path (antenna_slit 과 같은 인자). body 의 동일 영역에 union.
Phase 4 v2: material attribute 분리 (`body._pd_materials` 같은 metadata 로 추적).
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
    name="polymer_inlay",
    category="modify/antenna",
    level="atomic",
    summary="Fill an antenna slit with polymer inlay (union). Geometry identical to "
            "antenna_slit. v1 returns the unified body; v2 will track material attribute.",
    selector_kinds=[],
    history_rules={
        "body_faces":  HistoryRule.MODIFIED_INHERIT,
        "inlay_faces": HistoryRule.GENERATED_NEW,
    },
    produces_features=["polymer_inlay"],
    preserves=["outer_envelope"],
    manufacturing={
        "two_shot_injection": {"min_wall_mm": 0.5,
                                "extras": {"primary_material": "metal_unibody",
                                            "secondary_material": "polymer"}},
        "insert_molding":     {"min_wall_mm": 0.5},
    },
    failure_modes=["fm.inlay_geometry_mismatch_slit"],
    cost_hint=0.15,
    # Inlay always unions a tool box. If the slit was already cut the union
    # refills it (+); if the tool sits entirely inside the body the result
    # is unchanged — allow that as a soft pass.
    post_conditions=[PostCondition(kind="volume_increased", allow_no_change=True)],
)
class PolymerInlay(SkillBase):
    class Args(BaseModel):
        start: tuple[float, float, float]
        end: tuple[float, float, float]
        width_mm: float = Field(default=0.8, gt=0, le=3)
        depth_mm: float = Field(default=10.0, gt=0, le=20)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Align, Axis, Box, Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

        dx = args.end[0] - args.start[0]
        dy = args.end[1] - args.start[1]
        L = (dx ** 2 + dy ** 2) ** 0.5
        if L < 1e-3:
            raise ValueError("polymer_inlay: zero length")

        tool = Box(L, args.width_mm, args.depth_mm,
                   align=(Align.MIN, Align.CENTER, Align.CENTER))
        angle = math.degrees(math.atan2(dy, dx))
        tool = tool.rotate(Axis.Z, angle)
        tool.position = args.start

        shape = body.wrapped if hasattr(body, "wrapped") else body
        fuse = BRepAlgoAPI_Fuse(shape, tool.wrapped)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("polymer_inlay fuse 실패")

        history = EntityHistoryMap(
            rules={
                "body_faces":  HistoryRule.MODIFIED_INHERIT,
                "inlay_faces": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(fuse.Shape()), history=history)
