"""boss_with_hole — macro. 스크류 보스 = 원기둥 plateau + 중심 hole.

expansion: [union (cylindrical plateau), hole (screw thread or clearance hole)]

내부 부품 스크류 고정의 표준 패턴.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="boss_with_hole",
    category="modify/boss",
    level="macro",
    summary="Screw boss = cylindrical plateau + concentric hole. "
            "Standard pattern for internal part mounting. "
            "Diameter ratio (boss_d : hole_d) typically 2:1 for injection-molded plastic.",
    selector_kinds=[],
    history_rules={"boss_solid": HistoryRule.GENERATED_NEW},
    produces_features=["boss_with_hole", "screw_seat"],
    preserves=["outer_envelope_outside_boss"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"boss_d_recommended": "hole_d * 2.0"}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "cnc_3axis":         {"min_wall_mm": 0.5},
    },
    failure_modes=["fm.hole_d_geq_boss_d", "fm.boss_too_short_for_hole"],
    cost_hint=0.2,
    expansion=["union", "hole"],
    # Boss + hole — net effect is still adding material (boss vol > hole vol
    # because hole_d < boss_d). Per-step verification of the sub-skills
    # already enforces the directional checks; the macro just confirms
    # net volume increase.
    post_conditions=[PostCondition(kind="volume_increased")],
)
class BossWithHole(SkillBase):
    class Args(BaseModel):
        position: tuple[float, float, float] = Field(
            description="boss 의 base 위치 (housing 내부 face 위)",
        )
        direction: Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"] = Field(
            default="+Z",
            description="boss 가 자라나는 방향 (housing 의 내부 face 의 inward normal)",
        )
        boss_diameter_mm: float = Field(gt=0, le=30,
                                         description="외부 원기둥 직경")
        boss_height_mm: float = Field(gt=0, le=30,
                                       description="원기둥 height")
        hole_diameter_mm: float = Field(gt=0, le=20,
                                         description="중심 hole 직경 (스크류 clearance 또는 self-tap)")
        hole_depth_mm: float | None = Field(default=None, gt=0,
                                              description="None = boss_height + 1mm")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.compose.union import Union
        from phone_designer.skills.modify_pocket.hole import Hole

        if args.hole_diameter_mm >= args.boss_diameter_mm:
            raise ValueError(
                f"hole_d ({args.hole_diameter_mm}) >= boss_d "
                f"({args.boss_diameter_mm})"
            )

        depth = args.hole_depth_mm if args.hole_depth_mm is not None else (
            args.boss_height_mm + 1.0
        )

        current = body

        # 1. boss = primitive_cylinder, axis = direction, base = position
        current = Union().apply(current, {
            "other": {
                "kind": "primitive_cylinder",
                "diameter_mm": args.boss_diameter_mm,
                "height_mm": args.boss_height_mm,
                "position": list(args.position),
                "axis": args.direction,
            },
        }).body

        # 2. hole — boss top 에서 안쪽으로 (direction 의 반대)
        d = _direction_vector(args.direction)
        boss_top = (
            args.position[0] + d[0] * args.boss_height_mm,
            args.position[1] + d[1] * args.boss_height_mm,
            args.position[2] + d[2] * args.boss_height_mm,
        )
        inward = _opposite_direction(args.direction)
        current = Hole().apply(current, {
            "position": list(boss_top),
            "diameter_mm": args.hole_diameter_mm,
            "depth_mm": depth,
            "direction": inward,
        }).body

        history = EntityHistoryMap(
            rules={"boss_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=current, history=history)


def _direction_vector(d: str) -> tuple[float, float, float]:
    return {"+X": (1, 0, 0), "-X": (-1, 0, 0),
            "+Y": (0, 1, 0), "-Y": (0, -1, 0),
            "+Z": (0, 0, 1), "-Z": (0, 0, -1)}[d]


def _opposite_direction(d: str) -> str:
    return {"+X": "-X", "-X": "+X", "+Y": "-Y", "-Y": "+Y",
            "+Z": "-Z", "-Z": "+Z"}[d]
