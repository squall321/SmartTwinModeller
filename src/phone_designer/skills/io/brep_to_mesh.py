"""brep_to_mesh — atomic inspect. body → STL file + mesh stats.

Re-export of the current body as STL via `BRepMesh_IncrementalMesh` +
`StlAPI_Writer`. Distinct from `stl_export` in that it produces a richer
mesh-stats dict on `result.extras["mesh_stats"]` (triangle count, vertex
count, surface area estimate, plus the deflection settings used). Body is
returned unchanged.

This is the dual of `mesh_to_brep`: pipelines that need to round-trip a
solid through STL (e.g., 3D-print preview followed by reimport) use these
two skills together.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _iter_triangulations(shape):
    """Yield (face, Poly_Triangulation, location) for every face that has a mesh."""
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
            if tri is not None:
                yield face, tri, loc
        except Exception:
            pass
        it.Next()


def _mesh_stats(shape) -> dict[str, Any]:
    """Count triangles + unique-ish vertices + sum approximate triangle area."""
    triangle_count = 0
    vertex_count = 0
    area_total = 0.0
    for _face, tri, _loc in _iter_triangulations(shape):
        nb_tri = int(tri.NbTriangles())
        nb_nodes = int(tri.NbNodes())
        triangle_count += nb_tri
        vertex_count += nb_nodes
        # accumulate area using each triangle's cross-product magnitude / 2
        try:
            for i in range(1, nb_tri + 1):
                t = tri.Triangle(i)
                n1, n2, n3 = t.Get()
                p1 = tri.Node(n1)
                p2 = tri.Node(n2)
                p3 = tri.Node(n3)
                ux, uy, uz = p2.X() - p1.X(), p2.Y() - p1.Y(), p2.Z() - p1.Z()
                vx, vy, vz = p3.X() - p1.X(), p3.Y() - p1.Y(), p3.Z() - p1.Z()
                cx = uy * vz - uz * vy
                cy = uz * vx - ux * vz
                cz = ux * vy - uy * vx
                area_total += 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)
        except Exception:
            pass
    return {
        "triangle_count": int(triangle_count),
        "vertex_count": int(vertex_count),
        "surface_area_mm2": round(area_total, 4),
    }


@skill(
    name="brep_to_mesh",
    category="inspect",
    level="atomic",
    summary="Tessellate body and write a binary STL, then attach mesh stats "
            "(triangle count, vertex count, surface area) to "
            "extras['mesh_stats']. Body is returned unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["stl_artifact", "mesh_stats"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.tessellation_failed", "fm.stl_write_failed"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class BrepToMesh(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1, description="출력 STL 경로")
        linear_deflection_mm: float = Field(
            default=0.1, gt=0,
            description="Max chord deviation — smaller = finer mesh.",
        )
        angular_deflection_deg: float = Field(
            default=5.0, gt=0,
            description="Per-face angular deflection limit in degrees.",
        )
        relative: bool = Field(
            default=False,
            description="If True, linear deflection is relative to face size.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.StlAPI import StlAPI_Writer

        if body is None:
            raise ValueError("brep_to_mesh: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        if shape is None:
            raise ValueError("brep_to_mesh: body.wrapped is None")

        ang_rad = math.radians(float(args.angular_deflection_deg))
        mesher = BRepMesh_IncrementalMesh(
            shape,
            float(args.linear_deflection_mm),
            bool(args.relative),
            ang_rad,
            True,  # parallel
        )
        mesher.Perform()
        if not mesher.IsDone():
            raise RuntimeError("brep_to_mesh: BRepMesh_IncrementalMesh failed")

        out_path = Path(args.path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        writer = StlAPI_Writer()
        ok = writer.Write(shape, str(out_path))
        if not ok or not out_path.exists():
            raise RuntimeError(f"brep_to_mesh: StlAPI_Writer.Write failed → {out_path}")

        stats = _mesh_stats(shape)
        stats["mesh_path"] = str(out_path)
        stats["linear_deflection_mm"] = float(args.linear_deflection_mm)
        stats["angular_deflection_deg"] = float(args.angular_deflection_deg)

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"mesh_stats": stats},
        )
