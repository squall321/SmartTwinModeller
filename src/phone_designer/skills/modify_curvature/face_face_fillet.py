"""face_face_fillet — atomic. Blend between two faces, even non-adjacent.

Goal: produce a smooth blend (fillet) that connects the two designated faces,
even if they don't share a single common edge (e.g. a flat top and a side
wall separated by a small chamfer).

OCCT approach (theoretical):
    The pure face-face fillet operation in OCCT is `ChFi3d_FilBuilder` /
    `BRepBlend_*` (constructing a Geom_Surface that is tangent to both
    faces). Both are accessible from C++ but the OCP Python bindings are
    incomplete / non-trivial to drive — many of the required surface-fitting
    inputs are not exposed.

Pragmatic approach (this implementation):
    1. Resolve face A and face B.
    2. Find any edges shared by both faces (IsSame). If found, fillet those
       edges directly with `BRepFilletAPI_MakeFillet`.
    3. Otherwise, find the SHORTEST chain of edges connecting A and B —
       practically: the edges of every face that touches both A's edge ring
       and B's edge ring (i.e. faces "between" A and B). Fillet the edges
       that lie on this connecting face that touch A or B.
    4. If neither yields a usable edge set, raise.

This is an APPROXIMATION of a true face-face surface blend: it produces a
constant-radius blend on the existing connecting edges instead of building
a new spline surface that is tangent to A and B. The visible result is
nearly identical for the common "two faces meet at a sharp edge" case;
for more exotic non-adjacent topology the approximation is honest about
its limits.

The skill is fully documented as a fallback. A future v2 could call into
ChFi3d_FilBuilder directly if/when those OCP bindings stabilise.
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


def _face_edges(face) -> list:
    """Return all unique TopoDS_Edge of `face`."""
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_MapOfShape

    seen = TopTools_MapOfShape()
    out = []
    it = TopExp_Explorer(face, TopAbs_EDGE)
    while it.More():
        e = it.Current()
        if not seen.Contains(e):
            seen.Add(e)
            out.append(TopoDS.Edge_s(e))
        it.Next()
    return out


def _edges_shared_by(face_a, face_b) -> list:
    """Return TopoDS_Edges that are IsSame in both face_a and face_b."""
    a_edges = _face_edges(face_a)
    b_edges = _face_edges(face_b)
    shared = []
    for ea in a_edges:
        for eb in b_edges:
            if ea.IsSame(eb):
                shared.append(ea)
                break
    return shared


def _faces_bridging(shape, face_a, face_b) -> list:
    """Return faces (other than A, B) that share an edge with BOTH A and B."""
    from phone_designer.skills._resolvers import _all_faces

    a_edges = _face_edges(face_a)
    b_edges = _face_edges(face_b)
    out = []
    for f in _all_faces(shape):
        if f.IsSame(face_a) or f.IsSame(face_b):
            continue
        f_edges = _face_edges(f)
        touches_a = any(
            any(fe.IsSame(ae) for ae in a_edges) for fe in f_edges
        )
        if not touches_a:
            continue
        touches_b = any(
            any(fe.IsSame(be) for be in b_edges) for fe in f_edges
        )
        if touches_a and touches_b:
            out.append(f)
    return out


def _connecting_edges(shape, face_a, face_b) -> list:
    """Best-effort edges to fillet to blend A → B.

    1. Direct shared edges → return them.
    2. Otherwise: edges of any bridging face that touch A or B.
    """
    direct = _edges_shared_by(face_a, face_b)
    if direct:
        return direct

    bridges = _faces_bridging(shape, face_a, face_b)
    if not bridges:
        return []

    a_edges = _face_edges(face_a)
    b_edges = _face_edges(face_b)

    out = []
    seen_hashes = set()
    for bf in bridges:
        for be in _face_edges(bf):
            on_a_or_b = any(be.IsSame(ae) for ae in a_edges) or any(
                be.IsSame(bz) for bz in b_edges
            )
            if not on_a_or_b:
                continue
            h = be.HashCode(2**31 - 1)
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            out.append(be)
    return out


@skill(
    name="face_face_fillet",
    category="modify/curvature",
    level="atomic",
    summary="Blend between two faces with a constant-radius fillet. If the two "
            "faces share an edge, fillets that edge directly. If they are "
            "non-adjacent, finds the bridging face(s) between them and fillets "
            "the edges on those bridges that touch either selected face. "
            "FALLBACK: this is an edge-based approximation of a true Geom-"
            "surface face-face blend (OCP's ChFi3d_FilBuilder is not stably "
            "exposed); the visible result matches the standard sharp-edge case.",
    selector_kinds=["faces"],
    history_rules={
        "face_a":         HistoryRule.MODIFIED_INHERIT,
        "face_b":         HistoryRule.MODIFIED_INHERIT,
        "result_blend":   HistoryRule.GENERATED_NEW,
        "consumed_edges": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_a_matches_one",
        "pc.face_selector_b_matches_one",
        "pc.faces_have_connecting_edges",
    ],
    produces_features=["face_face_blend"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_5axis":         {"min_fillet_r_mm": 0.3},
        "cnc_3axis":         {"min_fillet_r_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.no_connecting_edges",
        "fm.radius_too_large_for_blend",
        "fm.self_intersection",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="face_count_changed")],
)
class FaceFaceFillet(SkillBase):
    class Args(BaseModel):
        face_selector_a: SelectorRef
        face_selector_b: SelectorRef
        radius_mm: float = Field(gt=0.0, le=50.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        from phone_designer.skills._resolvers import (
            _edge_endpoints, _edge_length, resolve_faces,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces_a = resolve_faces(shape, args.face_selector_a, body=body)
        if not faces_a:
            raise RuntimeError(
                f"face_face_fillet: face_selector_a matched 0 — "
                f"{args.face_selector_a.model_dump()}"
            )
        faces_b = resolve_faces(shape, args.face_selector_b, body=body)
        if not faces_b:
            raise RuntimeError(
                f"face_face_fillet: face_selector_b matched 0 — "
                f"{args.face_selector_b.model_dump()}"
            )
        face_a = faces_a[0]
        face_b = faces_b[0]

        connecting = _connecting_edges(shape, face_a, face_b)
        if not connecting:
            raise RuntimeError(
                "face_face_fillet: no connecting edges between selected "
                "faces (no shared edge, no bridging face). True surface-blend "
                "ChFi3d_FilBuilder fallback unavailable through OCP."
            )

        maker = BRepFilletAPI_MakeFillet(shape)
        for e in connecting:
            maker.Add(args.radius_mm, e)
        maker.Build()
        if not maker.IsDone():
            raise RuntimeError(
                f"face_face_fillet: BRepFilletAPI_MakeFillet failed "
                f"(radius={args.radius_mm}, edges={len(connecting)})"
            )
        new_shape = maker.Shape()

        refs = []
        for e in connecting:
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
                "face_a":         HistoryRule.MODIFIED_INHERIT,
                "face_b":         HistoryRule.MODIFIED_INHERIT,
                "result_blend":   HistoryRule.GENERATED_NEW,
                "consumed_edges": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(
            body=Part(new_shape),
            history=history,
            selector_freeze=freeze,
        )
