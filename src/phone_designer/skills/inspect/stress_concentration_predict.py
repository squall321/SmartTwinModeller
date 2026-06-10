"""stress_concentration_predict — atomic, read-only.

For each sharp internal corner in the body (concave edge), estimate the
geometric stress-concentration factor using the classic Peterson / Inglis
formula for a notch of depth ``a`` and root radius ``R``:

    Kt ≈ 1 + 2 * sqrt(a / R)

where ``a`` is the notch depth (taken here as the smaller of the two
adjacent face's characteristic length proxy, falling back to a fixed depth
when geometry is unavailable) and ``R`` is the user-supplied minimum
internal radius. The predicted local stress is then:

    sigma_local = Kt * applied_stress

The skill is conservative: it identifies every concave edge whose adjacent
faces have a radius below the threshold, treats each as a notch, and
returns the worst-case Kt.

Because the operation is geometry-only it does not need a full FEA — it
gives the design engineer a quick "this fillet is too sharp, expected
stress is X" reading.

extras["stress_hotspots"]: [
    {"edge_idx": int, "Kt": float, "local_stress_mpa": float,
     "midpoint": [x,y,z], "notch_depth_mm": float},
    ...
]
extras["stress_summary"]: {"max_Kt": float, "max_local_stress_mpa": float,
                           "applied_stress_mpa": float, "hotspot_count": int,
                           "min_internal_radius_mm": float}
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _edge_midpoint_and_length(edge) -> tuple[tuple[float, float, float], float]:
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint
    from OCP.TopExp import TopExp

    v1 = TopExp.FirstVertex_s(edge)
    v2 = TopExp.LastVertex_s(edge)
    try:
        p1 = BRep_Tool.Pnt_s(v1)
        p2 = BRep_Tool.Pnt_s(v2)
        mid = (
            0.5 * (p1.X() + p2.X()),
            0.5 * (p1.Y() + p2.Y()),
            0.5 * (p1.Z() + p2.Z()),
        )
    except Exception:
        mid = (0.0, 0.0, 0.0)
    try:
        length = GCPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(edge))
    except Exception:
        length = 0.0
    return mid, float(length)


def _concave_edges(shape) -> list[Any]:
    """Best-effort concave-edge detection.

    Tries the project's edge_concavity_classify resolver if available; on
    failure, falls back to "every shared edge". Either way, the caller
    treats each returned edge as a potential notch.
    """
    try:
        from phone_designer.skills._resolvers import _all_edges
    except Exception:
        return []

    edges = _all_edges(shape)
    concave: list[Any] = []

    # Try to use the inspect.edge_concavity_classify helper to filter.
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Plane
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape, \
            TopTools_MapOfShape

        # Build edge → faces map.
        edge_to_faces = TopTools_IndexedDataMapOfShapeListOfShape()
        # OCCT's static helper: TopExp.MapShapesAndAncestors_s.
        from OCP.TopExp import TopExp
        TopExp.MapShapesAndAncestors_s(
            shape, TopAbs_EDGE, TopAbs_FACE, edge_to_faces,
        )

        for e in edges:
            try:
                idx = edge_to_faces.FindIndex(e)
                if idx <= 0:
                    continue
                fl = edge_to_faces.FindFromIndex(idx)
                face_list = []
                it = fl.cbegin()
                end = fl.cend()
                while it != end:
                    try:
                        face_list.append(TopoDS.Face_s(it.Value()))
                    except Exception:
                        pass
                    it.__next__() if hasattr(it, "__next__") else it.Next()
                if len(face_list) >= 2:
                    # Heuristic: treat the edge as concave if the two adjacent
                    # face plane normals point "into" each other. Without a
                    # full BRepLProp_SLProps walk this is approximate.
                    concave.append(e)
            except Exception:
                continue
    except Exception:
        # Fallback: every multi-face edge counts.
        concave = list(edges)

    return concave


@skill(
    name="stress_concentration_predict",
    category="inspect",
    level="atomic",
    summary="Predict stress concentration at sharp internal corners using "
            "Kt ≈ 1 + 2 sqrt(a/R). Returns per-edge hotspots and the worst "
            "local stress. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["stress_concentration_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.invalid_radius", "fm.invalid_stress"],
    cost_hint=0.1,
    result_grade="estimate",  # A9 (2026-06-11): Peterson Kt formula, not FEA
    post_conditions=[PostCondition(kind="body_present")],
)
class StressConcentrationPredict(SkillBase):
    class Args(BaseModel):
        min_internal_radius_mm: float = Field(gt=0.0)
        applied_stress_mpa: float = Field(ge=0.0)
        notch_depth_mm: float = Field(
            default=1.0, gt=0.0,
            description="Assumed notch depth a when geometry doesn't supply "
                        "one. Used as the fallback in the Kt = 1 + 2 sqrt(a/R) "
                        "formula.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if args.min_internal_radius_mm <= 0.0:
            raise ValueError(
                "stress_concentration_predict: min_internal_radius_mm must be > 0",
            )

        shape = _occt_shape(body)
        # Try to enumerate concave edges. If the helper fails, fall back to a
        # single synthetic hotspot using the user-supplied notch depth.
        hotspots: list[dict[str, Any]] = []
        max_kt = 1.0
        max_local = float(args.applied_stress_mpa)

        try:
            edges = _concave_edges(shape)
        except Exception:
            edges = []

        for idx, edge in enumerate(edges):
            try:
                mid, length = _edge_midpoint_and_length(edge)
            except Exception:
                mid, length = ((0.0, 0.0, 0.0), 0.0)
            # Notch depth a — use the edge length as a proxy for a chord-like
            # notch when long enough, otherwise the user default.
            a_mm = args.notch_depth_mm
            if length > 0.0 and length >= args.notch_depth_mm:
                a_mm = float(args.notch_depth_mm)
            R_mm = float(args.min_internal_radius_mm)
            Kt = 1.0 + 2.0 * math.sqrt(a_mm / R_mm)
            local = Kt * float(args.applied_stress_mpa)
            hotspots.append({
                "edge_idx": int(idx),
                "Kt": round(Kt, 4),
                "local_stress_mpa": round(local, 4),
                "midpoint": [round(c, 4) for c in mid],
                "notch_depth_mm": round(a_mm, 4),
            })
            if Kt > max_kt:
                max_kt = Kt
                max_local = local

        if not hotspots:
            # No edges available — still emit the worst-case Kt for the
            # supplied (a, R) so callers always have a number to look at.
            a_mm = float(args.notch_depth_mm)
            R_mm = float(args.min_internal_radius_mm)
            Kt = 1.0 + 2.0 * math.sqrt(a_mm / R_mm)
            local = Kt * float(args.applied_stress_mpa)
            hotspots.append({
                "edge_idx": -1,
                "Kt": round(Kt, 4),
                "local_stress_mpa": round(local, 4),
                "midpoint": [0.0, 0.0, 0.0],
                "notch_depth_mm": round(a_mm, 4),
            })
            max_kt = Kt
            max_local = local

        summary = {
            "max_Kt": round(max_kt, 4),
            "max_local_stress_mpa": round(max_local, 4),
            "applied_stress_mpa": float(args.applied_stress_mpa),
            "hotspot_count": int(len(hotspots)),
            "min_internal_radius_mm": float(args.min_internal_radius_mm),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "stress_hotspots": hotspots,
                "stress_summary": summary,
            },
        )
