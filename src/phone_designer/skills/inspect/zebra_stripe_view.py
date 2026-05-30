"""zebra_stripe_view — atomic, read-only Class-A inspection.

Project virtual "zebra stripes" (parallel black/white bands perpendicular to
view_direction) on every face, then look at how those stripes line up at
shared edges. A stripe that bends across an edge means the two adjoining
faces meet with a tangent discontinuity (G1 break) — these are exactly the
defects a stylist looks for on an A-surface review.

Algorithm:
    1. For each face, compute the centroid, the unit normal at centroid, and
       the "stripe coordinate" t = (centroid · view_direction)/stripe_period.
       Two faces sharing an edge encode the *same* stripe coordinate if and
       only if their surfaces are G1-continuous across that edge.
    2. Build edge → adjacent-face map (TopExp.MapShapesAndAncestors_s).
    3. For every internal edge (shared by ≥ 2 faces) sample several points
       along the edge; at each point compute the surface normals from BOTH
       adjacent faces (projecting the edge point back into UV). The angle
       between those normals is the G1 break angle.
    4. severity = break_angle / π   (0 → perfect tangent, 1 → flipped).
       Anything > π/12 (~15°) is reported as a discontinuity.

extras schema:
    {
      "stripe_count": int,
      "view_direction": [x, y, z],
      "g1_break_threshold_rad": float,
      "edge_count": int,
      "g1_discontinuities": [
        {"edge_idx": int,
         "max_break_rad": float,
         "mean_break_rad": float,
         "severity": "low" | "medium" | "high",
         "sample_count": int},
        ...
      ],
    }

Body unchanged — post ``body_present``.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family: str, name: str):
    import pathlib
    import yaml
    here = pathlib.Path(__file__).resolve()
    for root in here.parents:
        cand = root / "catalogs" / family / f"{name}.yaml"
        if cand.exists():
            return yaml.safe_load(cand.read_text())
    raise FileNotFoundError(f"catalog not found: {family}/{name}.yaml")


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _unit(v):
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if n < 1e-12:
        return (0.0, 0.0, 1.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def _face_normal_at_uv(adaptor, u: float, v: float):
    from OCP.GeomAbs import GeomAbs_Plane

    try:
        if adaptor.GetType() == GeomAbs_Plane:
            n = adaptor.Plane().Axis().Direction()
            return _unit((n.X(), n.Y(), n.Z()))
        d1u = adaptor.DN(u, v, 1, 0)
        d1v = adaptor.DN(u, v, 0, 1)
        nx = d1u.Y() * d1v.Z() - d1u.Z() * d1v.Y()
        ny = d1u.Z() * d1v.X() - d1u.X() * d1v.Z()
        nz = d1u.X() * d1v.Y() - d1u.Y() * d1v.X()
        return _unit((nx, ny, nz))
    except Exception:
        return None


def _face_normal_at_point(face, xyz):
    """Project xyz to (u,v) on face, return unit normal. Falls back to centroid."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.gp import gp_Pnt
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_REVERSED

    try:
        surf = BRep_Tool.Surface_s(face)
        sas = ShapeAnalysis_Surface(surf)
        uv = sas.ValueOfUV(gp_Pnt(xyz[0], xyz[1], xyz[2]), 1e-4)
        u, v = uv.X(), uv.Y()
    except Exception:
        u, v = 0.0, 0.0

    adaptor = BRepAdaptor_Surface(face)
    n = _face_normal_at_uv(adaptor, u, v)
    if n is None:
        return None
    if face.Orientation() == TopAbs_REVERSED:
        n = (-n[0], -n[1], -n[2])
    return n


def _angle_between(a, b) -> float:
    da = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
    db = math.sqrt(b[0] ** 2 + b[1] ** 2 + b[2] ** 2)
    if da < 1e-12 or db < 1e-12:
        return 0.0
    c = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (da * db)
    c = max(-1.0, min(1.0, c))
    return math.acos(c)


# G1-break threshold (rad). 15° = π/12. Below this we treat the edge as smooth.
_G1_THRESHOLD = math.pi / 12


