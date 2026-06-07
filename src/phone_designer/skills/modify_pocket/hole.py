"""hole — atomic. 단일 원형 hole (blind/through).

크라운 샤프트 hole, 마이크 hole, 스피커 hole, mounting hole 등 모든 원형 관통/blind hole.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="hole",
    category="modify/hole",
    level="atomic",
    summary="Drill a circular hole at the given position, axis direction, and depth. "
            "If depth is null, treat as through-hole (deep enough to traverse the body).",
    selector_kinds=[],   # 위치 직접 지정 (selector 안 씀)
    history_rules={
        "result_cylinder_face": HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.diameter_positive",
        "pc.position_inside_or_on_body",
    ],
    produces_features=["cylindrical_hole"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5, "extras": {"max_aspect_ratio": 10.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.hole_exits_body", "fm.hole_too_close_to_edge"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class Hole(SkillBase):
    class Args(BaseModel):
        position: tuple[float, float, float]
        diameter_mm: float = Field(gt=0, le=10000)
        depth_mm: float | None = Field(default=None, gt=0, le=10000,
                                        description="None = through-hole")
        direction: Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"] = "-Z"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part, Cylinder, Axis, Location
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        shape = body.wrapped if hasattr(body, "wrapped") else body

        # 직경 → cylinder 생성 후 위치/방향 적용
        # depth=None 이면 충분히 길게 (200mm 가정, body 통과)
        depth = args.depth_mm if args.depth_mm is not None else 200.0

        # Cylinder default axis = +Z. 다른 방향이면 rotate.
        from build123d import Align
        cyl = Cylinder(
            radius=args.diameter_mm / 2, height=depth,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )

        # direction 에 따라 회전
        if args.direction == "-Z":
            # +Z 면 base z=0, -Z 면 base z=-height. position 이 hole 의 입구.
            cyl.position = (args.position[0], args.position[1], args.position[2] - depth)
            # cylinder 그대로 (위로 자라남, 입구가 position)
        elif args.direction == "+Z":
            cyl.position = args.position
        elif args.direction in ("+X", "-X"):
            cyl = cyl.rotate(Axis.Y, 90)
            if args.direction == "+X":
                cyl.position = args.position
            else:
                cyl.position = (args.position[0] - depth, args.position[1], args.position[2])
        elif args.direction in ("+Y", "-Y"):
            # rotate(Axis.X, +90) maps +Z → -Y, which is wrong for both +Y
            # and -Y intents. Use -90 so the cylinder points +Y, then use
            # `position` to anchor the entry as for the ±X case.
            cyl = cyl.rotate(Axis.X, -90)
            if args.direction == "+Y":
                cyl.position = args.position
            else:
                cyl.position = (args.position[0], args.position[1] - depth, args.position[2])

        # body - cyl
        cut = BRepAlgoAPI_Cut(shape, cyl.wrapped)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError(f"hole 부울 cut 실패 (position={args.position}, d={args.diameter_mm})")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "result_cylinder_face": HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
