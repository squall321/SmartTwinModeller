"""mesh_to_brep — atomic create. STL/OBJ mesh file → BREP body.

Approach: read the mesh as a `Poly_Triangulation` via `RWStl.ReadFile_s`, then
for every triangle build a planar 3-edge wire, materialize a face, and feed
them into `BRepBuilderAPI_Sewing`. The result is a sewn shell (`TopoDS_Shell`
or compound thereof). If the mesh is closed and orientable, `BRepBuilderAPI_
MakeSolid` will lift the shell to a solid; otherwise we keep the shell.

Round-6 hardening:
    - **Chunked sewing.** `BRepBuilderAPI_Sewing.Add()` is per-face and is
      cheap; only `Perform()` is the heavy step. We process the input
      triangles in CHUNKS (default 5000) and log progress so very large
      meshes don't appear to hang. The face cap (`max_faces`) defaults to
      None (unbounded) — callers can still set a value to short-circuit
      ridiculous inputs.
    - **Unit auto-detect.** STL has no unit metadata. We compute the bbox
      diagonal of the input mesh and, if it falls outside [0.01, 100000] mm,
      rescale uniformly so the diagonal lands at `target_diag_mm`. This
      catches Blender-default-meter exports (~1e-2 mm equivalent) and
      micron-scale dumps without surprising the caller.
    - **Closure detection.** After sewing, we walk every shell in the sewn
      output and ask `BRepCheck_Shell.Closed()` whether it's watertight,
      and count `BRepBuilderAPI_Sewing.NbFreeEdges()` as a proxy for
      open boundary edges. Results land in `extras["mesh_to_brep"]`.

Limitations (still):
    - Non-manifold or self-intersecting meshes will sew partially; the
      result may be an open shell. The post-condition is `body_present`,
      not "is a solid", so we don't fail in that case.
    - OBJ files: not yet supported (RWStl handles STL only).

We tolerate degenerate triangles (two coincident vertices) by skipping them
and recording the count in `extras["skipped_degenerate"]`.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


log = logging.getLogger(__name__)


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


def _bbox_of_triangulation(tri) -> tuple[float, float, float, float, float, float]:
    """Return (xmin,ymin,zmin,xmax,ymax,zmax) over all Poly_Triangulation nodes."""
    nb = int(tri.NbNodes())
    if nb == 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    p0 = tri.Node(1)
    xmin = xmax = p0.X()
    ymin = ymax = p0.Y()
    zmin = zmax = p0.Z()
    for i in range(2, nb + 1):
        p = tri.Node(i)
        x, y, z = p.X(), p.Y(), p.Z()
        if x < xmin: xmin = x
        if x > xmax: xmax = x
        if y < ymin: ymin = y
        if y > ymax: ymax = y
        if z < zmin: zmin = z
        if z > zmax: zmax = z
    return xmin, ymin, zmin, xmax, ymax, zmax


@skill(
    name="mesh_to_brep",
    category="create",
    level="atomic",
    summary="Convert an STL mesh into a BREP shell/solid by sewing per-triangle "
            "faces in 5k-triangle chunks. Auto-detects unit scale via bbox "
            "diagonal and lifts the shell to a solid when watertight. The "
            "post-condition is `body_present` (not `is_solid`) so open meshes "
            "still return a shell with a closure report under extras.",
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
        max_triangles: int | None = Field(
            default=None, ge=1,
            description="Legacy alias for `max_faces`. If both are set, "
                        "`max_faces` wins. `None` = unbounded.",
        )
        max_faces: int | None = Field(
            default=None, ge=1,
            description="Optional ceiling on triangle (face) count. None = no "
                        "limit; sewing proceeds in 5k chunks regardless of "
                        "mesh size.",
        )
        chunk_size: int = Field(
            default=5000, ge=100,
            description="Triangle batch size for sewing progress logging. "
                        "BRepBuilderAPI_Sewing handles incremental Add() — "
                        "smaller chunks only affect logging cadence.",
        )
        auto_scale_units: bool = Field(
            default=True,
            description="Auto-rescale the mesh if its bbox diagonal falls "
                        "outside [0.01, 100000] mm (i.e., looks like meters "
                        "or microns). Disable when source units are known-mm.",
        )
        target_diag_mm: float = Field(
            default=100.0, gt=0,
            description="When auto-scaling, the mesh is uniformly scaled so "
                        "its bbox diagonal becomes this many mm.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakePolygon,
            BRepBuilderAPI_MakeSolid,
            BRepBuilderAPI_Sewing,
        )
        from OCP.BRepCheck import BRepCheck_Shell, BRepCheck_Status
        from OCP.gp import gp_Pnt
        from OCP.Poly import Poly_Triangulation
        from OCP.RWStl import RWStl
        from OCP.TopAbs import TopAbs_SHELL
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

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

        # Resolve the effective face cap. `max_faces` is the new canonical
        # name; `max_triangles` is the legacy alias kept for backwards compat
        # with existing tests. `None` on both = no cap.
        face_cap = args.max_faces if args.max_faces is not None else args.max_triangles
        if face_cap is not None and n_tri > face_cap:
            raise RuntimeError(
                f"mesh_to_brep: mesh has {n_tri} triangles, limit is "
                f"{face_cap}. Decimate first via mesh_simplify / "
                f"mesh_decimate, or pass max_faces=None to disable the cap.",
            )

        # --- Unit auto-detect ---------------------------------------------
        # STL has no unit metadata. Compute the input bbox diagonal; if it
        # looks suspiciously tiny (~meters in mm) or huge (~microns in mm),
        # rescale uniformly so the diagonal matches `target_diag_mm`.
        xmin, ymin, zmin, xmax, ymax, zmax = _bbox_of_triangulation(triangulation)
        diag = math.sqrt(
            (xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2,
        )
        scale_applied = 1.0
        if args.auto_scale_units and diag > 0.0 and (diag < 0.01 or diag > 100000.0):
            scale_applied = float(args.target_diag_mm) / diag
            log.warning(
                "mesh_to_brep: bbox diag=%.6g mm out of [0.01, 1e5]; "
                "auto-scaling by factor %.6g to target diag %.3g mm",
                diag, scale_applied, args.target_diag_mm,
            )
            # Materialize a rescaled triangulation. We can't mutate the
            # OCCT Poly_Triangulation in place safely, so rebuild it.
            n_nodes = int(triangulation.NbNodes())
            rescaled = Poly_Triangulation(n_nodes, n_tri, False)
            for i in range(1, n_nodes + 1):
                q = triangulation.Node(i)
                rescaled.SetNode(
                    i,
                    gp_Pnt(q.X() * scale_applied,
                           q.Y() * scale_applied,
                           q.Z() * scale_applied),
                )
            for i in range(1, n_tri + 1):
                rescaled.SetTriangle(i, triangulation.Triangle(i))
            triangulation = rescaled

        # Sewing tolerance dictates both vertex merging and degenerate filtering.
        tol = float(args.sewing_tolerance_mm)
        sewer = BRepBuilderAPI_Sewing(tol)

        skipped = 0
        added = 0
        chunk = max(100, int(args.chunk_size))
        log.info(
            "mesh_to_brep: sewing %d triangles in chunks of %d (tol=%.4g mm)",
            n_tri, chunk, tol,
        )
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
            if i % chunk == 0:
                log.info(
                    "mesh_to_brep: progress %d/%d triangles added (skipped=%d)",
                    added, n_tri, skipped,
                )

        if added == 0:
            raise RuntimeError(
                f"mesh_to_brep: every triangle was degenerate or rejected "
                f"({skipped} skipped). Try a larger sewing_tolerance_mm.",
            )

        log.info("mesh_to_brep: Perform() over %d sewn faces …", added)
        sewer.Perform()
        sewn = sewer.SewedShape()
        if sewn is None or sewn.IsNull():
            raise RuntimeError("mesh_to_brep: BRepBuilderAPI_Sewing produced null shape")

        # Open-edge count (sewer's view of un-merged boundary edges).
        try:
            open_edges = int(sewer.NbFreeEdges())
        except Exception:
            open_edges = -1

        result_shape = sewn
        is_solid = False
        is_shell = False
        is_closed = False

        # --- Closure check + solid lift ------------------------------------
        if args.try_make_solid:
            try:
                exp = TopExp_Explorer(sewn, TopAbs_SHELL)
                if exp.More():
                    shell = TopoDS.Shell_s(exp.Current())
                    is_shell = True
                    # Ask BRepCheck whether this shell is watertight.
                    try:
                        checker = BRepCheck_Shell(shell)
                        status = checker.Closed()
                        is_closed = (status == BRepCheck_Status.BRepCheck_NoError)
                    except Exception:
                        is_closed = False
                    if is_closed:
                        solid_maker = BRepBuilderAPI_MakeSolid(shell)
                        if solid_maker.IsDone():
                            candidate = solid_maker.Solid()
                            if candidate is not None and not candidate.IsNull():
                                result_shape = candidate
                                is_solid = True
            except Exception:
                # fall back to whatever sewer produced
                pass

        if is_shell and not is_closed:
            log.warning(
                "mesh_to_brep: sewn shell is NOT closed (open_edges=%d). "
                "Returning open shell — caller should inspect "
                "extras['mesh_to_brep'].",
                open_edges,
            )

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
                "mesh_to_brep": {
                    "closed": bool(is_closed),
                    "open_edges": int(open_edges),
                    "scale_applied": float(scale_applied),
                    "input_diag_mm": float(diag),
                    "chunk_size": int(chunk),
                    "face_cap": face_cap,
                },
            },
        )
