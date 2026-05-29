"""chamfer_conic — atomic.

Conic-bulge chamfer. `conic_param` ρ ∈ [0, 1] controls the conic profile of
the chamfer face cross-section:
    ρ = 0    → straight chamfer (line cross-section, identical to a
               symmetric chamfer of width `distance_mm`)
    ρ = 0.5  → parabolic (tangent transition, looks like a fillet)
    ρ = 1.0  → quarter-circle (maximum bulge)

v0 limitation: OCCT's stock chamfer API (BRepFilletAPI_MakeChamfer) only
produces flat chamfer faces. To approximate the conic bulge without writing
a full G2 patch builder, we slightly enlarge the chamfer width:
    effective_width = distance_mm · (1 + 0.5 · ρ)
This grows the chamfer with ρ so a ρ-sweep produces a visually distinguishable
family of bodies and the post_condition (face_count_changed) is satisfied.
A future version should construct an actual conic-section face via
GeomAPI_PointsToBSplineSurface and skin the chamfer manually.
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
    name="chamfer_conic",
    category="modify/chamfer",
    level="atomic",
    summary="Apply a conic (bulged) chamfer to selected edges. conic_param "
            "ρ∈[0,1] controls the cross-section: 0=straight, 0.5=tangent, "
            "1=quarter-circle. v0 approximates the bulge by widening the "
            "flat chamfer; a true conic patch is future work.",
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
    produces_features=["chamfer_face_conic"],
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
        "fm.conic_param_out_of_range",
    ],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="face_count_changed")],
)
class ChamferConic(SkillBase):
    class Args(BaseModel):
        edge_selector: SelectorRef
        distance_mm: float = Field(gt=0.0, le=20.0,
                                   description="nominal chamfer width")
        conic_param: float = Field(ge=0.0, le=1.0,
                                   description="0=straight, 0.5=tangent, 1=quarter-circle")

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
                f"chamfer_conic: selector matched 0 edges: "
                f"{args.edge_selector.model_dump()}"
            )

        # v0: widen the flat chamfer by ρ so the family is visually distinct.
        effective = float(args.distance_mm) * (1.0 + 0.5 * float(args.conic_param))

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
            # symmetric chamfer of `effective` width via 4-arg Add.
            maker.Add(effective, effective, e, ref_face)
            added += 1

        if added == 0:
            raise RuntimeError(
                f"chamfer_conic: no edge-face pair found "
                f"(edges matched={len(edges)})"
            )

        maker.Build()
        if not maker.IsDone():
            raise RuntimeError(
                f"chamfer_conic: BRepFilletAPI_MakeChamfer failed "
                f"(effective_width={effective}, ρ={args.conic_param}, "
                f"edges={added})"
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
