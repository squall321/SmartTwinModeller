"""chamfer_edges_by_predicate — atomic.

selector 로 지정된 edge 들에 동일 width chamfer 적용.
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
    name="chamfer_edges_by_predicate",
    category="modify/chamfer",
    level="atomic",
    summary="Apply a constant-width 45° chamfer to edges matching a selector predicate.",
    selector_kinds=["edges"],
    history_rules={
        "target_edges":   HistoryRule.CONSUMED,
        "result_face":    HistoryRule.GENERATED_NEW,
        "adjacent_faces": HistoryRule.MODIFIED_INHERIT,
    },
    preconditions=[
        "pc.predicate_matches_at_least_one",
        "pc.width_less_than_half_shortest_edge",
    ],
    produces_features=["chamfer_face"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
        "sheet_metal_stamp": {"min_wall_mm": 0.5},
    },
    failure_modes=["fm.self_intersection_if_width_too_large", "fm.no_match"],
    cost_hint=0.12,
    post_conditions=[PostCondition(kind="face_count_changed")],
)
class ChamferEdgesByPredicate(SkillBase):
    class Args(BaseModel):
        selector: SelectorRef
        width_mm: float = Field(ge=0.05, le=20.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        from phone_designer.skills._resolvers import (
            resolve_edges, _edge_endpoints, _edge_length, _all_faces,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        edges = resolve_edges(shape, args.selector)
        if not edges:
            raise RuntimeError(f"selector matched 0 edges: {args.selector.model_dump()}")

        # BRepFilletAPI_MakeChamfer 는 (width, edge, adjacent_face) 요구.
        # 각 edge 의 인접 face 를 IsSame 비교로 검색 (MapShapesAndAncestors 의 hash 이슈 회피).
        all_faces = _all_faces(shape)

        def _find_adjacent_face(target_edge):
            for face in all_faces:
                fit = TopExp_Explorer(face, TopAbs_EDGE)
                while fit.More():
                    fe = TopoDS.Edge_s(fit.Current())
                    if fe.IsSame(target_edge):
                        return face
                    fit.Next()
            return None

        maker = BRepFilletAPI_MakeChamfer(shape)
        added = 0
        for e in edges:
            f = _find_adjacent_face(e)
            if f is None:
                continue
            # OCP 7.8: Add(Dis1, Dis2, Edge, Face) — 대칭 chamfer 는 같은 거리 2번.
            maker.Add(args.width_mm, args.width_mm, e, f)
            added += 1

        if added == 0:
            raise RuntimeError(
                f"chamfer 적용 가능한 edge-face pair 가 0 — "
                f"edges matched={len(edges)} 이지만 인접 face 매핑 실패"
            )

        maker.Build()
        if not maker.IsDone():
            raise RuntimeError(
                f"BRepFilletAPI_MakeChamfer failed (width={args.width_mm}, edges={len(edges)})"
            )
        new_shape = maker.Shape()

        history = EntityHistoryMap(
            rules={
                "target_edges":   HistoryRule.CONSUMED,
                "result_face":    HistoryRule.GENERATED_NEW,
                "adjacent_faces": HistoryRule.MODIFIED_INHERIT,
            },
        )

        # freeze 계산
        refs = []
        for e in edges:
            p1, p2 = _edge_endpoints(e)
            center = (
                round((p1.X() + p2.X()) / 2, 3),
                round((p1.Y() + p2.Y()) / 2, 3),
                round((p1.Z() + p2.Z()) / 2, 3),
            )
            length = _edge_length(e)
            refs.append(EntityRef(
                tag=None, kind="edge",
                bbox_center=center, measure=round(length, 3),
            ))
        freeze = SelectorFreeze.from_entities(refs)

        return SkillResult(
            body=Part(new_shape),
            history=history,
            selector_freeze=freeze,
        )
