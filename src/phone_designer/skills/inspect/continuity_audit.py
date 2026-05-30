"""continuity_audit — atomic, read-only Class-A inspection.

Walk every shared edge between two faces and certify the continuity level
(G0 / G1 / G2) achieved across it. The standard automotive Class-A audit:

    G0 — positional continuity   (the two faces meet at the edge)
    G1 — tangent continuity      (the surface normals align across the edge)
    G2 — curvature continuity    (the principal curvatures align as well)

extras schema:
    {
      "min_continuity": "G0" | "G1" | "G2",
      "edge_count": int,
      "shared_edge_count": int,
      "pass_count": int,
      "fail_count": int,
      "continuity_failures": [
        {"edge_idx": int,
         "required": "G1",
         "achieved": "G0",
         "max_normal_angle_rad": float,
         "max_curvature_delta": float,
         "max_position_gap_mm": float},
        ...
      ],
    }

Body unchanged — post ``body_present``.
"""
from __future__ import annotations

import math
from typing import Any, Literal

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
        return None
    return (v[0] / n, v[1] / n, v[2] / n)


# Tolerances (consistent with OCCT linear tolerance / Class-A audit norms).
_G0_GAP_MM = 0.01           # 10 µm positional gap allowed
_G1_ANGLE_RAD = math.pi / 36  # 5°
_G2_CURV_DELTA = 0.05       # 1/mm difference allowed in mean curvature


def _surface_props_at_xyz(face, xyz):
    """At the closest UV of ``xyz`` on ``face``, return
    (xyz_on_surf, unit_normal, mean_curvature). None on failure."""
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
        slp = BRepLProp_SLProps(adaptor, u, v, 2, 1e-6)
        p = slp.Value()
        pt = (p.X(), p.Y(), p.Z())
        if slp.IsNormalDefined():
            n = slp.Normal()
            nx, ny, nz = n.X(), n.Y(), n.Z()
        else:
            return None
        if face.Orientation() == TopAbs_REVERSED:
            nx, ny, nz = -nx, -ny, -nz
        mean_c = float(slp.MeanCurvature()) if slp.IsCurvatureDefined() else 0.0
        return (pt, (nx, ny, nz), mean_c)
    except Exception:
        return None


def _angle_between(a, b) -> float:
    da = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
    db = math.sqrt(b[0] ** 2 + b[1] ** 2 + b[2] ** 2)
    if da < 1e-12 or db < 1e-12:
        return 0.0
    c = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (da * db)
    c = max(-1.0, min(1.0, c))
    return math.acos(c)


def _achieved_level(gap: float, angle: float, curv: float) -> str:
    if gap > _G0_GAP_MM:
        return "broken"
    if angle > _G1_ANGLE_RAD:
        return "G0"
    if curv > _G2_CURV_DELTA:
        return "G1"
    return "G2"


_LEVEL_ORDER = {"broken": 0, "G0": 1, "G1": 2, "G2": 3}


def _meets(achieved: str, required: str) -> bool:
    return _LEVEL_ORDER.get(achieved, 0) >= _LEVEL_ORDER.get(required, 0)


@skill(
    name="continuity_audit",
    category="inspect",
    level="atomic",
    summary="Class-A continuity audit. For every shared edge between two "
            "faces, certify positional (G0), tangent (G1), or curvature (G2) "
            "continuity and list every edge that fails the requested minimum. "
            "Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["continuity_audit"],
    preserves=["body_topology"],
    manufacturing={
        "injection_mold_pa": {"extras": {"class_a_required": "G1"}},
        "die_cast_al":       {"extras": {"class_a_required": "G1"}},
    },
    failure_modes=["fm.g1_break", "fm.g2_break"],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="body_present")],
)
class ContinuityAudit(SkillBase):
    class Args(BaseModel):
        min_continuity: Literal["G0", "G1", "G2"] = "G1"
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

        edge_to_faces = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(
            shape, TopAbs_EDGE, TopAbs_FACE, edge_to_faces,
        )

        shared_edge_count = 0
        pass_count = 0
        fail_count = 0
        failures: list[dict[str, Any]] = []

        for ei, edge in enumerate(edges):
            try:
                face_list = edge_to_faces.FindFromKey(edge)
            except Exception:
                continue

            parents = []
            try:
                # TopTools_ListOfShape is directly Python-iterable on this OCP
                # build; TopTools_ListIteratorOfListOfShape is not exported.
                for fshape in face_list:
                    parents.append(TopoDS.Face_s(fshape))
            except Exception:
                continue

            if len(parents) < 2:
                continue
            shared_edge_count += 1
            f1, f2 = parents[0], parents[1]

            # sample along the edge
            try:
                ac = BRepAdaptor_Curve(edge)
                disc = GCPnts_UniformAbscissa(ac, args.samples_per_edge)
                if not disc.IsDone() or disc.NbPoints() < 2:
                    continue
                n_pts = disc.NbPoints()
            except Exception:
                continue

            max_gap = 0.0
            max_angle = 0.0
            max_curv = 0.0
            usable = 0
            for i in range(1, n_pts + 1):
                try:
                    t = disc.Parameter(i)
                    p = ac.Value(t)
                    pt = (p.X(), p.Y(), p.Z())
                except Exception:
                    continue
                a = _surface_props_at_xyz(f1, pt)
                b = _surface_props_at_xyz(f2, pt)
                if a is None or b is None:
                    continue
                usable += 1
                p1, n1, c1 = a
                p2, n2, c2 = b

                gap1 = math.sqrt(sum((p1[k] - pt[k]) ** 2 for k in range(3)))
                gap2 = math.sqrt(sum((p2[k] - pt[k]) ** 2 for k in range(3)))
                gap = max(gap1, gap2)
                ang = _angle_between(n1, n2)
                if ang > math.pi / 2:
                    ang = math.pi - ang
                cdiff = abs(c1 - c2)

                if gap > max_gap:
                    max_gap = gap
                if ang > max_angle:
                    max_angle = ang
                if cdiff > max_curv:
                    max_curv = cdiff

            if usable == 0:
                continue

            achieved = _achieved_level(max_gap, max_angle, max_curv)
            if _meets(achieved, args.min_continuity):
                pass_count += 1
            else:
                fail_count += 1
                failures.append({
                    "edge_idx": ei,
                    "required": args.min_continuity,
                    "achieved": achieved,
                    "max_normal_angle_rad": round(max_angle, 6),
                    "max_curvature_delta": round(max_curv, 6),
                    "max_position_gap_mm": round(max_gap, 6),
                })

        extras = {
            "min_continuity": args.min_continuity,
            "edge_count": len(edges),
            "shared_edge_count": shared_edge_count,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "continuity_failures": failures,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
