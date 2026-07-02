"""fillet_edges_by_predicate — atomic.

Selector 로 지정된 edge 들에 동일 radius fillet 적용.
PoC 의 fillet_with_history 를 framework 안에 internalize.

Class-A knobs (2026-07): ``continuity='g2'`` builds a CURVATURE-continuous blend
(maker.SetContinuity(GeomAbs_G2, tol)) and ``fillet_shape`` picks the cross-section
law (rational | quasi_angular | polynomial via maker.SetFilletShape) — the standard
cosmetic-fillet controls every general CAD kernel exposes. Defaults preserve the
historical behaviour exactly (OCCT's own G1 rational), so existing plans are
byte-stable.
"""
from __future__ import annotations

import math
from typing import Any, Literal

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
    name="fillet_edges_by_predicate",
    category="modify/fillet",
    level="atomic",
    summary="Apply a constant-radius fillet to edges matching a selector predicate.",
    selector_kinds=["edges"],
    history_rules={
        "target_edges":   HistoryRule.CONSUMED,
        "result_face":    HistoryRule.GENERATED_NEW,
        "adjacent_faces": HistoryRule.MODIFIED_INHERIT,
    },
    preconditions=[
        "pc.predicate_matches_at_least_one",
        "pc.radius_less_than_half_shortest_edge",
    ],
    produces_features=["fillet_face"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_fillet_r_mm": "args.radius_mm * 0.5"},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.self_intersection_if_radius_too_large", "fm.no_match"],
    cost_hint=0.15,
    # Fillet changes topology (new face per edge) — volume change is small,
    # but face/edge count must change.
    post_conditions=[PostCondition(kind="face_count_changed")],
)
class FilletEdgesByPredicate(SkillBase):
    class Args(BaseModel):
        selector: SelectorRef
        radius_mm: float = Field(ge=0.05, le=50.0)
        continuity: Literal["g1", "g2"] = Field(
            default="g1",
            description="Blend continuity to the adjacent faces: g1 (tangent, the "
                        "OCCT default — historical behaviour) or g2 (curvature-"
                        "continuous, for Class-A / cosmetic surfaces).")
        fillet_shape: Literal["rational", "quasi_angular", "polynomial"] = Field(
            default="rational",
            description="Cross-section law: rational (exact circular, default) | "
                        "quasi_angular (near-circular, better for G2) | polynomial "
                        "(fully polynomial, smoothest for downstream NURBS work).")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
        from OCP.ChFi3d import ChFi3d_Polynomial, ChFi3d_QuasiAngular
        from OCP.GeomAbs import GeomAbs_G2

        from phone_designer.skills._resolvers import resolve_edges
        shape = body.wrapped if hasattr(body, "wrapped") else body
        edges = resolve_edges(shape, args.selector)
        if not edges:
            raise RuntimeError(f"selector matched 0 edges: {args.selector.model_dump()}")

        maker = BRepFilletAPI_MakeFillet(shape)
        if args.fillet_shape != "rational":
            maker.SetFilletShape(
                ChFi3d_QuasiAngular if args.fillet_shape == "quasi_angular"
                else ChFi3d_Polynomial)
        if args.continuity == "g2":
            maker.SetContinuity(GeomAbs_G2, math.radians(1.0))
        for e in edges:
            maker.Add(args.radius_mm, e)
        maker.Build()
        if not maker.IsDone():
            raise RuntimeError(
                f"BRepFilletAPI_MakeFillet failed (radius={args.radius_mm}, "
                f"edges={len(edges)})"
            )
        new_shape = maker.Shape()

        # history 추출 (PoC 패턴)
        history = EntityHistoryMap(
            rules={
                "target_edges":   HistoryRule.CONSUMED,
                "result_face":    HistoryRule.GENERATED_NEW,
                "adjacent_faces": HistoryRule.MODIFIED_INHERIT,
            },
        )

        # SelectorFreeze 계산 — matched edges 의 위상 시그니처
        freeze = _freeze_edges(edges)

        return SkillResult(
            body=Part(new_shape),
            history=history,
            selector_freeze=freeze,
        )


def _freeze_edges(edges: list) -> SelectorFreeze:
    """[[plan-determinism]] 의 freeze 계산."""
    from OCP.TopExp import TopExp
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint

    refs = []
    for e in edges:
        v1 = TopExp.FirstVertex_s(e)
        v2 = TopExp.LastVertex_s(e)
        p1 = BRep_Tool.Pnt_s(v1)
        p2 = BRep_Tool.Pnt_s(v2)
        center = (
            round((p1.X() + p2.X()) / 2, 3),
            round((p1.Y() + p2.Y()) / 2, 3),
            round((p1.Z() + p2.Z()) / 2, 3),
        )
        length = GCPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(e))
        refs.append(EntityRef(
            tag=None, kind="edge",
            bbox_center=center, measure=round(length, 3),
        ))
    return SelectorFreeze.from_entities(refs)
