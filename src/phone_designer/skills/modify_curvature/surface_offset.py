"""surface_offset — atomic. body 전체 face 의 offset (외각 두께 조정).

Phase 4 v1: 균일 offset (uniform thickness change). 모든 face 가 +/- 동일 방향.
양수 = 부피 증가 (외각 확장), 음수 = 감소 (내부로).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="surface_offset",
    category="modify/curvature",
    level="atomic",
    summary="Uniform surface offset of the entire body (thickness change). "
            "Positive = expand outward, negative = shrink inward. "
            "Note: large offsets can fail due to self-intersection.",
    selector_kinds=[],
    history_rules={
        "all_faces":      HistoryRule.MODIFIED_INHERIT,
        "offset_volume":  HistoryRule.GENERATED_NEW,
    },
    produces_features=["offset_body"],
    preserves=["topology_count"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.self_intersection_after_offset", "fm.offset_too_large"],
    cost_hint=0.25,
    # Offset legitimately is a no-op when offset_mm ≈ 0 — skill has an
    # explicit early return. allow_no_change preserves that path.
    post_conditions=[PostCondition(kind="volume_changed", allow_no_change=True)],
)
class SurfaceOffset(SkillBase):
    class Args(BaseModel):
        offset_mm: float = Field(
            ge=-10, le=10,
            description="외각 offset (mm). 양수 = 확장, 음수 = 축소.",
        )
        tolerance_mm: float = Field(default=0.01, gt=0, le=1,
                                     description="OffsetShape 의 tolerance")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
        from OCP.BRepOffset import BRepOffset_Skin
        from OCP.GeomAbs import GeomAbs_Intersection

        if abs(args.offset_mm) < 1e-6:
            # no-op
            history = EntityHistoryMap(rules={"all_faces": HistoryRule.MODIFIED_INHERIT})
            return SkillResult(body=body, history=history)

        shape = body.wrapped if hasattr(body, "wrapped") else body

        maker = BRepOffsetAPI_MakeOffsetShape()
        # OCP signature: PerformBySimple(shape, offset, tol, mode, intersection, selfInter, ...)
        # 가장 간단한 default 호출:
        try:
            maker.PerformByJoin(
                shape,
                args.offset_mm,
                args.tolerance_mm,
                BRepOffset_Skin,
                False,    # selfInter
                False,    # selfInter1
                GeomAbs_Intersection,
                False,    # removeIntEdges
            )
        except Exception as exc:
            raise RuntimeError(f"surface_offset PerformByJoin 실패: {exc}")

        if not maker.IsDone():
            raise RuntimeError(
                f"surface_offset: IsDone=False (offset={args.offset_mm})"
            )

        history = EntityHistoryMap(
            rules={
                "all_faces":     HistoryRule.MODIFIED_INHERIT,
                "offset_volume": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(maker.Shape()), history=history)
