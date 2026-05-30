"""edge_concavity_classify — atomic, read-only.

For every edge of the body, classify by the dihedral angle of its
adjacent faces:

    convex  — internal angle < 180°  (edge sticks outward, e.g. box corner)
    concave — internal angle > 180°  (edge is an inner crease, e.g. pocket lip)
    tangent — internal angle ≈ 180°  (faces meet smoothly, e.g. fillet boundary)

Implementation uses BRepAdaptor_Surface to get the outward normals of the
adjacent faces at the edge midpoint and the signed cross product against
the edge tangent.

extras["edge_concavity"] = {
    "convex":  [edge_idx, ...],
    "concave": [edge_idx, ...],
    "tangent": [edge_idx, ...],
    "total":   N,
}

Body unchanged — post ``body_present``.
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


def _edge_midpoint_and_tangent(edge):
    """Return (midpoint_xyz, unit_tangent) at the edge's midpoint."""
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
    """Outward unit normal of face at the surface point closest to xyz."""
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


_TANGENT_DEG = 5.0
_PROBE_EPS_MM = 0.01
# Number of directions sampled in the plane perpendicular to the edge tangent
# when measuring the dihedral angle through the body.
_SAMPLE_DIRS = 24


def _classify(n1, n2, midpoint, tangent, classifier) -> str:
    """Convex / concave / tangent classification.

    First check tangency: if the two outward normals are within ``_TANGENT_DEG``
    of perfectly aligned, the dihedral is ~180° → "tangent" (smooth seam).

    Otherwise estimate the dihedral angle *through the body* by sampling N
    probe points in the plane perpendicular to the edge tangent at ``midpoint``
    and counting how many lie INSIDE the solid:
        fraction_inside ≈ dihedral_through_body / 360°
    A simple solid wedge (e.g. box corner) yields ~90°/360° ≈ 25% inside,
    while an inner crease (e.g. bottom of a blind hole) yields ~270°/360°
    ≈ 75% inside. Threshold at 50% → convex / concave.

    This approach is robust to OCCT face / edge orientation quirks because it
    works directly from solid containment, which is unambiguous.
    """
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    dot = n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]
    dot = max(-1.0, min(1.0, dot))
    angle = math.degrees(math.acos(dot))
    if angle < _TANGENT_DEG:
        return "tangent"

    # Build orthonormal basis (e1, e2) perpendicular to the edge tangent.
    # Prefer n1 projected onto the perpendicular plane as e1 — that anchors
    # the sweep relative to face1 and yields stable enumeration.
    if tangent is None:
        # No tangent → fall back to outward-bisector probe (legacy behaviour).
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
    # Project n1 onto plane ⟂ T: e1_raw = n1 − (n1·T)·T
    dot_n1_t = n1[0] * tx + n1[1] * ty + n1[2] * tz
    e1x = n1[0] - dot_n1_t * tx
    e1y = n1[1] - dot_n1_t * ty
    e1z = n1[2] - dot_n1_t * tz
    e1m = math.sqrt(e1x * e1x + e1y * e1y + e1z * e1z)
    if e1m < 1e-9:
        # n1 is parallel to tangent — degenerate; fall back to convex.
        return "convex"
    e1x, e1y, e1z = e1x / e1m, e1y / e1m, e1z / e1m
    # e2 = T × e1 (right-handed basis)
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
    # 0.5 is the threshold; small margin avoids flicker on near-flat seams.
    if frac > 0.55:
        return "concave"
    return "convex"


@skill(
    name="edge_concavity_classify",
    category="inspect",
    level="atomic",
    summary="Classify every edge of the body by the dihedral angle of its "
            "adjacent faces — convex (<180°), concave (>180°), or tangent "
            "(≈180°). Result on extras['edge_concavity']; body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["edge_concavity"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class EdgeConcavityClassify(SkillBase):
    class Args(BaseModel):
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        from phone_designer.skills._resolvers import _all_edges, _all_faces

        shape = _occt_shape(body)
        edges = _all_edges(shape)
        faces = _all_faces(shape)
        classifier = BRepClass3d_SolidClassifier(shape)

        # Build edge → incident face indices via per-face edge walk
        # (avoids OCP's missing TopTools_ListIteratorOfListOfShape).
        edge_owners: dict[int, list[int]] = {}
        for fi, face in enumerate(faces):
            fit = TopExp_Explorer(face, TopAbs_EDGE)
            while fit.More():
                fe = TopoDS.Edge_s(fit.Current())
                for ei, e in enumerate(edges):
                    if e.IsSame(fe):
                        owners = edge_owners.setdefault(ei, [])
                        if fi not in owners:
                            owners.append(fi)
                        break
                fit.Next()

        convex: list[int] = []
        concave: list[int] = []
        tangent: list[int] = []

        for idx, edge in enumerate(edges):
            owners = edge_owners.get(idx, [])
            if len(owners) < 2:
                # boundary edge (single-face / open) — skip
                continue
            f1, f2 = faces[owners[0]], faces[owners[1]]

            try:
                mid, tan = _edge_midpoint_and_tangent(edge)
            except Exception:
                continue
            n1 = _face_normal_at_xyz(f1, mid)
            n2 = _face_normal_at_xyz(f2, mid)
            if n1 is None or n2 is None:
                continue
            kind = _classify(n1, n2, mid, tan, classifier)
            if kind == "convex":
                convex.append(idx)
            elif kind == "concave":
                concave.append(idx)
            else:
                tangent.append(idx)

        extras = {
            "edge_concavity": {
                "convex": convex,
                "concave": concave,
                "tangent": tangent,
                "total": len(edges),
            },
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
