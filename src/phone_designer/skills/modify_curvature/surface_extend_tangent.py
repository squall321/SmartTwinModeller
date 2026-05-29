"""surface_extend_tangent — atomic. Extend a face along a boundary edge.

Approach (raw OCCT):
    1. Resolve the source face A and pick its boundary edge nearest to a
       caller-provided direction hint (or, by default, the edge with the
       maximum span — the most natural extension boundary).
    2. Compute the outward tangent direction of A at the edge midpoint:
       the direction along A that points AWAY from A's center (so the
       extension grows outside the face, not back inside).
    3. Translate the source edge by `distance_mm * tangent` to get a
       parallel "far" edge.
    4. Build the four-sided patch with BRepFill_Filling: source edge is
       G1-bound to the source face; the three other edges of the
       quadrilateral (the two side connectors + the far edge) are simple
       C0 constraints. The resulting face is tangent to A along the
       source edge and grows outward by `distance_mm`.

If GeomPlate_BuildPlateSurface ever becomes the preferred path, the
helper `_build_extension_face` can be swapped without touching the
public skill class.
"""
from __future__ import annotations

import math
from typing import Any

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


def _edge_endpoints(edge):
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp
    v1 = TopExp.FirstVertex_s(edge)
    v2 = TopExp.LastVertex_s(edge)
    return BRep_Tool.Pnt_s(v1), BRep_Tool.Pnt_s(v2)


def _edge_length(edge) -> float:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint
    return GCPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(edge))


def _edge_midpoint(edge) -> tuple[float, float, float]:
    p1, p2 = _edge_endpoints(edge)
    return ((p1.X() + p2.X()) / 2.0,
            (p1.Y() + p2.Y()) / 2.0,
            (p1.Z() + p2.Z()) / 2.0)


def _face_center(face) -> tuple[float, float, float]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    c = props.CentreOfMass()
    return (c.X(), c.Y(), c.Z())


def _pick_longest_edge(face):
    """Return the boundary edge of `face` with maximum length."""
    best = None
    best_len = -1.0
    for e in _face_edges(face):
        L = _edge_length(e)
        if L > best_len:
            best_len = L
            best = e
    return best


def _outward_tangent_on_face(face, edge_midpoint, edge_dir) -> tuple[float, float, float]:
    """Compute a unit vector that lies in the face's tangent plane at the
    edge midpoint, perpendicular to the edge direction, pointing AWAY from
    the face center.

    For a planar face this reduces to: face_normal × edge_dir, sign chosen
    so the vector points outward (component along (mid - face_center) > 0).
    For a curved face we project the (mid - face_center) vector onto the
    plane perpendicular to edge_dir to get an approximation — good enough
    for the extension purpose.
    """
    fc = _face_center(face)
    mx, my, mz = edge_midpoint
    rx, ry, rz = mx - fc[0], my - fc[1], mz - fc[2]

    # Project (mid - center) onto the plane perpendicular to edge_dir.
    ex, ey, ez = edge_dir
    en = math.sqrt(ex * ex + ey * ey + ez * ez)
    if en < 1e-9:
        # degenerate edge — use raw radial.
        rn = math.sqrt(rx * rx + ry * ry + rz * rz)
        if rn < 1e-9:
            return (1.0, 0.0, 0.0)
        return (rx / rn, ry / rn, rz / rn)
    ex, ey, ez = ex / en, ey / en, ez / en
    dot = rx * ex + ry * ey + rz * ez
    tx, ty, tz = rx - dot * ex, ry - dot * ey, rz - dot * ez
    tn = math.sqrt(tx * tx + ty * ty + tz * tz)
    if tn < 1e-9:
        # midpoint coincides with center projection — pick an arbitrary
        # perpendicular.
        return (1.0, 0.0, 0.0)
    return (tx / tn, ty / tn, tz / tn)


def _edge_direction(edge) -> tuple[float, float, float]:
    """Approximate edge tangent direction: end - start (normalized)."""
    p1, p2 = _edge_endpoints(edge)
    dx, dy, dz = p2.X() - p1.X(), p2.Y() - p1.Y(), p2.Z() - p1.Z()
    n = math.sqrt(dx * dx + dy * dy + dz * dz)
    if n < 1e-9:
        return (1.0, 0.0, 0.0)
    return (dx / n, dy / n, dz / n)


