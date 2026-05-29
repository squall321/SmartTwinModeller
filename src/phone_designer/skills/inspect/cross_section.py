"""cross_section — atomic, read-only.

body 와 평면의 교차 곡선 (BRepAlgoAPI_Section). 각 edge 를 균일 간격으로 N 점
샘플해 polylines 3D list 로 반환한다.

result.extras schema:
    {"plane_origin": [x,y,z],
     "plane_normal": [x,y,z],
     "polylines": [[(x,y,z), (x,y,z), ...], ...],   # 각 edge 가 polyline
     "edge_count": int,
     "total_length_mm": float}
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _sample_edge(edge, samples: int) -> tuple[list[tuple[float, float, float]], float]:
    """edge 를 균일 간격 samples 점으로 샘플. (polyline, length) 반환."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint, GCPnts_UniformAbscissa

    curve = BRepAdaptor_Curve(edge)
    length = float(GCPnts_AbscissaPoint.Length_s(curve))
    n = max(2, int(samples))

    pts: list[tuple[float, float, float]] = []
    try:
        algo = GCPnts_UniformAbscissa(curve, n)
        if algo.IsDone() and algo.NbPoints() >= 2:
            for i in range(1, algo.NbPoints() + 1):
                u = algo.Parameter(i)
                p = curve.Value(u)
                pts.append((p.X(), p.Y(), p.Z()))
            return pts, length
    except Exception:
        pts = []

    # Fallback: uniform parameter sampling
    u_first = curve.FirstParameter()
    u_last = curve.LastParameter()
    if u_last <= u_first:
        return [], length
    for i in range(n):
        u = u_first + (u_last - u_first) * (i / (n - 1))
        p = curve.Value(u)
        pts.append((p.X(), p.Y(), p.Z()))
    return pts, length


@skill(
    name="cross_section",
    category="inspect",
    level="atomic",
    summary="Compute the intersection curve of the body with a plane "
            "(BRepAlgoAPI_Section). Returns sampled polylines per edge. "
            "Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["cross_section_polylines"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class CrossSection(SkillBase):
    class Args(BaseModel):
        plane_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        plane_normal: tuple[float, float, float] = (0.0, 0.0, 1.0)
        samples_per_edge: int = Field(default=20, ge=2, le=500)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

        shape = _occt_shape(body)

        origin = gp_Pnt(*args.plane_origin)
        # gp_Dir requires non-zero normal — guard.
        nx, ny, nz = args.plane_normal
        if (nx * nx + ny * ny + nz * nz) < 1e-18:
            raise ValueError("plane_normal must be non-zero")
        normal = gp_Dir(nx, ny, nz)
        plane = gp_Pln(origin, normal)

        section = BRepAlgoAPI_Section(shape, plane)
        section.ComputePCurveOn1(False)
        section.Approximation(False)
        section.Build()
        if not section.IsDone():
            # Empty intersection — return empty polylines, not crash.
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={
                    "plane_origin": list(args.plane_origin),
                    "plane_normal": list(args.plane_normal),
                    "polylines": [],
                    "edge_count": 0,
                    "total_length_mm": 0.0,
                },
            )

        section_shape = section.Shape()

        # Enumerate the section's edges.
        from phone_designer.skills._resolvers import _all_edges

        edges = _all_edges(section_shape)

        polylines: list[list[tuple[float, float, float]]] = []
        total_length = 0.0
        for e in edges:
            try:
                pts, L = _sample_edge(e, args.samples_per_edge)
            except Exception:
                pts, L = [], 0.0
            if pts:
                polylines.append([(round(p[0], 4), round(p[1], 4), round(p[2], 4))
                                  for p in pts])
            total_length += L

        extras = {
            "plane_origin": [round(c, 4) for c in args.plane_origin],
            "plane_normal": [round(c, 4) for c in args.plane_normal],
            "polylines": polylines,
            "edge_count": len(polylines),
            "total_length_mm": round(total_length, 4),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