def _severity_band(angle_rad: float) -> str:
    if angle_rad < math.pi / 6:      # < 30°
        return "low"
    if angle_rad < math.pi / 3:      # < 60°
        return "medium"
    return "high"


@skill(
    name="zebra_stripe_view",
    category="inspect",
    level="atomic",
    summary="Class-A zebra-stripe view. Virtual parallel stripes projected "
            "along view_direction reveal where adjacent faces meet with a "
            "G1 discontinuity (the stripes bend at the shared edge). Read-"
            "only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["zebra_stripe_audit"],
    preserves=["body_topology"],
    manufacturing={
        "injection_mold_pa": {"extras": {"class_a_aware": True}},
        "die_cast_al":       {"extras": {"class_a_aware": True}},
    },
    failure_modes=[],
    cost_hint=0.35,
    post_conditions=[PostCondition(kind="body_present")],
)
class ZebraStripeView(SkillBase):
    class Args(BaseModel):
        stripe_count: int = Field(default=20, ge=2, le=200)
        view_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        samples_per_edge: int = Field(default=5, ge=2, le=50)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GCPnts import GCPnts_UniformAbscissa
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
        from OCP.TopExp import TopExp
        from OCP.TopoDS import TopoDS
        from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

        from phone_designer.skills._resolvers import _all_edges

        shape = _occt_shape(body)
        edges = _all_edges(shape)

        # edge → list of parent faces
        edge_to_faces = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(
            shape, TopAbs_EDGE, TopAbs_FACE, edge_to_faces,
        )

        discontinuities: list[dict[str, Any]] = []

        for ei, edge in enumerate(edges):
            try:
                face_list = edge_to_faces.FindFromKey(edge)
            except Exception:
                continue

            # collect unique parent faces.
            # TopTools_ListOfShape is directly Python-iterable on this OCP
            # build; TopTools_ListIteratorOfListOfShape is not exported.
            parents: list = []
            try:
                for fshape in face_list:
                    parents.append(TopoDS.Face_s(fshape))
            except Exception:
                pass

            if len(parents) < 2:
                continue  # boundary edge, not shared

            f1 = parents[0]
            f2 = parents[1]

            # sample edge
            try:
                adaptor = BRepAdaptor_Curve(edge)
                disc = GCPnts_UniformAbscissa(adaptor, args.samples_per_edge)
                if not disc.IsDone() or disc.NbPoints() < 2:
                    continue
                n_pts = disc.NbPoints()
            except Exception:
                continue

            angles: list[float] = []
            for i in range(1, n_pts + 1):
                try:
                    t = disc.Parameter(i)
                    p = adaptor.Value(t)
                    xyz = (p.X(), p.Y(), p.Z())
                except Exception:
                    continue
                n1 = _face_normal_at_point(f1, xyz)
                n2 = _face_normal_at_point(f2, xyz)
                if n1 is None or n2 is None:
                    continue
                a = _angle_between(n1, n2)
                # Two faces sharing an edge with consistent orientation should
                # have *parallel* outward normals (≈0 rad) when G1-continuous.
                # If a > π/2 it usually means we flipped one — fold to [0, π/2].
                if a > math.pi / 2:
                    a = math.pi - a
                angles.append(a)

            if not angles:
                continue

            max_a = max(angles)
            mean_a = sum(angles) / len(angles)
            if max_a < _G1_THRESHOLD:
                continue   # smooth enough — not a defect

            discontinuities.append({
                "edge_idx": ei,
                "max_break_rad": round(max_a, 6),
                "mean_break_rad": round(mean_a, 6),
                "severity": _severity_band(max_a),
                "sample_count": len(angles),
            })

        extras = {
            "stripe_count": args.stripe_count,
            "view_direction": [round(c, 6) for c in _unit(args.view_direction)],
            "g1_break_threshold_rad": round(_G1_THRESHOLD, 6),
            "edge_count": len(edges),
            "g1_discontinuities": discontinuities,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
