"""variable_radius_fillet — atomic. edge 별 다른 R fillet.

selector 매칭 N edges 에 각각 radii[i] 적용. radii list 가 edges 수보다 적으면 cycle.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import (
    EntityHistoryMap,
    EntityRef,
    HistoryRule,
    SelectorFreeze,
)
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="variable_radius_fillet",
    category="modify/fillet",
    level="atomic",
    summary="Apply per-edge different fillet radii. Edges from selector × radii list. "
            "If radii has fewer entries, it cycles. Edges sorted by bbox-center "
            "lexicographic for stable assignment.",
    selector_kinds=["edges"],
    history_rules={
        "target_edges":   HistoryRule.CONSUMED,
        "result_faces":   HistoryRule.GENERATED_NEW,
        "adjacent_faces": HistoryRule.MODIFIED_INHERIT,
    },
    produces_features=["variable_fillet_faces"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_5axis":         {"min_fillet_r_mm": 0.3},
        "cnc_3axis":         {"min_fillet_r_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
    },
    failure_modes=["fm.no_match", "fm.radius_too_large_for_some_edge"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="face_count_changed")],
)
class VariableRadiusFillet(SkillBase):
    class Args(BaseModel):
        selector: SelectorRef
        radii_mm: list[float] = Field(min_length=1,
                                       description="R list. edges 수보다 적으면 cycle.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        from phone_designer.skills._resolvers import (
            resolve_edges, _edge_endpoints, _edge_length,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        edges = resolve_edges(shape, args.selector)
        if not edges:
            raise RuntimeError(f"selector matched 0 edges: {args.selector.model_dump()}")

        # 안정 할당: edges 를 bbox-center lexicographic 으로 정렬
        def _sort_key(e):
            p1, p2 = _edge_endpoints(e)
            return (
                round((p1.X() + p2.X()) / 2, 3),
                round((p1.Y() + p2.Y()) / 2, 3),
                round((p1.Z() + p2.Z()) / 2, 3),
            )
        sorted_edges = sorted(edges, key=_sort_key)

        maker = BRepFilletAPI_MakeFillet(shape)
        for i, e in enumerate(sorted_edges):
            r = args.radii_mm[i % len(args.radii_mm)]
            maker.Add(r, e)
        maker.Build()
        if not maker.IsDone():
            raise RuntimeError(
                f"variable_radius_fillet: BRepFilletAPI_MakeFillet failed "
                f"(edges={len(sorted_edges)}, radii={args.radii_mm})"
            )
        new_shape = maker.Shape()

        # freeze
        refs = []
        for e in sorted_edges:
            p1, p2 = _edge_endpoints(e)
            center = (
                round((p1.X() + p2.X()) / 2, 3),
                round((p1.Y() + p2.Y()) / 2, 3),
                round((p1.Z() + p2.Z()) / 2, 3),
            )
            refs.append(EntityRef(
                tag=None, kind="edge",
                bbox_center=center, measure=round(_edge_length(e), 3),
            ))

        history = EntityHistoryMap(
            rules={
                "target_edges":   HistoryRule.CONSUMED,
                "result_faces":   HistoryRule.GENERATED_NEW,
                "adjacent_faces": HistoryRule.MODIFIED_INHERIT,
            },
        )
        return SkillResult(
            body=Part(new_shape),
            history=history,
            selector_freeze=SelectorFreeze.from_entities(refs),
        )
