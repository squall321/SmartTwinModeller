"""mesh_export — atomic inspect. body → OBJ or PLY neutral mesh file.

Writes the current body to one of the two most common neutral triangle-mesh
formats (OBJ or PLY) that Blender / MeshLab / 3D scanners consume. OCCT 7.8
ships native CAF writers (``RWObj_CafWriter`` / ``RWPly_CafWriter``) but those
require building an XCAF document. This skill takes the simpler, robust,
dependency-free path:

  1. tessellate the body in-place with ``BRepMesh_IncrementalMesh``
     (same idiom as ``stl_export`` / ``brep_to_mesh``),
  2. walk every face's ``Poly_Triangulation`` (``BRep_Tool.Triangulation_s``),
     transforming each node by the face's ``TopLoc_Location`` so the vertices
     are in world coordinates,
  3. emit ASCII OBJ (``v x y z`` / ``f i j k``, 1-indexed) or ASCII PLY
     (``ply`` header + vertex list + ``3 i j k`` face list, 0-indexed) with
     plain Python file writes — fully deterministic.

Face orientation is respected: a ``TopAbs_REVERSED`` face has its triangle
winding flipped so exported normals point outward, matching STL/OCCT
convention.

The body is returned UNCHANGED — this skill is read-only as far as the model
is concerned; the only side effect is the filesystem write. Both the written
path and mesh stats land on ``result.extras``.

extras schema:
    {
      "written_path": str,          # the file we wrote
      "format": "obj" | "ply",
      "vertex_count": int,          # total mesh nodes written
      "triangle_count": int,        # total triangles written
      "file_size_bytes": int,
      "linear_deflection_mm": float,
      "angular_deflection_deg": float,
    }
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _infer_format(path: str, explicit: str | None) -> str:
    """Resolve the target format from an explicit arg or the file extension."""
    if explicit:
        fmt = explicit.strip().lower()
        if fmt not in ("obj", "ply"):
            raise ValueError(f"mesh_export: unsupported format {explicit!r} "
                             "(expected 'obj' or 'ply')")
        return fmt
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in ("obj", "ply"):
        return ext
    raise ValueError(
        f"mesh_export: cannot infer format from extension {ext!r}; "
        "pass format='obj' or format='ply'"
    )


def _collect_mesh(shape) -> tuple[list[tuple[float, float, float]],
                                  list[tuple[int, int, int]]]:
    """Walk every face triangulation → (world-space vertices, 0-based triangles).

    Node coordinates from ``Poly_Triangulation.Node`` live in the face's local
    frame; each is transformed by the face ``TopLoc_Location`` before being
    written so the mesh is in world coordinates. Reversed faces have their
    winding flipped so exported normals stay outward-facing.
    """
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []

    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        try:
            face = TopoDS.Face_s(it.Current())
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)
            if tri is not None:
                trsf = loc.Transformation()
                reversed_ = face.Orientation() == TopAbs_REVERSED
                # base index into the global vertex list for this face's nodes
                base = len(vertices)
                nb_nodes = int(tri.NbNodes())
                for n in range(1, nb_nodes + 1):
                    p = tri.Node(n)               # gp_Pnt in local frame
                    p = p.Transformed(trsf)       # → world frame
                    vertices.append((p.X(), p.Y(), p.Z()))
                nb_tri = int(tri.NbTriangles())
                for i in range(1, nb_tri + 1):
                    t = tri.Triangle(i)
                    a, b, c = t.Get()             # 1-based node indices
                    if reversed_:
                        b, c = c, b               # flip winding
                    # map local 1-based node id → global 0-based vertex id
                    triangles.append(
                        (base + a - 1, base + b - 1, base + c - 1)
                    )
        except Exception:
            pass
        it.Next()

    return vertices, triangles


def _write_obj(path: Path,
               vertices: list[tuple[float, float, float]],
               triangles: list[tuple[int, int, int]]) -> None:
    lines: list[str] = ["# mesh_export OBJ", f"# {len(vertices)} vertices "
                        f"{len(triangles)} faces"]
    for (x, y, z) in vertices:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for (a, b, c) in triangles:
        # OBJ face indices are 1-based
        lines.append(f"f {a + 1} {b + 1} {c + 1}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _write_ply(path: Path,
               vertices: list[tuple[float, float, float]],
               triangles: list[tuple[int, int, int]]) -> None:
    header = [
        "ply",
        "format ascii 1.0",
        "comment mesh_export",
        f"element vertex {len(vertices)}",
        "property float x",
        "property float y",
        "property float z",
        f"element face {len(triangles)}",
        "property list uchar int vertex_indices",
        "end_header",
    ]
    lines = list(header)
    for (x, y, z) in vertices:
        lines.append(f"{x:.6f} {y:.6f} {z:.6f}")
    for (a, b, c) in triangles:
        # PLY indices are 0-based
        lines.append(f"3 {a} {b} {c}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


@skill(
    name="mesh_export",
    category="io",
    level="atomic",
    summary="Tessellate the body and write it to a neutral triangle-mesh file "
            "in OBJ or PLY format (Blender / MeshLab / scanner friendly). "
            "Format is taken from the `format` arg or inferred from the file "
            "extension. Read-only — body is returned unchanged; only the "
            "filesystem is touched.",
    selector_kinds=[],
    history_rules={},
    produces_features=["mesh_artifact"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.tessellation_failed", "fm.mesh_write_failed"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class MeshExport(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1,
                          description="출력 경로 (.obj 또는 .ply)")
        format: Literal["obj", "ply"] | None = Field(
            default=None,
            description="'obj' 또는 'ply'. 생략하면 path 확장자에서 추론.",
        )
        linear_deflection_mm: float = Field(
            default=0.1, gt=0,
            description="Max chord deviation in mm — smaller = finer mesh.",
        )
        angular_deflection_deg: float = Field(
            default=5.0, gt=0,
            description="Per-face angular deflection limit in degrees.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepMesh import BRepMesh_IncrementalMesh

        if body is None:
            raise ValueError("mesh_export: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        if shape is None:
            raise ValueError("mesh_export: body.wrapped is None")

        fmt = _infer_format(args.path, args.format)

        ang_rad = math.radians(float(args.angular_deflection_deg))
        mesher = BRepMesh_IncrementalMesh(
            shape,
            float(args.linear_deflection_mm),
            False,          # relative
            ang_rad,
            True,           # parallel
        )
        mesher.Perform()
        if not mesher.IsDone():
            raise RuntimeError("mesh_export: BRepMesh_IncrementalMesh failed")

        vertices, triangles = _collect_mesh(shape)
        if not triangles:
            raise RuntimeError(
                "mesh_export: no triangles produced — tessellation empty"
            )

        out_path = Path(args.path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if fmt == "obj":
            _write_obj(out_path, vertices, triangles)
        else:
            _write_ply(out_path, vertices, triangles)

        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError(f"mesh_export: write produced no file → {out_path}")

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "written_path": str(out_path),
                "format": fmt,
                "vertex_count": int(len(vertices)),
                "triangle_count": int(len(triangles)),
                "file_size_bytes": int(out_path.stat().st_size),
                "linear_deflection_mm": float(args.linear_deflection_mm),
                "angular_deflection_deg": float(args.angular_deflection_deg),
            },
        )
