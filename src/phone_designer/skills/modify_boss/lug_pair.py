"""lug_pair — macro. 양 측면 스트랩 마운트 (lug pad + pin hole) 한 쌍.

워치 외피의 12시/6시 (또는 사용자 지정 X 방향) 의 lug 한 쌍.

expansion: [union(lug_top), union(lug_bottom), hole(pin_top), hole(pin_bottom)]
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="lug_pair",
    category="modify/boss",
    level="macro",
    summary="Pair of strap-mount lugs on opposite sides of the housing — each is a "
            "box-shaped pad with a through pin hole. Default axis = Y (12-o'clock / "
            "6-o'clock of a watch face).",
    selector_kinds=[],
    history_rules={"lugs": HistoryRule.GENERATED_NEW},
    produces_features=["lug_pad_pair", "pin_hole_pair"],
    preserves=["outer_envelope_outside_lugs"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.lug_too_close_to_housing_edge"],
    cost_hint=0.25,
    expansion=["union", "union", "hole", "hole"],
    post_conditions=[PostCondition(kind="volume_increased")],
)
class LugPair(SkillBase):
    class Args(BaseModel):
        axis: Literal["+Y/-Y", "+X/-X"] = Field(
            default="+Y/-Y",
            description="lug 가 위치하는 축 — '+Y/-Y' = 12/6시 방향, '+X/-X' = 3/9시",
        )
        offset_mm: float = Field(default=21.0, gt=0,
                                  description="housing 중심에서 lug 중심까지 거리")
        z_position_mm: float = Field(default=5.5, ge=0,
                                      description="lug 의 Z 위치 (housing 의 중심 z)")
        lug_length_mm: float = Field(default=8.0, gt=0, le=20,
                                      description="lug 의 다른 축 방향 폭")
        lug_thickness_mm: float = Field(default=3.0, gt=0, le=10,
                                         description="lug 의 axis 방향 두께")
        lug_height_mm: float = Field(default=3.0, gt=0, le=10,
                                      description="lug 의 Z 두께")
        pin_diameter_mm: float = Field(default=1.5, gt=0, le=5,
                                        description="스트랩 핀 hole 직경")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.compose.union import Union
        from phone_designer.skills.modify_pocket.hole import Hole

        is_y = args.axis == "+Y/-Y"
        # axis 별 lug box 의 length/width 매핑
        if is_y:
            # +Y/-Y 방향 — length 가 X, thickness 가 Y
            box_l, box_w = args.lug_length_mm, args.lug_thickness_mm
            lug_pos_sign = "Y"
            pin_direction_a, pin_direction_b = "-Y", "+Y"
        else:
            box_l, box_w = args.lug_thickness_mm, args.lug_length_mm
            lug_pos_sign = "X"
            pin_direction_a, pin_direction_b = "-X", "+X"

        current = body

        # 두 lug pad 추가
        for sign in (+1, -1):
            offset = args.offset_mm * sign
            if is_y:
                lug_position = (0.0, offset, args.z_position_mm)
            else:
                lug_position = (offset, 0.0, args.z_position_mm)
            current = Union().apply(current, {
                "other": {
                    "kind": "primitive_box",
                    "length_mm": box_l,
                    "width_mm": box_w,
                    "height_mm": args.lug_height_mm,
                    "position": lug_position,
                },
            }).body

        # pin hole 두 개 — axis 방향으로 through
        # 첫 lug (sign=+1) 의 outer side 에서 시작 → -axis 방향으로 through
        for sign in (+1, -1):
            offset = args.offset_mm * sign
            if is_y:
                # +Y 쪽 lug 의 outer (외부) face = (0, offset + box_w/2, z)
                # 핀 hole 은 외부에서 안쪽 (-Y) 방향
                hole_origin = (
                    0.0,
                    offset + (box_w / 2 + 0.1) * sign,
                    args.z_position_mm,
                )
                direction = "-Y" if sign > 0 else "+Y"
            else:
                hole_origin = (
                    offset + (box_w / 2 + 0.1) * sign,
                    0.0,
                    args.z_position_mm,
                )
                direction = "-X" if sign > 0 else "+X"
            current = Hole().apply(current, {
                "position": list(hole_origin),
                "diameter_mm": args.pin_diameter_mm,
                "depth_mm": args.lug_thickness_mm + 1.0,    # lug pad 만 통과 (housing 안 통과)
                "direction": direction,
            }).body

        history = EntityHistoryMap(
            rules={"lugs": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=current, history=history)
