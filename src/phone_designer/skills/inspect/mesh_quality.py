"""mesh_quality — atomic, read-only inspect.

Tessellate the body (or use the current tessellation if `force_remesh=False`
and one exists) and report mesh-quality metrics:

    extras["mesh_quality_report"] = {
        "triangle_count": int,
        "vertex_count": int,
        "degenerate_triangle_count": int,   # collinear / zero-area triangles
        "non_manifold_edge_count": int,     # edges shared by != 2 triangles
        "boundary_edge_count": int,         # edges used by exactly 1 triangle
        "max_edge_length_mm": float,
        "min_edge_length_mm": float,
        "min_triangle_angle_deg": float,
        "max_triangle_angle_deg": float,
        "min_triangle_area_mm2": float,
        "max_triangle_area_mm2": float,
    }

Used by planners to decide when a body needs remeshing (e.g., before STL
export to a printer with a min feature size), and as the input-validation
gate for `mesh_to_brep` (high non-manifold counts → sewing will fail).

Body is returned unchanged.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _iter_face_meshes(shape):
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        try:
            face = TopoDS.Face_s(it.Current())
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)
            if tri is not None and tri.NbTriangles() > 0:
                yield tri
        except Exception:
            pass
        it.Next()


def _triangle_metrics(p1, p2, p3) -> tuple[float, float, float, float, float]:
    """Return (area, min_edge, max_edge, min_angle_deg, max_angle_deg).

    Degenerate triangles return area=0; angles default to 0/180.
    """
    def _len(a, b):
        return math.sqrt(
            (a.X() - b.X()) ** 2 + (a.Y() - b.Y()) ** 2 + (a.Z() - b.Z()) ** 2,
        )
    a = _len(p2, p3)
    b = _len(p1, p3)
    c = _len(p1, p2)
    edges = (a, b, c)
    min_e = min(edges)
    max_e = max(edges)

    # Heron's formula for area
    s = 0.5 * (a + b + c)
    area_sq = s * (s - a) * (s - b) * (s - c)
    area = math.sqrt(max(area_sq, 0.0))

    # Angles via law of cosines. Guard against degenerate edges.
    def _angle_opposite(side, e1, e2):
        if e1 < 1e-12 or e2 < 1e-12:
            return 0.0
        cos_v = (e1 * e1 + e2 * e2 - side * side) / (2.0 * e1 * e2)
        cos_v = max(-1.0, min(1.0, cos_v))
        return math.degrees(math.acos(cos_v))

    ang_a = _angle_opposite(a, b, c)
    ang_b = _angle_opposite(b, a, c)
    ang_c = _angle_opposite(c, a, b)
    angles = (ang_a, ang_b, ang_c)
    return area, min_e, max_e, min(angles), max(angles)


def _build_report(shape, *, degenerate_area_tol: float) -> dict[str, Any]:
    triangle_count = 0
    vertex_count = 0
    degenerate = 0

    min_edge = math.inf
    max_edge = 0.0
    min_angle = math.inf
    max_angle = 0.0
    min_area = math.inf
    max_area = 0.0

    # edge -> count of incident triangles, for non-manifold detection
    edge_uses: dict[tuple[int, int, int, int, int, int], int] = {}

    def _quantize(p, scale=1e6) -> tuple[int, int, int]:
        # 1 µm quantization — robust to floating-point jitter at face seams.
        return (
            int(round(p.X() * scale)),
            int(round(p.Y() * scale)),
            int(round(p.Z() * scale)),
        )

    for tri in _iter_face_meshes(shape):
        nb_tri = int(tri.NbTriangles())
        vertex_count += int(tri.NbNodes())
        triangle_count += nb_tri
        for i in range(1, nb_tri + 1):
            t = tri.Triangle(i)
            a, b, c = t.Get()
            p1 = tri.Node(a)
            p2 = tri.Node(b)
            p3 = tri.Node(c)
            area, min_e, max_e, min_a, max_a = _triangle_metrics(p1, p2, p3)
            if area <= degenerate_area_tol:
                degenerate += 1
                continue
            if min_e < min_edge: min_edge = min_e
            if max_e > max_edge: max_edge = max_e
            if min_a < min_angle: min_angle = min_a
            if max_a > max_angle: max_angle = max_a
            if area < min_area: min_area = area
            if area > max_area: max_area = area

            # accumulate edge use counts (quantized endpoints for cross-face matching)
            q1 = _quantize(p1)
            q2 = _quantize(p2)
            q3 = _quantize(p3)
            for ea, eb in ((q1, q2), (q2, q3), (q3, q1)):
                if ea < eb:
                    key = (*ea, *eb)
                else:
                    key = (*eb, *ea)
                edge_uses[key] = edge_uses.get(key, 0) + 1

    boundary_edges = sum(1 for v in edge_uses.values() if v == 1)
    non_manifold_edges = sum(1 for v in edge_uses.values() if v > 2)

    if not math.isfinite(min_edge):
        min_edge = 0.0
    if not math.isfinite(min_angle):
        min_angle = 0.0
    if not math.isfinite(min_area):
        min_area = 0.0

    return {
        "triangle_count": int(triangle_count),
        "vertex_count": int(vertex_count),
        "degenerate_triangle_count": int(degenerate),
        "non_manifold_edge_count": int(non_manifold_edges),
        "boundary_edge_count": int(boundary_edges),
        "max_edge_length_mm": round(max_edge, 4),
        "min_edge_length_mm": round(min_edge, 6),
        "min_triangle_angle_deg": round(min_angle, 4),
        "max_triangle_angle_deg": round(max_angle, 4),
        "min_triangle_area_mm2": round(min_area, 6),
        "max_triangle_area_mm2": round(max_area, 4),
    }


@skill(
    name="mesh_quality",
    category="inspect",
    level="atomic",
    summary="Tessellate (if needed) and report mesh-quality metrics: non-manifold "
            "edge count, degenerate triangle count, edge-length and angle "
            "extrema, boundary edge count. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["mesh_quality_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.tessellation_failed"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class MeshQuality(SkillBase):
    class Args(BaseModel):
        linear_deflection_mm: float = Field(
            default=0.1, gt=0,
            description="Used only when force_remesh=True or no existing mesh.",
        )
        angular_deflection_deg: float = Field(default=5.0, gt=0)
        force_remesh: bool = Field(
            default=False,
            description="If True, always re-tessellate even if a mesh exists.",
        )
        degenerate_area_tol_mm2: float = Field(
            default=1e-6, ge=0,
            description="Triangles with area below this are flagged degenerate.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepMesh import BRepMesh_IncrementalMesh

        if body is None:
            raise ValueError("mesh_quality: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        if shape is None:
            raise ValueError("mesh_quality: body.wrapped is None")

        # Check if any face already has a triangulation; remesh otherwise or
        # when the caller asks.
        needs_mesh = args.force_remesh
        if not needs_mesh:
            has_any = False
            for _tri in _iter_face_meshes(shape):
                has_any = True
                break
            needs_mesh = not has_any

        if needs_mesh:
            ang_rad = math.radians(float(args.angular_deflection_deg))
            mesher = BRepMesh_IncrementalMesh(
                shape,
                float(args.linear_deflection_mm),
                False,
                ang_rad,
                True,
            )
            mesher.Perform()
            if not mesher.IsDone():
                raise RuntimeError("mesh_quality: BRepMesh_IncrementalMesh failed")

        report = _build_report(
            shape,
            degenerate_area_tol=float(args.degenerate_area_tol_mm2),
        )

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"mesh_quality_report": report},
        )
