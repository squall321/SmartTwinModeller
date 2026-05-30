"""face_adjacency_graph — atomic, read-only.

Walks every face of the body and records the topology graph: for each
shared edge between two faces, emit an edge entry with the shared edge's
length and a dihedral concavity classification.

extras["face_graph"] = {
    "node_count": N,        # number of faces (nodes)
    "edges": [
        {"a": face_idx_a, "b": face_idx_b,
         "len": shared_edge_length_mm,
         "concavity": "convex" | "concave" | "tangent"},
        ...
    ],
}

Body unchanged — post ``body_present``.

Concavity rule: at the shared edge midpoint, take both adjacent faces'
outward normals; classify by the signed dihedral angle. Tangent if the
normals are nearly aligned (~180° internal angle), convex if they bend
outward (<180°), concave if they bend inward (>180°). Curved surfaces
use BRepAdaptor_Surface to evaluate the normal at the local parameter.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _edge_midpoint_xyz(edge):
    """Midpoint of the edge in 3D, plus unit tangent at midpoint."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.gp import gp_Pnt, gp_Vec

    ac = BRepAdaptor_Curve(edge)
    u_first = ac.FirstParameter()
    u_last = ac.LastParameter()
    u_mid = 0.5 * (u_first + u_last)
    p = gp_Pnt()
    d1 = gp_Vec()
    ac.D1(u_mid, p, d1)
    mid = (p.X(), p.Y(), p.Z())
    mag = math.sqrt(d1.X() ** 2 + d1.Y() ** 2 + d1.Z() ** 2)
    if mag < 1e-12:
        tan = None
    else:
        tan = (d1.X() / mag, d1.Y() / mag, d1.Z() / mag)
    return mid, tan


