"""mesh_to_brep — atomic create. STL/OBJ mesh file → BREP body.

Approach: read the mesh as a `Poly_Triangulation` via `RWStl.ReadFile_s`, then
for every triangle build a planar 3-edge wire, materialize a face, and feed
them into `BRepBuilderAPI_Sewing`. The result is a sewn shell (`TopoDS_Shell`
or compound thereof). If the mesh is closed and orientable, `BRepBuilderAPI_
MakeSolid` will lift the shell to a solid; otherwise we keep the shell.

Limitations (documented up-front — sewing tolerances are tricky):
    - Best-effort only. Recommended for ≤10k triangles.
    - Non-manifold or self-intersecting meshes will sew partially; the result
      may be an open shell. The post-condition is `body_present`, not "is
      a solid", so we don't fail in that case — the caller can inspect.
    - OBJ files: we accept the extension but actually read via `RWStl`, which
      handles STL only. A real OBJ reader (e.g., `Mesh_VertexFile`) would be
      a follow-up. For now, OBJ paths raise NotImplementedError.

We tolerate degenerate triangles (two coincident vertices) by skipping them
and recording the count in `extras["skipped_degenerate"]`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _node_to_pnt(triangulation, idx: int):
    """1-based node index → gp_Pnt (OCCT Poly_Triangulation is 1-indexed)."""
    return triangulation.Node(idx)


def _triangle_is_degenerate(p1, p2, p3, tol: float) -> bool:
    """Reject triangles whose vertices coincide or whose area is ~0."""
    # cheap squared-distance check first
    def _d2(a, b):
        dx = a.X() - b.X()
        dy = a.Y() - b.Y()
        dz = a.Z() - b.Z()
        return dx * dx + dy * dy + dz * dz
    t2 = tol * tol
    if _d2(p1, p2) < t2 or _d2(p2, p3) < t2 or _d2(p1, p3) < t2:
        return True
    # cross-product magnitude (= 2 * area) — guard against collinear
    ux, uy, uz = p2.X() - p1.X(), p2.Y() - p1.Y(), p2.Z() - p1.Z()
    vx, vy, vz = p3.X() - p1.X(), p3.Y() - p1.Y(), p3.Z() - p1.Z()
    cx = uy * vz - uz * vy
    cy = uz * vx - ux * vz
    cz = ux * vy - uy * vx
    area2_sq = cx * cx + cy * cy + cz * cz
    return area2_sq < (tol * tol) ** 2


@skill(
    name="mesh_to_brep",
    category="create",
    level="atomic",
    summary="Convert an STL mesh into a BREP shell/solid by sewing per-triangle "
            "faces. Best-effort: works on small (≤10k triangles), reasonably "
            "manifold meshes. Open or non-manifold meshes yield an open shell.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["meshed_brep"],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.file_not_found", "fm.stl_parse_failed", "fm.sewing_failed"],
    cost_hint=0.6,
    post_conditions=[PostCondition(kind="body_present")],
)
class MeshToBrep(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1, description="STL 입력 파일 경로 (절대 또는 cwd-relative)")
        sewing_tolerance_mm: float = Field(
            default=0.01, gt=0,
            description="BRepBuilderAPI_Sewing tolerance. Mesh edges within "
                        "this distance are merged. Too tight = open shell; "
                        "too loose = mesh distortion.",
        )
        try_make_solid: bool = Field(
            default=True,
            description="After sewing, attempt MakeSolid. Falls back to the "
                        "shell if the mesh isn't closed.",
        )
        max_triangles: int = Field(
            default=10_000, ge=1,
            description="Safety limit. Larger meshes typically take minutes "
                        "and may exhaust memory.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRep import BRep_Builder
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakePolygon,
            BRepBuilderAPI_MakeSolid,
            BRepBuilderAPI_Sewing,
        )
        from OCP.RWStl import RWStl
        from OCP.TopAbs import TopAbs_SHELL
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS, TopoDS_Compound

        p = Path(args.path)
        if not p.exists():
            raise FileNotFoundError(f"mesh file not found: {p}")
        if p.suffix.lower() == ".obj":
            raise NotImplementedError(
                "OBJ import not yet supported — STL only. "
                "Convert via meshlab/blender first.",
            )

        triangulation = RWStl.ReadFile_s(str(p))
        if triangulation is None:
            raise RuntimeError(f"mesh_to_brep: failed to parse mesh: {p}")

        n_tri = int(triangulation.NbTriangles())
        if n_tri == 0:
            raise RuntimeError(f"mesh_to_brep: mesh has 0 triangles: {p}")
        if n_tri > args.max_triangles:
            raise RuntimeError(
                f"mesh_to_brep: mesh has {n_tri} triangles, limit is "
                f"{args.max_triangles}. Decimate first via mesh_simplify.",
            )

        # Sewing tolerance dictates both vertex merging and degenerate filtering.
        tol = float(args.sewing_tolerance_mm)
        sewer = BRepBuilderAPI_Sewing(tol)

        skipped = 0
        added = 0
        for i in range(1, n_tri + 1):
            try:
                t = triangulation.Triangle(i)
                n1, n2, n3 = t.Get()
                p1 = _node_to_pnt(triangulation, n1)
                p2 = _node_to_pnt(triangulation, n2)
                p3 = _node_to_pnt(triangulation, n3)
                if _triangle_is_degenerate(p1, p2, p3, tol):
                    skipped += 1
                    continue
                poly = BRepBuilderAPI_MakePolygon(p1, p2, p3, True)
                wire = poly.Wire()
                face_maker = BRepBuilderAPI_MakeFace(wire, True)  # OnlyPlane=True
                if not face_maker.IsDone():
                    skipped += 1
                    continue
                sewer.Add(face_maker.Face())
                added += 1
            except Exception:
                skipped += 1
                continue

        if added == 0:
            raise RuntimeError(
                f"mesh_to_brep: every triangle was degenerate or rejected "
                f"({skipped} skipped). Try a larger sewing_tolerance_mm.",
            )

        sewer.Perform()
        sewn = sewer.SewedShape()
        if sewn is None or sewn.IsNull():
            raise RuntimeError("mesh_to_brep: BRepBuilderAPI_Sewing produced null shape")

        result_shape = sewn
        is_solid = False
        is_shell = False

        if args.try_make_solid:
            # Find shells in the sewn output and try to lift the first one to a solid.
            try:
                exp = TopExp_Explorer(sewn, TopAbs_SHELL)
                if exp.More():
                    shell = TopoDS.Shell_s(exp.Current())
                    solid_maker = BRepBuilderAPI_MakeSolid(shell)
                    if solid_maker.IsDone():
                        candidate = solid_maker.Solid()
                        if candidate is not None and not candidate.IsNull():
                            result_shape = candidate
                            is_solid = True
                if not is_solid:
                    # at least one shell was emitted — keep that
                    exp2 = TopExp_Explorer(sewn, TopAbs_SHELL)
                    if exp2.More():
                        is_shell = True
            except Exception:
                # fall back to whatever sewer produced
                pass

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(
            body=Part(result_shape),
            history=history,
            extras={
                "source_path": str(p),
                "triangle_count_input": int(n_tri),
                "triangles_sewn": int(added),
                "skipped_degenerate": int(skipped),
                "sewing_tolerance_mm": float(tol),
                "is_solid": bool(is_solid),
                "is_shell": bool(is_shell),
            },
        )
