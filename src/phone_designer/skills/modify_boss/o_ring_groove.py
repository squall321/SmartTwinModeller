"""o_ring_groove — atomic. 방수 그루브.

Phase 4 v1: 단순 circular groove (ring shape pocket) on a planar face.
  outer cylinder - inner cylinder 의 차이 = 그루브 ring 의 cutting tool.

Phase 4 v2 (P1 backlog):
  - 임의 path (사각/타원) 의 sweep groove
  - profile: rect / semicircle / dovetail
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="o_ring_groove",
    category="modify/boss",
    level="atomic",
    summary="Circular o-ring groove (ring-shape pocket) on a planar face. "
            "Phase 4 v1 supports circular groove only — arbitrary path sweep is v2.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":  HistoryRule.MODIFIED_INHERIT,
        "groove_walls": HistoryRule.GENERATED_NEW,
        "groove_floor": HistoryRule.GENERATED_NEW,
    },
    produces_features=["o_ring_groove"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3, "min_fillet_r_mm": 0.2},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_recommended": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.groove_width_zero", "fm.groove_outside_face"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class ORingGroove(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        outer_diameter_mm: float = Field(gt=0, le=200)
        inner_diameter_mm: float = Field(gt=0, le=200)
        depth_mm: float = Field(gt=0, le=10)
        center_x_mm: float = 0.0
        center_y_mm: float = 0.0

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Align, Cylinder, Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        from phone_designer.skills._resolvers import (
            resolve_faces, _face_center, _face_normal_at_center,
        )

        if args.inner_diameter_mm >= args.outer_diameter_mm:
            raise ValueError(
                f"inner_d ({args.inner_diameter_mm}) >= outer_d "
                f"({args.outer_diameter_mm}) — 그루브 폭 = 0 이상이어야"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"o_ring_groove: face_selector 매칭 0 — {args.face_selector.model_dump()}"
            )
        target = faces[0]

        # face center + normal — Phase 4 v1 의 face_local 변환 단순화 (planar top/bottom 만)
        center = _face_center(target)
        normal = _face_normal_at_center(target)

        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "o_ring_groove v1: planar 의 +Z/-Z face 만 지원 (top/bottom)"
            )

        # ring tool = outer_cyl - inner_cyl
        outer = Cylinder(
            radius=args.outer_diameter_mm / 2, height=args.depth_mm + 1.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        inner = Cylinder(
            radius=args.inner_diameter_mm / 2, height=args.depth_mm + 2.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )

        # face 가 +Z 면 outer/inner 의 base 가 face center - depth (안쪽으로 자라남)
        # face 가 -Z 면 base 가 face center (바깥 → 안)
        if normal[2] > 0:
            base_z = center[2] - args.depth_mm
        else:
            base_z = center[2]

        outer.position = (
            center[0] + args.center_x_mm,
            center[1] + args.center_y_mm,
            base_z,
        )
        inner.position = (
            center[0] + args.center_x_mm,
            center[1] + args.center_y_mm,
            base_z - 0.5,    # 약간 더 깊게 — clean cut
        )

        # ring tool
        ring_cut = BRepAlgoAPI_Cut(outer.wrapped, inner.wrapped)
        ring_cut.Build()
        if not ring_cut.IsDone():
            raise RuntimeError("o_ring_groove: ring tool 생성 실패")
        ring_tool = ring_cut.Shape()

        # body 에서 ring 만큼 cut
        cut = BRepAlgoAPI_Cut(shape, ring_tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("o_ring_groove: body cut 실패")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":  HistoryRule.MODIFIED_INHERIT,
                "groove_walls": HistoryRule.GENERATED_NEW,
                "groove_floor": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
