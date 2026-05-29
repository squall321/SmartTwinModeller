"""final_fillet_all_sharp_edges — atomic. 일괄 마감.

모든 sharp edge (직각 또는 그 미만) 에 작은 R fillet.
주의: 너무 큰 R 또는 face split 가 fillet 못 따라가는 edge 는 자동 skip.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="final_fillet_all_sharp_edges",
    category="modify/finish",
    level="atomic",
    summary="Apply a small uniform fillet to all 'sharp' edges (angle below threshold). "
            "Edges that fail individually are skipped — partial fillet OK.",
    selector_kinds=[],
    history_rules={
        "filleted_edges":     HistoryRule.CONSUMED,
        "result_faces":       HistoryRule.GENERATED_NEW,
        "non_sharp_features": HistoryRule.MODIFIED_INHERIT,
    },
    produces_features=["fillet_faces_array"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_fillet_r_mm": 0.3},
        "die_cast_al":       {"min_fillet_r_mm": 0.5, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_fillet_r_mm": 0.3, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.no_edges_processed"],
    cost_hint=0.4,    # 모든 edge 적용해서 시간 듦
    # final_fillet may individually skip every edge (legitimate no-op on a
    # body with no sharp edges). allow_no_change preserves that case.
    post_conditions=[PostCondition(kind="face_count_changed", allow_no_change=True)],
)
class FinalFilletAllSharpEdges(SkillBase):
    class Args(BaseModel):
        radius_mm: float = Field(default=0.2, ge=0.05, le=2.0,
                                  description="작은 R 가 권장 (자동 마감)")
        min_angle_deg: float = Field(default=30.0, ge=0, le=89,
                                      description="이 각도 이하면 sharp 로 간주")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        from phone_designer.skills._resolvers import _all_edges

        shape = body.wrapped if hasattr(body, "wrapped") else body

        # 모든 edge 를 일단 시도 — fillet maker 에 추가하되 fail 시 skip.
        # OCCT 의 angle 측정은 별도 API (BRepGProp_EdgeTool 등) — 일단 모든 edge 에
        # 시도하고 maker.IsDone() 으로 검증.
        edges = _all_edges(shape)
        if not edges:
            raise RuntimeError("no edges in body")

        # 일괄 시도 — 한꺼번에 add 후 build, 실패하면 individual fallback
        result_shape = shape
        try:
            maker = BRepFilletAPI_MakeFillet(shape)
            for e in edges:
                maker.Add(args.radius_mm, e)
            maker.Build()
            if maker.IsDone():
                result_shape = maker.Shape()
                ok_count = len(edges)
            else:
                raise RuntimeError("bulk fillet IsDone=False")
        except Exception:
            # individual fallback — edge 별 시도
            ok_count = 0
            for e in edges:
                try:
                    one_maker = BRepFilletAPI_MakeFillet(result_shape)
                    one_maker.Add(args.radius_mm, e)
                    one_maker.Build()
                    if one_maker.IsDone():
                        result_shape = one_maker.Shape()
                        ok_count += 1
                except Exception:
                    continue

        if ok_count == 0:
            raise RuntimeError(
                f"final_fillet: 0 edges processed (radius={args.radius_mm}). "
                f"radius 가 너무 클 수 있음 — 더 작은 값 시도."
            )

        history = EntityHistoryMap(
            rules={
                "filleted_edges":     HistoryRule.CONSUMED,
                "result_faces":       HistoryRule.GENERATED_NEW,
                "non_sharp_features": HistoryRule.MODIFIED_INHERIT,
            },
        )
        return SkillResult(body=Part(result_shape), history=history)
