"""section_at_centroid — atomic, read-only.

Automatic section through the body's centroid along a principal axis (X/Y/Z
or the longest bounding box axis when ``axis="auto"``). Equivalent to
manually calling ``cross_section`` with ``plane_origin = centroid`` and
``plane_normal = ±axis``, but the LLM doesn't have to compute the centroid
first.

Useful for previewing internal structure (e.g., a thin-wall cavity, a
hidden boss) without picking a plane by hand.

Args:
    axis: "X" | "Y" | "Z" | "auto"   (default "auto" — longest bbox dim)
    samples_per_edge: same as cross_section (default 20)

extras schema:
    {
      "axis": "X" | "Y" | "Z",
      "centroid": [x,y,z],
      "plane_origin": [x,y,z],
      "plane_normal": [x,y,z],
      "polylines": [[(x,y,z), ...], ...],
      "edge_count": int,
      "total_length_mm": float,
      "bbox": {"min": [x,y,z], "max": [x,y,z]}
    }

body unchanged (post ``body_present``).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _body_centroid(shape) -> tuple[float, float, float]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    try:
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        c = props.CentreOfMass()
        return (c.X(), c.Y(), c.Z())
    except Exception:
        return (0.0, 0.0, 0.0)


def _body_bbox(shape) -> tuple[tuple[float, float, float],
                                 tuple[float, float, float]]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        try:
            BRepBndLib.Add_s(shape, bb)
        except Exception:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    if bb.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def _principal_axis_auto(bbox_min, bbox_max) -> str:
    """Return 'X'/'Y'/'Z' for the axis with the longest extent."""
    dx = bbox_max[0] - bbox_min[0]
    dy = bbox_max[1] - bbox_min[1]
    dz = bbox_max[2] - bbox_min[2]
    if dx >= dy and dx >= dz:
        return "X"
    if dy >= dz:
        return "Y"
    return "Z"


def _sample_edge(edge, samples: int) -> tuple[list[tuple[float, float, float]], float]:
    """Uniform arc-length sampling of an edge → (polyline, length).

    Mirrors cross_section._sample_edge; kept local to avoid cross-skill import.
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


@skill(
    name="section_at_centroid",
    category="inspect",
    level="atomic",
    summary="Compute a section through the body's centroid along X/Y/Z "
            "(default: longest bounding-box axis). Returns sampled polylines "
            "per intersection edge. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["centroid_section_polylines"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class SectionAtCentroid(SkillBase):
    class Args(BaseModel):
        axis: Literal["X", "Y", "Z", "auto"] = "auto"
        samples_per_edge: int = Field(default=20, ge=2, le=500)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

        shape = _occt_shape(body)

        centroid = _body_centroid(shape)
        bbox_min, bbox_max = _body_bbox(shape)

        axis = args.axis
        if axis == "auto":
            axis = _principal_axis_auto(bbox_min, bbox_max)

        normal_xyz: tuple[float, float, float]
        if axis == "X":
            normal_xyz = (1.0, 0.0, 0.0)
        elif axis == "Y":
            normal_xyz = (0.0, 1.0, 0.0)
        else:
            normal_xyz = (0.0, 0.0, 1.0)

        polylines: list[list[tuple[float, float, float]]] = []
        total_length = 0.0

        try:
            origin = gp_Pnt(*centroid)
            normal = gp_Dir(*normal_xyz)
            plane = gp_Pln(origin, normal)
            section = BRepAlgoAPI_Section(shape, plane)
            section.ComputePCurveOn1(False)
            section.Approximation(False)
            section.Build()
            if section.IsDone():
                section_shape = section.Shape()
                from phone_designer.skills._resolvers import _all_edges
                edges = _all_edges(section_shape)
                for e in edges:
                    try:
                        pts, L = _sample_edge(e, args.samples_per_edge)
                    except Exception:
                        pts, L = [], 0.0
                    if pts:
                        polylines.append([
                            (round(p[0], 4), round(p[1], 4), round(p[2], 4))
                            for p in pts
                        ])
                    total_length += L
        except Exception:
            pass

        extras = {
            "axis": axis,
            "centroid": [round(c, 6) for c in centroid],
            "plane_origin": [round(c, 6) for c in centroid],
            "plane_normal": [round(c, 6) for c in normal_xyz],
            "polylines": polylines,
            "edge_count": len(polylines),
            "total_length_mm": round(total_length, 4),
            "bbox": {
                "min": [round(c, 6) for c in bbox_min],
                "max": [round(c, 6) for c in bbox_max],
            },
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
