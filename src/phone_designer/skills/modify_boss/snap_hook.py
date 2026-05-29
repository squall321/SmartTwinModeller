"""snap_hook — atomic. 사출 부품의 스냅 후크.

스냅 후크 단순화 모델: cantilever beam + hook lip.
Phase 4 v1: 단순 box-cantilever + 옆에 trapezoidal lip.
Phase 4 v2: face-aligned hook 자동 정렬.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="snap_hook",
    category="modify/boss",
    level="atomic",
    summary="Injection-molded snap hook — cantilever beam + protruding lip at tip. "
            "v1: axis-aligned box-cantilever + box-lip. v2 will support face-aligned attach.",
    selector_kinds=[],
    history_rules={"snap_hook_solid": HistoryRule.GENERATED_NEW},
    produces_features=["snap_hook"],
    preserves=["outer_envelope_outside_hook"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"max_lip_overhang_mm": 1.0}},
    },
    failure_modes=["fm.lip_too_large_for_beam"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class SnapHook(SkillBase):
    class Args(BaseModel):
        base_position: tuple[float, float, float] = Field(
            description="cantilever beam 의 base (housing 부착점)",
        )
        cantilever_length_mm: float = Field(default=8.0, gt=0, le=30)
        cantilever_width_mm: float = Field(default=3.0, gt=0, le=15)
        cantilever_thickness_mm: float = Field(default=1.5, gt=0, le=5)
        lip_overhang_mm: float = Field(default=0.8, gt=0, le=3,
                                        description="hook lip 의 옆으로 돌출 (snap engage 깊이)")
        lip_height_mm: float = Field(default=1.5, gt=0, le=5)
        cantilever_axis: Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"] = Field(
            default="+Z",
            description="beam 이 자라나는 방향 (base 부터 tip 방향)",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Align, Axis, Box, Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

        # v1: cantilever_axis = +Z 가정 시 beam 이 Z 로 자라남, lip 이 X 또는 Y 로 돌출.
        # 다른 축은 회전 + 평행이동.

        # 1. cantilever beam — base 부터 axis 방향 length
        beam = Box(
            length=args.cantilever_thickness_mm,
            width=args.cantilever_width_mm,
            height=args.cantilever_length_mm,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )

        # 2. lip — beam 의 tip 에 옆으로 돌출 (X 방향 가정)
        lip = Box(
            length=args.cantilever_thickness_mm + args.lip_overhang_mm,
            width=args.cantilever_width_mm,
            height=args.lip_height_mm,
            align=(Align.MIN, Align.CENTER, Align.MIN),
        )
        # lip 의 base = beam 의 top (z = cantilever_length)
        # X offset = -thickness/2 (beam 의 -X 면 정렬)
        lip.position = (
            -args.cantilever_thickness_mm / 2,
            0,
            args.cantilever_length_mm,
        )

        # beam + lip → snap hook
        f1 = BRepAlgoAPI_Fuse(beam.wrapped, lip.wrapped)
        f1.Build()
        if not f1.IsDone():
            raise RuntimeError("snap_hook: beam + lip fuse 실패")
        hook_shape = f1.Shape()

        # axis 변환
        from build123d import Compound
        hook = Compound(hook_shape)
        if args.cantilever_axis in ("+X", "-X"):
            hook = hook.rotate(Axis.Y, 90 if args.cantilever_axis == "+X" else -90)
        elif args.cantilever_axis in ("+Y", "-Y"):
            hook = hook.rotate(Axis.X, -90 if args.cantilever_axis == "+Y" else 90)
        elif args.cantilever_axis == "-Z":
            hook = hook.rotate(Axis.X, 180)

        hook.position = args.base_position

        # body 에 union
        shape = body.wrapped if hasattr(body, "wrapped") else body
        f2 = BRepAlgoAPI_Fuse(shape, hook.wrapped)
        f2.Build()
        if not f2.IsDone():
            raise RuntimeError("snap_hook: body union 실패")
        new_shape = f2.Shape()

        history = EntityHistoryMap(
            rules={"snap_hook_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(new_shape), history=history)
