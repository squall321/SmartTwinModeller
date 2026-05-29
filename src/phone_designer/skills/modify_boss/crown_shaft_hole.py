"""crown_shaft_hole — macro. 회전 크라운 베어링 + 샤프트 hole.

워치의 측면 크라운 인터페이스 — 외부 crown plinth (옵션) + 내부 베어링 recess +
실제 회전 샤프트 통과 hole.

expansion: [union(plinth?), hole(bearing_recess), hole(shaft)]
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="crown_shaft_hole",
    category="modify/boss",
    level="macro",
    summary="Watch crown interface — optional external plinth (cylindrical knob seat) + "
            "bearing recess (wide shallow hole) + shaft hole (narrow deep). All coaxial "
            "along the given direction (typically +X for right-side crown).",
    selector_kinds=[],
    history_rules={"crown_interface": HistoryRule.GENERATED_NEW},
    produces_features=["crown_plinth", "bearing_recess", "shaft_hole"],
    preserves=["outer_envelope_outside_crown"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.bearing_too_close_to_outer", "fm.shaft_exits_body"],
    cost_hint=0.3,
    expansion=["union", "hole", "hole"],
    # Plinth-optional: can be net add (with plinth) or net remove (without).
    # Always at least cuts a bearing recess + shaft hole — volume must change.
    post_conditions=[PostCondition(kind="volume_changed")],
)
class CrownShaftHole(SkillBase):
    class Args(BaseModel):
        position: tuple[float, float, float] = Field(
            description="크라운 축이 통과하는 외부 표면의 한 점 (housing 측면)",
        )
        direction: Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"] = Field(
            default="+X",
            description="외부 face 의 outward normal (예: +X = 우측 측면). "
                        "내부 hole 은 자동으로 반대 방향으로 적용됨.",
        )
        shaft_d_mm: float = Field(default=2.5, gt=0, le=10,
                                   description="회전 샤프트 hole 직경")
        shaft_depth_mm: float = Field(default=6.0, gt=0, le=30,
                                       description="샤프트 hole 깊이")
        bearing_recess_d_mm: float = Field(default=5.0, gt=0, le=15,
                                            description="베어링 자리 직경")
        bearing_recess_depth_mm: float = Field(default=1.5, gt=0, le=10,
                                                description="베어링 자리 깊이")
        plinth_d_mm: float = Field(default=0.0, ge=0, le=20,
                                    description="외부 crown plinth 직경 (0 = 없음)")
        plinth_height_mm: float = Field(default=0.0, ge=0, le=10,
                                         description="외부 plinth 돌출 (0 = 없음)")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.compose.union import Union
        from phone_designer.skills.modify_pocket.hole import Hole

        if args.bearing_recess_d_mm <= args.shaft_d_mm:
            raise ValueError(
                f"bearing_recess_d ({args.bearing_recess_d_mm}) 가 "
                f"shaft_d ({args.shaft_d_mm}) 보다 커야 함"
            )

        current = body

        # user direction = outward normal (예: +X = 우측 face). hole 의 axis
        # 의미는 "안쪽으로 향하는 방향" 이라 반대.
        outward = args.direction
        inward = _opposite_direction(outward)

        # 1. plinth (옵션) — 외부 plinth 가 있으면 union (outward 방향으로 자라남)
        if args.plinth_height_mm > 0 and args.plinth_d_mm > 0:
            # plinth 는 외부에 돌출. primitive_cylinder 의 axis = outward.
            # 그 base 위치 = position (외부 face 의 점), axis 방향으로 자라남.
            current = Union().apply(current, {
                "other": {
                    "kind": "primitive_cylinder",
                    "diameter_mm": args.plinth_d_mm,
                    "height_mm": args.plinth_height_mm,
                    "position": list(args.position),
                    "axis": outward,
                },
            }).body

        # 2. bearing recess — wider shallow hole (outward 의 표면에서 안쪽으로)
        current = Hole().apply(current, {
            "position": list(args.position),
            "diameter_mm": args.bearing_recess_d_mm,
            "depth_mm": args.bearing_recess_depth_mm,
            "direction": inward,
        }).body

        # 3. shaft hole — 베어링 안에서 더 깊게
        current = Hole().apply(current, {
            "position": list(args.position),
            "diameter_mm": args.shaft_d_mm,
            "depth_mm": args.shaft_depth_mm,
            "direction": inward,
        }).body

        history = EntityHistoryMap(
            rules={"crown_interface": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=current, history=history)


def _opposite_direction(direction: str) -> str:
    return {
        "+X": "-X", "-X": "+X",
        "+Y": "-Y", "-Y": "+Y",
        "+Z": "-Z", "-Z": "+Z",
    }[direction]