def _face_normal_at_xyz(face, xyz):
    """Evaluate outward unit normal of ``face`` at the point closest to ``xyz``.

    Returns (nx, ny, nz) or None on failure.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.gp import gp_Pnt
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface
    from OCP.TopAbs import TopAbs_REVERSED

    try:
        surf = BRep_Tool.Surface_s(face)
        sas = ShapeAnalysis_Surface(surf)
        uv = sas.ValueOfUV(gp_Pnt(xyz[0], xyz[1], xyz[2]), 1e-4)
        u, v = uv.X(), uv.Y()
    except Exception:
        return None

    try:
        adaptor = BRepAdaptor_Surface(face)
        slp = BRepLProp_SLProps(adaptor, u, v, 1, 1e-6)
        if not slp.IsNormalDefined():
            return None
        n = slp.Normal()
        nx, ny, nz = n.X(), n.Y(), n.Z()
        if face.Orientation() == TopAbs_REVERSED:
            nx, ny, nz = -nx, -ny, -nz
        return (nx, ny, nz)
    except Exception:
        return None


# Concavity tolerance: within this angle of perfect tangency → "tangent".
_TANGENT_DEG = 5.0
# Probe step (mm) along sample directions in the plane perpendicular to the
# edge tangent — big enough to escape OCCT linear tolerance (~1e-7 mm), small
# enough that the probe lies inside any reasonable feature.
_PROBE_EPS_MM = 0.01
# Number of sample directions around the edge for dihedral measurement.
_SAMPLE_DIRS = 24


def _classify_concavity(n1, n2, midpoint, tangent, classifier) -> str:
    """Classify dihedral as convex / concave / tangent.

    Tangent is detected when the two outward normals are aligned (<5°).
    Otherwise estimate dihedral-through-body by sampling ``_SAMPLE_DIRS``
    probe points uniformly in the plane perpendicular to the edge tangent
    and counting how many fall inside the solid:
        fraction_inside ≈ dihedral_through_body / 360°
    A simple wedge (box corner) gives ~25 % inside (convex), an inner crease
    (hole bottom lip) gives ~75 % (concave). 50 % threshold with a small
    margin avoids flicker on near-flat seams.
    """
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    dot = n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]
    dot = max(-1.0, min(1.0, dot))
    angle_between = math.degrees(math.acos(dot))
    if angle_between < _TANGENT_DEG:
        return "tangent"

    if tangent is None:
        # Fallback: outward-bisector probe (less reliable).
        sx = n1[0] + n2[0]
        sy = n1[1] + n2[1]
        sz = n1[2] + n2[2]
        mag = math.sqrt(sx * sx + sy * sy + sz * sz)
        if mag < 1e-9:
            return "convex"
        ux, uy, uz = sx / mag, sy / mag, sz / mag
        probe = gp_Pnt(
            midpoint[0] + _PROBE_EPS_MM * ux,
            midpoint[1] + _PROBE_EPS_MM * uy,
            midpoint[2] + _PROBE_EPS_MM * uz,
        )
        classifier.Perform(probe, 1e-6)
        return "concave" if classifier.State() == TopAbs_IN else "convex"

    tx, ty, tz = tangent
    # Project n1 onto the plane ⟂ T → e1 (anchored to face1 for stable sweep).
    dot_n1_t = n1[0] * tx + n1[1] * ty + n1[2] * tz
    e1x = n1[0] - dot_n1_t * tx
    e1y = n1[1] - dot_n1_t * ty
    e1z = n1[2] - dot_n1_t * tz
    e1m = math.sqrt(e1x * e1x + e1y * e1y + e1z * e1z)
    if e1m < 1e-9:
        return "convex"
    e1x, e1y, e1z = e1x / e1m, e1y / e1m, e1z / e1m
    # e2 = T × e1 — right-handed basis in plane ⟂ T.
    e2x = ty * e1z - tz * e1y
    e2y = tz * e1x - tx * e1z
    e2z = tx * e1y - ty * e1x

    inside = 0
    counted = 0
    for k in range(_SAMPLE_DIRS):
        theta = 2.0 * math.pi * k / _SAMPLE_DIRS
        cs = math.cos(theta)
        sn = math.sin(theta)
        dx = cs * e1x + sn * e2x
        dy = cs * e1y + sn * e2y
        dz = cs * e1z + sn * e2z
        probe = gp_Pnt(
            midpoint[0] + _PROBE_EPS_MM * dx,
            midpoint[1] + _PROBE_EPS_MM * dy,
            midpoint[2] + _PROBE_EPS_MM * dz,
        )
        try:
            classifier.Perform(probe, 1e-6)
            counted += 1
            if classifier.State() == TopAbs_IN:
                inside += 1
        except Exception:
            continue

    if counted == 0:
        return "convex"
    frac = inside / counted
    if frac > 0.55:
        return "concave"
    return "convex"


@skill(
    name="face_adjacency_graph",
    category="inspect",
    level="atomic",
    summary="Build the topology graph of the body — for every pair of faces "
            "sharing an edge, record the shared edge length and the dihedral "
            "concavity (convex / concave / tangent). Result on "
            "extras['face_graph']; body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["face_graph"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class FaceAdjacencyGraph(SkillBase):
    class Args(BaseModel):
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        from phone_designer.skills._resolvers import (
            _all_edges,
            _all_faces,
            _edge_length,
        )

        shape = _occt_shape(body)
        faces = _all_faces(shape)
        edges = _all_edges(shape)
        classifier = BRepClass3d_SolidClassifier(shape)

        # Build edge → list of incident face indices via per-face edge walk.
        # Avoids OCP's TopTools_ListIteratorOfListOfShape (missing on this build).
        edge_owners: dict[int, list[int]] = {}
        for fi, face in enumerate(faces):
            fit = TopExp_Explorer(face, TopAbs_EDGE)
            while fit.More():
                fe = TopoDS.Edge_s(fit.Current())
                # find this edge in `edges` (IsSame-based) — small loop, fine for
                # phones / housings where face_count ≲ 200.
                for ei, e in enumerate(edges):
                    if e.IsSame(fe):
                        owners = edge_owners.setdefault(ei, [])
                        if fi not in owners:
                            owners.append(fi)
                        break
                fit.Next()

        graph_edges: list[dict[str, Any]] = []
        for ei, edge in enumerate(edges):
            owners = edge_owners.get(ei, [])
            if len(owners) < 2:
                continue
            ia, ib = owners[0], owners[1]
            if ia == ib:
                continue
            a, b = (ia, ib) if ia < ib else (ib, ia)
            f1, f2 = faces[a], faces[b]

            try:
                length = float(_edge_length(edge))
            except Exception:
                length = 0.0

            try:
                mid, tan = _edge_midpoint_xyz(edge)
            except Exception:
                mid = None
                tan = None

            concavity = "tangent"
            if mid is not None:
                n1 = _face_normal_at_xyz(f1, mid)
                n2 = _face_normal_at_xyz(f2, mid)
                if n1 is not None and n2 is not None:
                    concavity = _classify_concavity(n1, n2, mid, tan, classifier)

            graph_edges.append({
                "a": a,
                "b": b,
                "len": round(length, 6),
                "concavity": concavity,
            })

        extras = {
            "face_graph": {
                "node_count": len(faces),
                "edges": graph_edges,
            },
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