def _make_line_edge(p1_xyz, p2_xyz):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Pnt
    me = BRepBuilderAPI_MakeEdge(
        gp_Pnt(*p1_xyz), gp_Pnt(*p2_xyz),
    )
    if not me.IsDone():
        raise RuntimeError("surface_extend_tangent: MakeEdge(line) failed")
    return me.Edge()


def _build_extension_face(face, source_edge, distance_mm: float):
    """Build the extension Face by driving BRepFill_Filling with:
       - source_edge ↔ face (G1)
       - 3 free C0 edges forming a quadrilateral patch outward by distance_mm.
    """
    from OCP.BRepFill import BRepFill_Filling
    from OCP.GeomAbs import GeomAbs_C0, GeomAbs_G1

    p1, p2 = _edge_endpoints(source_edge)
    mid = _edge_midpoint(source_edge)
    edir = _edge_direction(source_edge)
    out_dir = _outward_tangent_on_face(face, mid, edir)

    # Translated edge endpoints (parallel to source, offset by distance_mm).
    q1 = (p1.X() + out_dir[0] * distance_mm,
          p1.Y() + out_dir[1] * distance_mm,
          p1.Z() + out_dir[2] * distance_mm)
    q2 = (p2.X() + out_dir[0] * distance_mm,
          p2.Y() + out_dir[1] * distance_mm,
          p2.Z() + out_dir[2] * distance_mm)

    side_a = _make_line_edge((p1.X(), p1.Y(), p1.Z()), q1)
    side_b = _make_line_edge((p2.X(), p2.Y(), p2.Z()), q2)
    far_edge = _make_line_edge(q1, q2)

    filler = BRepFill_Filling(3, 15, 2, False, 1e-5, 1e-4, 0.01, 0.1, 8, 9)
    try:
        # Source edge anchors tangency to the source face.
        filler.Add(source_edge, face, GeomAbs_G1, True)
        filler.Add(side_a, GeomAbs_C0, True)
        filler.Add(far_edge, GeomAbs_C0, True)
        filler.Add(side_b, GeomAbs_C0, True)
    except Exception as exc:
        raise RuntimeError(
            f"surface_extend_tangent: filler.Add failed: {exc}"
        ) from exc

    try:
        filler.Build()
    except Exception as exc:
        raise RuntimeError(
            f"surface_extend_tangent: filler.Build failed: {exc}"
        ) from exc

    if not filler.IsDone():
        raise RuntimeError(
            "surface_extend_tangent: BRepFill_Filling did not converge "
            f"(distance={distance_mm})"
        )
    return filler.Face()


@skill(
    name="surface_extend_tangent",
    category="modify/curvature",
    level="atomic",
    summary="Extend a face along one of its boundary edges by `distance_mm`, "
            "tangent (G1) to the source face. Picks the longest boundary edge "
            "by default and builds a quadrilateral extension patch via "
            "BRepFill_Filling. Returns the new face (surface body).",
    selector_kinds=["faces"],
    history_rules={
        "source_face":     HistoryRule.MODIFIED_INHERIT,
        "extension_face":  HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
    ],
    produces_features=["surface_extension"],
    preserves=["source_face_geometry"],
    manufacturing={
        "cnc_5axis": {"min_fillet_r_mm": 0.3},
    },
    failure_modes=[
        "fm.filler_did_not_converge",
        "fm.face_has_no_edges",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class SurfaceExtendTangent(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        distance_mm: float = Field(gt=0.0, le=200.0,
                                    description="Extension length along face tangent (mm)")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        from phone_designer.skills._resolvers import resolve_faces

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                "surface_extend_tangent: face_selector matched 0 faces"
            )
        source_face = faces[0]
        source_edge = _pick_longest_edge(source_face)
        if source_edge is None:
            raise RuntimeError(
                "surface_extend_tangent: face has no boundary edges"
            )

        extension_face = _build_extension_face(
            source_face, source_edge, args.distance_mm,
        )

        history = EntityHistoryMap(
            rules={
                "source_face":    HistoryRule.MODIFIED_INHERIT,
                "extension_face": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(extension_face), history=history)
