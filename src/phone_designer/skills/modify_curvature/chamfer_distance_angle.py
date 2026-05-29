"""chamfer_distance_angle — atomic.

Distance-angle chamfer. The user supplies a single distance `d` measured
along the first adjacent face plus an angle `θ` measured from that face;
this maps to a two-distance chamfer with d1=d, d2=d·tan(θ).

θ ∈ (0°, 89°). θ=45° is equivalent to a symmetric chamfer of width d.
"""
from __future__ import annotations

import math
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
    name="chamfer_distance_angle",
    category="modify/chamfer",
    level="atomic",
    summary="Apply a distance-angle chamfer to selected edges. distance is "
            "measured along the first adjacent face and angle is measured "
            "from that face into the chamfer. Internally converted to a "
            "two-distance chamfer (d, d·tan(angle)).",
    selector_kinds=["edges"],
    history_rules={
        "target_edges":   HistoryRule.CONSUMED,
        "result_face":    HistoryRule.GENERATED_NEW,
        "adjacent_faces": HistoryRule.MODIFIED_INHERIT,
    },
    preconditions=[
        "pc.predicate_matches_at_least_one",
        "pc.distance_less_than_half_shortest_edge",
    ],
    produces_features=["chamfer_face_distance_angle"],
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
class ChamferDistanceAngle(SkillBase):
    class Args(BaseModel):
        edge_selector: SelectorRef
        distance_mm: float = Field(gt=0.0, le=20.0,
                                   description="distance along first adjacent face")
        angle_deg: float = Field(gt=0.0, lt=89.0,
                                 description="angle from first face into chamfer")

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
                f"chamfer_distance_angle: selector matched 0 edges: "
                f"{args.edge_selector.model_dump()}"
            )

        d1 = float(args.distance_mm)
        # angle is measured from the first face; the perpendicular extent on
        # the second face is d·tan(θ).
        d2 = d1 * math.tan(math.radians(args.angle_deg))
        if d2 <= 0.0:
            raise RuntimeError(
                f"chamfer_distance_angle: computed d2={d2} ≤ 0 "
                f"(angle={args.angle_deg}°)"
            )

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
            ref_face = _find_adjacent_face(e)
            if ref_face is None:
                continue
            maker.Add(d1, d2, e, ref_face)
            added += 1

        if added == 0:
            raise RuntimeError(
                f"chamfer_distance_angle: no edge-face pair found "
                f"(edges matched={len(edges)})"
            )

        maker.Build()
        if not maker.IsDone():
            raise RuntimeError(
                f"chamfer_distance_angle: BRepFilletAPI_MakeChamfer failed "
                f"(d={d1}, angle={args.angle_deg}°→d2={d2}, edges={added})"
            )
        new_shape = maker.Shape()

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
