"""chamfer_asymmetric — atomic.

Asymmetric chamfer on selected edges: distance_1 along one face, distance_2
along the OTHER face. The standard symmetric chamfer_edges_by_predicate uses
the same distance on both sides; this skill takes two distances so the
chamfer face is rectangular (elongated) rather than square.

Args:
    edge_selector:  SelectorRef
    distance_1_mm:  distance measured on the FIRST adjacent face
    distance_2_mm:  distance measured on the SECOND adjacent face

Implementation:
    For each matched edge, find its two adjacent faces (via TopExp_Explorer
    scanning each face's edges, identifying which faces share the edge). Use
    the 4-arg OCP BRepFilletAPI_MakeChamfer.Add(d1, d2, edge, face) signature,
    where `face` is the reference face: d1 is measured along `face`, d2 along
    the other adjacent face.
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
    name="chamfer_asymmetric",
    category="modify/curvature",
    level="atomic",
    summary="Apply an asymmetric chamfer to edges matching a selector predicate. "
            "distance_1 is measured along one adjacent face, distance_2 along "
            "the other. The resulting chamfer face is rectangular (elongated) "
            "rather than the square produced by a symmetric chamfer.",
    selector_kinds=["edges"],
    history_rules={
        "target_edges":   HistoryRule.CONSUMED,
        "result_face":    HistoryRule.GENERATED_NEW,
        "adjacent_faces": HistoryRule.MODIFIED_INHERIT,
    },
    preconditions=[
        "pc.predicate_matches_at_least_one",
        "pc.distance_1_less_than_half_shortest_edge",
        "pc.distance_2_less_than_half_shortest_edge",
    ],
    produces_features=["chamfer_face_asymmetric"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
        "sheet_metal_stamp": {"min_wall_mm": 0.5},
    },
    failure_modes=[
        "fm.self_intersection_if_distance_too_large",
        "fm.no_match",
        "fm.no_adjacent_face_found",
    ],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="face_count_changed")],
)
class ChamferAsymmetric(SkillBase):
    class Args(BaseModel):
        edge_selector: SelectorRef
        distance_1_mm: float = Field(gt=0.0, le=20.0,
                                      description="distance along first adjacent face")
        distance_2_mm: float = Field(gt=0.0, le=20.0,
                                      description="distance along second adjacent face")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        from phone_designer.skills._resolvers import (
            _all_faces, _edge_endpoints, _edge_length, resolve_edges,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        edges = resolve_edges(shape, args.edge_selector)
        if not edges:
            raise RuntimeError(
                f"chamfer_asymmetric: selector matched 0 edges: "
                f"{args.edge_selector.model_dump()}"
            )

        all_faces = _all_faces(shape)

        def _find_adjacent_face(target_edge):
            """Return the FIRST face containing target_edge (IsSame match)."""
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
            ref_face = _find_adjacent_face(e)
            if ref_face is None:
                continue
            # 4-arg: Add(Dis1, Dis2, Edge, Face). Dis1 is along `face`, Dis2
            # is along the OTHER adjacent face.
            maker.Add(args.distance_1_mm, args.distance_2_mm, e, ref_face)
            added += 1

        if added == 0:
            raise RuntimeError(
                f"chamfer_asymmetric: no edge-face pair found "
                f"(edges matched={len(edges)})"
            )

        maker.Build()
        if not maker.IsDone():
            raise RuntimeError(
                f"chamfer_asymmetric: BRepFilletAPI_MakeChamfer failed "
                f"(d1={args.distance_1_mm}, d2={args.distance_2_mm}, "
                f"edges={added})"
            )
        new_shape = maker.Shape()

        # freeze
        refs = []
        for e in edges:
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
        freeze = SelectorFreeze.from_entities(refs)

        history = EntityHistoryMap(
            rules={
                "target_edges":   HistoryRule.CONSUMED,
                "result_face":    HistoryRule.GENERATED_NEW,
                "adjacent_faces": HistoryRule.MODIFIED_INHERIT,
            },
        )

        return SkillResult(
            body=Part(new_shape),
            history=history,
            selector_freeze=freeze,
        )
