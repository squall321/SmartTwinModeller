"""fill_surface_patch — atomic. N-sided fill surface from arbitrary boundary edges.

The general "Filled Surface" / "N-sided Surface" / "Patch" op: span an ARBITRARY
closed loop of N boundary edges with a smooth surface, each edge optionally tangent
(G1) or curvature-continuous (G2) to the face it belongs to. Closes an open boundary,
builds a transition skin, caps a hole.

Distinct from ``surface_blend_g1`` / ``surface_blend_g2``, which hard-wire EXACTLY
two faces / one boundary edge each with a single uniform continuity — they cannot
span an N-edge loop the caller picks. This resolves a caller-supplied edge selector
into the full boundary set and Adds each to one ``BRepFill_Filling``.

Returns a FACE (surface body), not a solid — the codebase convention for surfacing
ops (post_condition body_present, like surface_blend_*). Feed it to
``surface_thicken_variable`` to solidify. Continuity per edge:
  * 'c0' — the edge is a free boundary (position only);
  * 'g1' / 'g2' — the edge stays tangent / curvature-continuous to ITS OWN face
    (so the patch flows smoothly out of the surrounding geometry).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _face_edges(face) -> list:
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


@skill(
    name="fill_surface_patch",
    category="modify/curvature",
    level="atomic",
    summary="N-sided fill surface: span the boundary edges matched by an edge/face "
            "selector with a smooth patch (BRepFill_Filling), each edge free (c0) or "
            "tangent/curvature-continuous (g1/g2) to its own face. General "
            "Filled-Surface / N-sided-Patch — unlike surface_blend_g1/g2 (exactly 2 "
            "faces). Returns a FACE (surface body); thicken to solidify.",
    selector_kinds=["faces", "edges"],
    history_rules={"patch_face": HistoryRule.GENERATED_NEW},
    produces_features=["fill_surface_patch"],
    preserves=[],
    manufacturing={"cnc_5axis": {"min_fillet_r_mm": 0.3}},
    failure_modes=["fm.no_boundary_edges", "fm.filler_did_not_converge"],
    cost_hint=0.35,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class FillSurfacePatch(SkillBase):
    class Args(BaseModel):
        boundary_selector: SelectorRef = Field(
            description="Selects the boundary. If it resolves to FACES, every edge "
                        "of each matched face is added, tangent/curvature-continuous "
                        "to that face per `continuity`. If it resolves to EDGES, "
                        "those edges are added as free (c0) boundaries.")
        continuity: Literal["c0", "g1", "g2"] = Field(
            default="g1",
            description="Continuity of each face-boundary edge to its face: c0 "
                        "(position), g1 (tangent), g2 (curvature). Ignored for a "
                        "pure edge selector (always c0).")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepFill import BRepFill_Filling
        from OCP.GeomAbs import GeomAbs_C0, GeomAbs_G1, GeomAbs_G2

        from phone_designer.skills._resolvers import resolve_edges, resolve_faces

        if body is None:
            raise ValueError("fm.no_boundary_edges: fill_surface_patch needs a body.")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        cont = {"c0": GeomAbs_C0, "g1": GeomAbs_G1, "g2": GeomAbs_G2}[args.continuity]

        filler = BRepFill_Filling(3, 15, 2, False, 1e-5, 1e-4, 0.01, 0.1, 8, 9)

        n_added = 0
        # Try FACES first (the common case — cap a hole ringed by faces / span a
        # gap tangent to the surrounding faces). Fall back to a raw EDGE selector.
        faces = []
        try:
            faces = resolve_faces(shape, args.boundary_selector, body=body)
        except Exception:
            faces = []
        if faces:
            for f in faces:
                for e in _face_edges(f):
                    filler.Add(e, f, cont, True)
                    n_added += 1
        else:
            edges = resolve_edges(shape, args.boundary_selector)
            for e in edges:
                filler.Add(e, GeomAbs_C0, True)
                n_added += 1

        if n_added == 0:
            raise ValueError(
                "fm.no_boundary_edges: the selector matched no faces or edges to "
                "bound the patch.")

        filler.Build()
        if not filler.IsDone():
            raise ValueError(
                "fm.filler_did_not_converge: BRepFill_Filling failed on the "
                f"{n_added}-edge boundary — the loop may be non-planar/open or the "
                "continuity too strict.")
        patch = filler.Face()

        return SkillResult(
            body=Part(patch),
            history=EntityHistoryMap(rules={"patch_face": HistoryRule.GENERATED_NEW}),
            extras={"fill_surface": {
                "n_boundary_edges": n_added,
                "continuity": args.continuity,
                "is_solid": False,
                "grade": "surface",
            }},
        )
