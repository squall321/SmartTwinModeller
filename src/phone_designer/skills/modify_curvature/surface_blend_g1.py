"""surface_blend_g1 — atomic. G1-tangent surface blend between two faces.

Approach (raw OCCT):
    1. Resolve face A and face B.
    2. Pick the boundary edge of each face that is closest (in 3D distance
       between their midpoints) to the other face.
    3. Drive `BRepFill_Filling` with G1 tangency constraints anchored on
       face A and face B respectively. The filler builds a Geom_Surface
       that interpolates the two boundary edges and is tangent to both
       faces along those edges.
    4. Return the resulting blend face as a new body (Part / TopoDS_Face).

Compared to `face_face_fillet` (which is an edge-fillet fallback), this skill
produces an actual new spline blend surface — so the typical use is between
non-adjacent faces (no shared edge), where an edge fillet doesn't apply.

The result is a single Face (surface body), NOT a solid: post_condition is
`body_present`. Downstream skills (e.g. surface_thicken_variable) can lift
the blend into a solid if needed.

Failure modes:
    - BRepFill_Filling fails to converge (G1 incompatible with edge
      orientations or distance too large) → RuntimeError with edges/face
      info.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _face_edges(face) -> list:
    """Return unique TopoDS_Edge of `face`."""
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


def _edge_midpoint(edge) -> tuple[float, float, float]:
    """Approximate midpoint of an edge (average of endpoint coords)."""
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp
    v1 = TopExp.FirstVertex_s(edge)
    v2 = TopExp.LastVertex_s(edge)
    p1 = BRep_Tool.Pnt_s(v1)
    p2 = BRep_Tool.Pnt_s(v2)
    return (
        (p1.X() + p2.X()) / 2.0,
        (p1.Y() + p2.Y()) / 2.0,
        (p1.Z() + p2.Z()) / 2.0,
    )


def _face_center(face) -> tuple[float, float, float]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    c = props.CentreOfMass()
    return (c.X(), c.Y(), c.Z())


def _dist3(a: tuple, b: tuple) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _pick_nearest_edge(face_src, target_center: tuple[float, float, float],
                       exclude_edges=None):
    """Pick the edge on `face_src` whose midpoint is nearest to `target_center`,
    skipping any edge that IsSame as one of `exclude_edges`."""
    exclude_edges = exclude_edges or []
    best = None
    best_d = float("inf")
    for e in _face_edges(face_src):
        if any(e.IsSame(ex) for ex in exclude_edges):
            continue
        m = _edge_midpoint(e)
        d = _dist3(m, target_center)
        if d < best_d:
            best_d = d
            best = e
    return best


def _shared_edges(face_a, face_b) -> list:
    """Return edges of face_a that IsSame an edge of face_b."""
    a_edges = _face_edges(face_a)
    b_edges = _face_edges(face_b)
    shared = []
    for ea in a_edges:
        for eb in b_edges:
            if ea.IsSame(eb):
                shared.append(ea)
                break
    return shared


def _build_blend_face(face_a, face_b, continuity, blend_radius_mm: float):
    """Drive BRepFill_Filling: tangent constraints on both faces' nearest edges.

    `continuity` is a GeomAbs_Shape value (G1 or G2). `blend_radius_mm` is
    currently used only as an influence on the resolution parameter — the
    actual radius emerges from the inter-face geometry.
    """
    from OCP.BRepFill import BRepFill_Filling

    ca = _face_center(face_a)
    cb = _face_center(face_b)
    # If faces are adjacent (share edges), we must NOT pick that shared edge
    # for both anchors — the filler would degenerate to a zero-area patch.
    shared = _shared_edges(face_a, face_b)
    edge_a = _pick_nearest_edge(face_a, cb, exclude_edges=shared)
    edge_b = _pick_nearest_edge(face_b, ca, exclude_edges=shared)
    if edge_a is None or edge_b is None:
        raise RuntimeError("surface_blend: face has no usable boundary edge")

    # Tighter tolerance for tighter blends.
    tol3d = max(1e-4, blend_radius_mm * 1e-3)
    filler = BRepFill_Filling(3, 15, 2, False, 1e-5, tol3d, 0.01, 0.1, 8, 9)
    try:
        filler.Add(edge_a, face_a, continuity, True)
        filler.Add(edge_b, face_b, continuity, True)
    except Exception as exc:
        raise RuntimeError(
            f"surface_blend: BRepFill_Filling.Add failed: {exc}"
        ) from exc

    try:
        filler.Build()
    except Exception as exc:
        raise RuntimeError(
            f"surface_blend: BRepFill_Filling.Build failed: {exc}"
        ) from exc

    if not filler.IsDone():
        raise RuntimeError(
            "surface_blend: BRepFill_Filling did not converge "
            f"(continuity={continuity}, blend_radius={blend_radius_mm})"
        )
    return filler.Face()


@skill(
    name="surface_blend_g1",
    category="modify/curvature",
    level="atomic",
    summary="G1-tangent surface blend between two faces (possibly non-adjacent). "
            "Builds a new spline surface (via BRepFill_Filling) anchored on the "
            "boundary edges of A and B, tangent to both faces. Returns a Face "
            "(surface body); use surface_thicken_variable to make solid.",
    selector_kinds=["faces"],
    history_rules={
        "face_a":       HistoryRule.MODIFIED_INHERIT,
        "face_b":       HistoryRule.MODIFIED_INHERIT,
        "blend_face":   HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_a_matches_one",
        "pc.face_selector_b_matches_one",
    ],
    produces_features=["surface_blend_g1"],
    preserves=["face_a_geometry", "face_b_geometry"],
    manufacturing={
        "cnc_5axis":         {"min_fillet_r_mm": 0.3},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.filler_did_not_converge",
        "fm.face_has_no_edges",
    ],
    cost_hint=0.35,
    post_conditions=[PostCondition(kind="body_present")],
)
class SurfaceBlendG1(SkillBase):
    class Args(BaseModel):
        face_selector_a: SelectorRef
        face_selector_b: SelectorRef
        blend_radius_mm: float = Field(gt=0.0, le=50.0,
                                        description="Target blend radius hint (mm)")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.GeomAbs import GeomAbs_G1

        from phone_designer.skills._resolvers import resolve_faces

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces_a = resolve_faces(shape, args.face_selector_a, body=body)
        if not faces_a:
            raise RuntimeError(
                "surface_blend_g1: face_selector_a matched 0 faces"
            )
        faces_b = resolve_faces(shape, args.face_selector_b, body=body)
        if not faces_b:
            raise RuntimeError(
                "surface_blend_g1: face_selector_b matched 0 faces"
            )

        blend_face = _build_blend_face(
            faces_a[0], faces_b[0], GeomAbs_G1, args.blend_radius_mm,
        )

        history = EntityHistoryMap(
            rules={
                "face_a":     HistoryRule.MODIFIED_INHERIT,
                "face_b":     HistoryRule.MODIFIED_INHERIT,
                "blend_face": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(blend_face), history=history)
