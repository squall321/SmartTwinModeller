"""section_multi_plane — atomic, read-only.

Like ``cross_section`` but takes a *list* of planes and returns one section
result per plane (BRepAlgoAPI_Section). Useful for LLM agent 가 한 번에 여러
sectional view 를 만들거나 axial array 를 검사할 때.

각 plane dict 는 ``{"origin": (x,y,z), "normal": (x,y,z)}`` 형식. plane 마다
독립적으로 intersection 을 계산하고 polylines 와 total length 를 보고한다.

extras schema:
    {"plane_count": int,
     "sections": [
        {"index": i,
         "plane_origin": [x,y,z], "plane_normal": [x,y,z],
         "polylines": [[(x,y,z), ...], ...],
         "edge_count": int,
         "total_length_mm": float,
         "error": str | None},
        ...
     ]}

body 는 변경하지 않는다 (post ``body_present``).
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
    """Uniform arc-length sampling of an edge → (polyline, length).

    Mirrors ``cross_section._sample_edge`` (kept local to avoid coupling).
    """
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

    u_first = curve.FirstParameter()
    u_last = curve.LastParameter()
    if u_last <= u_first:
        return [], length
    for i in range(n):
        u = u_first + (u_last - u_first) * (i / (n - 1))
        p = curve.Value(u)
        pts.append((p.X(), p.Y(), p.Z()))
    return pts, length


def _section_one_plane(
    shape, origin_xyz: tuple[float, float, float],
    normal_xyz: tuple[float, float, float],
    samples_per_edge: int,
) -> dict[str, Any]:
    """Compute one plane's section against ``shape``."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

    nx, ny, nz = normal_xyz
    if (nx * nx + ny * ny + nz * nz) < 1e-18:
        return {
            "plane_origin": [round(c, 4) for c in origin_xyz],
            "plane_normal": [round(c, 4) for c in normal_xyz],
            "polylines": [],
            "edge_count": 0,
            "total_length_mm": 0.0,
            "error": "plane_normal must be non-zero",
        }

    try:
        origin = gp_Pnt(*origin_xyz)
        normal = gp_Dir(nx, ny, nz)
        plane = gp_Pln(origin, normal)

        section = BRepAlgoAPI_Section(shape, plane)
        section.ComputePCurveOn1(False)
        section.Approximation(False)
        section.Build()
        if not section.IsDone():
            return {
                "plane_origin": [round(c, 4) for c in origin_xyz],
                "plane_normal": [round(c, 4) for c in normal_xyz],
                "polylines": [],
                "edge_count": 0,
                "total_length_mm": 0.0,
                "error": None,
            }
        section_shape = section.Shape()
    except Exception as ex:
        return {
            "plane_origin": [round(c, 4) for c in origin_xyz],
            "plane_normal": [round(c, 4) for c in normal_xyz],
            "polylines": [],
            "edge_count": 0,
            "total_length_mm": 0.0,
            "error": f"{type(ex).__name__}: {ex}",
        }

    from phone_designer.skills._resolvers import _all_edges
    edges = _all_edges(section_shape)

    polylines: list[list[tuple[float, float, float]]] = []
    total_length = 0.0
    for e in edges:
        try:
            pts, L = _sample_edge(e, samples_per_edge)
        except Exception:
            pts, L = [], 0.0
        if pts:
            polylines.append([
                (round(p[0], 4), round(p[1], 4), round(p[2], 4)) for p in pts
            ])
        total_length += L

    return {
        "plane_origin": [round(c, 4) for c in origin_xyz],
        "plane_normal": [round(c, 4) for c in normal_xyz],
        "polylines": polylines,
        "edge_count": len(polylines),
        "total_length_mm": round(total_length, 4),
        "error": None,
    }


@skill(
    name="section_multi_plane",
    category="inspect",
    level="atomic",
    summary="Compute intersection curves of the body with multiple planes "
            "(one BRepAlgoAPI_Section per plane). Returns sampled polylines "
            "per plane. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["multi_section_polylines"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class SectionMultiPlane(SkillBase):
    class Args(BaseModel):
        planes: list[dict] = Field(default_factory=list)
        samples_per_edge: int = Field(default=20, ge=2, le=500)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        shape = _occt_shape(body)

        sections: list[dict[str, Any]] = []
        for i, pl in enumerate(args.planes):
            origin = tuple(pl.get("origin", (0.0, 0.0, 0.0)))
            normal = tuple(pl.get("normal", (0.0, 0.0, 1.0)))
            # Pad / coerce length 3
            if len(origin) != 3:
                origin = (0.0, 0.0, 0.0)
            if len(normal) != 3:
                normal = (0.0, 0.0, 1.0)
            entry = _section_one_plane(
                shape,
                (float(origin[0]), float(origin[1]), float(origin[2])),
                (float(normal[0]), float(normal[1]), float(normal[2])),
                args.samples_per_edge,
            )
            entry["index"] = i
            sections.append(entry)

        extras = {
            "plane_count": len(sections),
            "sections": sections,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
