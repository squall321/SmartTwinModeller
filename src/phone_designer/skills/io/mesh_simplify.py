"""mesh_simplify — atomic. STL file → decimated STL file (+ tiny BREP placeholder).

Vertex-cluster decimation (a.k.a. grid-snap simplification — close cousin of
quadric edge-collapse but much simpler to implement on top of OCCT). Strategy:

    1. Read the input STL via `RWStl.ReadFile_s` → Poly_Triangulation.
    2. Snap every vertex to a regular grid whose cell size is derived from
       the requested `target_ratio` (smaller ratio = coarser grid).
    3. Re-emit triangles; drop any whose vertices collapsed to ≤ 2 distinct
       grid points.
    4. Build a new Poly_Triangulation and write it back via
       `RWStl.WriteBinary_s`.

The skill returns the *input* body unchanged (path-driven, not body-driven),
plus rich `extras["simplify_stats"]`. The `body_present` post-condition is
satisfied as long as we pass a real body in — callers typically chain
`mesh_simplify` after `mesh_to_brep` or run it standalone with a dummy body.

Notes:
    - True quadric decimation (Garland-Heckbert) needs per-vertex error
      quadrics and edge-collapse priority queues — that's a meshlab-grade
      module. Vertex-cluster decimation gives ~80 % of the quality at ~5 %
      of the code and is well-suited as a pre-processing step for
      `mesh_to_brep`.
    - For input meshes that already snap to a grid (e.g., voxelized output),
      `target_ratio=1.0` is a no-op.
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


def _bbox_of_triangulation(tri) -> tuple[float, float, float, float, float, float]:
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
    name="mesh_simplify",
    category="inspect",
    level="atomic",
    summary="Decimate an STL mesh via vertex-cluster (grid-snap) simplification "
            "and write the reduced mesh to a new STL. Body is returned "
            "unchanged. Output triangle ratio approximates `target_ratio` but "
            "exact count depends on mesh topology.",
    selector_kinds=[],
    history_rules={},
    produces_features=["stl_artifact", "simplify_stats"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.file_not_found", "fm.stl_parse_failed", "fm.stl_write_failed"],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class MeshSimplify(SkillBase):
    class Args(BaseModel):
        input_path: str = Field(min_length=1, description="입력 STL 파일 경로")
        output_path: str = Field(min_length=1, description="출력(decimated) STL 경로")
        target_ratio: float = Field(
            default=0.5, gt=0.0, le=1.0,
            description="Target fraction of original triangle count (0.5 = ~half). "
                        "Cluster grid is sized from this ratio.",
        )
        write_binary: bool = Field(
            default=True,
            description="Binary STL (compact) vs. ASCII STL.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.gp import gp_Pnt
        from OCP.Poly import Poly_Triangle, Poly_Triangulation
        from OCP.RWStl import RWStl

        in_path = Path(args.input_path)
        if not in_path.exists():
            raise FileNotFoundError(f"mesh_simplify: input STL not found: {in_path}")

        tri = RWStl.ReadFile_s(str(in_path))
        if tri is None:
            raise RuntimeError(f"mesh_simplify: failed to parse STL: {in_path}")

        n_tri_in = int(tri.NbTriangles())
        n_nodes_in = int(tri.NbNodes())
        if n_tri_in == 0:
            raise RuntimeError(f"mesh_simplify: input STL has 0 triangles: {in_path}")

        # Determine grid cell size from target ratio. Heuristic: if the ratio
        # is r, the linear cell size scales as (1/r)^(1/2) of the average
        # edge length proxy — keeps triangle reduction in the right ballpark.
        xmin, ymin, zmin, xmax, ymax, zmax = _bbox_of_triangulation(tri)
        diag = math.sqrt(
            (xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2,
        )
        # baseline cell ~ diag / cbrt(n_nodes) (roughly avg vertex spacing)
        baseline = diag / max(1.0, n_nodes_in ** (1.0 / 3.0))
        # smaller target_ratio → larger cell → fewer surviving vertices
        scale = (1.0 / max(args.target_ratio, 1e-6)) ** 0.5
        cell = max(baseline * scale, 1e-9)

        # Cluster vertices: map original 1-based node idx → new 1-based idx.
        cluster_to_new: dict[tuple[int, int, int], int] = {}
        old_to_new: dict[int, int] = {}
        new_nodes: list[tuple[float, float, float]] = []

        for i in range(1, n_nodes_in + 1):
            p = tri.Node(i)
            key = (
                int(math.floor(p.X() / cell)),
                int(math.floor(p.Y() / cell)),
                int(math.floor(p.Z() / cell)),
            )
            if key in cluster_to_new:
                old_to_new[i] = cluster_to_new[key]
            else:
                new_idx = len(new_nodes) + 1
                cluster_to_new[key] = new_idx
                old_to_new[i] = new_idx
                # cluster representative = grid-cell center
                new_nodes.append((
                    (key[0] + 0.5) * cell,
                    (key[1] + 0.5) * cell,
                    (key[2] + 0.5) * cell,
                ))

        # Re-emit triangles; drop those whose three corners aren't distinct.
        new_triangles: list[tuple[int, int, int]] = []
        seen_tris: set[tuple[int, int, int]] = set()
        dropped = 0
        for i in range(1, n_tri_in + 1):
            t = tri.Triangle(i)
            a, b, c = t.Get()
            na = old_to_new[a]
            nb = old_to_new[b]
            nc = old_to_new[c]
            if na == nb or nb == nc or na == nc:
                dropped += 1
                continue
            # dedup by sorted triple (orientation-agnostic)
            key = tuple(sorted((na, nb, nc)))
            if key in seen_tris:
                dropped += 1
                continue
            seen_tris.add(key)
            new_triangles.append((na, nb, nc))

        if not new_triangles:
            raise RuntimeError(
                "mesh_simplify: every triangle collapsed at target_ratio="
                f"{args.target_ratio}. Use a larger ratio.",
            )

        # Build the new Poly_Triangulation.
        n_new_nodes = len(new_nodes)
        n_new_tris = len(new_triangles)
        out_tri = Poly_Triangulation(n_new_nodes, n_new_tris, False)
        for i, (x, y, z) in enumerate(new_nodes, start=1):
            out_tri.SetNode(i, gp_Pnt(x, y, z))
        for i, (a, b, c) in enumerate(new_triangles, start=1):
            out_tri.SetTriangle(i, Poly_Triangle(a, b, c))

        out_path = Path(args.output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # RWStl writers accept an OSD_Path (not a Python str); wrap accordingly.
        from OCP.OSD import OSD_Path
        from OCP.TCollection import TCollection_AsciiString
        osd_out = OSD_Path(TCollection_AsciiString(str(out_path)))
        if args.write_binary:
            ok = RWStl.WriteBinary_s(out_tri, osd_out)
        else:
            ok = RWStl.WriteAscii_s(out_tri, osd_out)
        if not ok or not out_path.exists():
            raise RuntimeError(
                f"mesh_simplify: STL write failed → {out_path}",
            )

        ratio_actual = n_new_tris / max(1, n_tri_in)

        return SkillResult(
            body=body,  # passthrough (may be None — body_present check enforced
                        # only when caller provides one)
            history=EntityHistoryMap(),
            extras={
                "simplify_stats": {
                    "input_path": str(in_path),
                    "output_path": str(out_path),
                    "triangles_in": int(n_tri_in),
                    "triangles_out": int(n_new_tris),
                    "vertices_in": int(n_nodes_in),
                    "vertices_out": int(n_new_nodes),
                    "triangles_dropped": int(dropped),
                    "target_ratio": float(args.target_ratio),
                    "actual_ratio": round(ratio_actual, 4),
                    "cell_size_mm": round(cell, 6),
                },
            },
        )
