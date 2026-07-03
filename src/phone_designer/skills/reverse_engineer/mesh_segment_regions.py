"""mesh_segment_regions — atomic, read-only. Scan-to-CAD stage 1 (Phase 3-1).

Segment a triangle mesh into connected regions of normal-continuous triangles
(region growing). This is the front half of the scan-to-CAD v1 spike:

    mesh_segment_regions → fit_region_surfaces → scan_to_brep

Input handling — DECISION for roadmap pin (d)
---------------------------------------------
Any body with faces is accepted:

* **faceted shell/solid** (mesh_import / stl_import / mesh_to_brep output —
  every face is already a planar triangle): triangles are recovered 1:1.
* **smooth B-rep solid** (a plain ``Box()`` / ``Cylinder()`` / imported STEP):
  AUTO-TESSELLATED in place with ``BRepMesh_IncrementalMesh``
  (``linear_deflection_mm`` / ``angular_deflection_deg`` args, parallel=False
  for determinism) and segmented from the resulting ``Poly_Triangulation``.
  ``extras["source"]`` reports which path was taken (``"faceted"`` when the
  face count equals the triangle count, else ``"tessellated_brep"``).

Bodies WITHOUT any face (edges, wires, empty compounds, ``body=None``) are a
structured refusal: ``fm.not_a_mesh``.

Algorithm (pure numpy — trimesh is available but adds nothing here: we already
have the triangles from OCCT, and plain edge-adjacency BFS is deterministic)
---------------------------------------------------------------------------
1. Collect world-space triangles per face (proven ``mesh_export`` idiom:
   ``BRep_Tool.Triangulation_s`` + ``TopLoc_Location`` transform + winding
   flip for ``TopAbs_REVERSED`` faces → outward-consistent normals).
2. Weld vertices on a quantized grid (``weld_tolerance_mm``) so triangles
   from different faces share vertex ids.
3. Per-triangle unit normal + area (degenerate triangles — repeated vertex or
   ~zero area — are excluded from growing and reported, region id −1).
4. Edge-adjacency (an edge connects exactly 2 triangles; non-manifold edges
   do NOT connect). Region-grow: neighbour joins when the angle between the
   two ADJACENT triangle normals ≤ ``angle_threshold_deg`` (local continuity,
   so a finely tessellated cylinder wall grows into ONE region).
5. Deterministic output: regions sorted by area desc, tie-broken by smallest
   member triangle index, then renumbered 0..n-1.

extras["mesh_segment_regions"] = {
  "source": "faceted" | "tessellated_brep",
  "n_vertices": int, "n_triangles": int, "n_degenerate": int,
  "angle_threshold_deg": float,
  "n_regions": int,
  "regions": [{"id", "n_triangles", "area_mm2", "normal_spread_deg",
               "mean_normal",           # None when the region wraps (curved)
               "bbox_mm": [xmin..zmax]}],
  "region_of_triangle": [int, ...],     # -1 = degenerate triangle
}

``normal_spread_deg`` is the max angle between any member normal and the
area-weighted mean normal — ~0 for planes, large (up to 180) for wrap-around
curved regions; it is a *descriptor*, not a fit verdict (that is
fit_region_surfaces' job). Body is returned unchanged (post ``body_present``).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# --------------------------------------------------------------------------- #
# triangle extraction (shared by fit_region_surfaces / scan_to_brep)
# --------------------------------------------------------------------------- #

def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _count_faces(shape) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    n = 0
    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        n += 1
        it.Next()
    return n


def extract_triangles(
    body: Any,
    linear_deflection_mm: float = 0.1,
    angular_deflection_deg: float = 5.0,
):
    """Body → (vertices ndarray (N,3), triangles ndarray (M,3) int, source).

    Tessellates in place with BRepMesh_IncrementalMesh (parallel=False —
    deterministic node ordering) and walks every face triangulation using the
    proven mesh_export idiom. Raises ``fm.not_a_mesh`` for face-less input.
    """
    import numpy as np
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    if body is None:
        raise ValueError(
            "fm.not_a_mesh: body is None — mesh_segment_regions needs a "
            "faceted shell (mesh_import / stl_import) or a solid to "
            "auto-tessellate")
    shape = _occt_shape(body)
    if shape is None or (hasattr(shape, "IsNull") and shape.IsNull()):
        raise ValueError("fm.not_a_mesh: body has no underlying OCCT shape")

    n_faces = _count_faces(shape)
    if n_faces == 0:
        raise ValueError(
            "fm.not_a_mesh: input has 0 faces (edge/wire/empty compound) — "
            "not a mesh or a solid; import a mesh first (mesh_import / "
            "stl_import) or pass a solid body")

    mesher = BRepMesh_IncrementalMesh(
        shape,
        float(linear_deflection_mm),
        False,                                    # relative
        math.radians(float(angular_deflection_deg)),
        False,                                    # parallel=False → determinism
    )
    mesher.Perform()

    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        face = TopoDS.Face_s(it.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            reversed_ = face.Orientation() == TopAbs_REVERSED
            base = len(verts)
            for n in range(1, int(tri.NbNodes()) + 1):
                p = tri.Node(n).Transformed(trsf)
                verts.append((p.X(), p.Y(), p.Z()))
            for i in range(1, int(tri.NbTriangles()) + 1):
                a, b, c = tri.Triangle(i).Get()
                if reversed_:
                    b, c = c, b
                tris.append((base + a - 1, base + b - 1, base + c - 1))
        it.Next()

    if not tris:
        raise ValueError(
            "fm.not_a_mesh: tessellation produced 0 triangles — body has "
            f"{n_faces} faces but no triangulatable geometry")

    source = "faceted" if n_faces == len(tris) else "tessellated_brep"
    return (np.asarray(verts, dtype=float),
            np.asarray(tris, dtype=np.int64),
            source)


def weld_vertices(verts, tris, tol: float = 1e-6):
    """Quantize coordinates on a ``tol`` grid and merge duplicate vertices.

    Identical floats always land on the same grid cell, so the mesh_import /
    OBJ round-trip (byte-identical shared corners) welds exactly. Points
    within tol of each other *usually* merge (straddling a cell boundary is
    the documented residual risk of grid welding).
    """
    import numpy as np

    keys = np.round(verts / float(tol)).astype(np.int64)
    uniq_keys, inverse = np.unique(keys, axis=0, return_inverse=True)
    first = np.full(len(uniq_keys), np.iinfo(np.int64).max, dtype=np.int64)
    np.minimum.at(first, inverse, np.arange(len(verts), dtype=np.int64))
    welded = verts[first]
    new_tris = inverse[tris]
    return welded, new_tris


def triangle_normals_areas(verts, tris):
    """→ (unit normals (M,3) — zero rows for degenerate, areas (M,), valid mask)."""
    import numpy as np

    p0 = verts[tris[:, 0]]
    p1 = verts[tris[:, 1]]
    p2 = verts[tris[:, 2]]
    cr = np.cross(p1 - p0, p2 - p0)
    nrm2 = np.linalg.norm(cr, axis=1)
    areas = 0.5 * nrm2
    repeated = (
        (tris[:, 0] == tris[:, 1])
        | (tris[:, 1] == tris[:, 2])
        | (tris[:, 0] == tris[:, 2])
    )
    valid = (nrm2 > 1e-12) & (~repeated)
    normals = np.zeros_like(cr)
    normals[valid] = cr[valid] / nrm2[valid, None]
    return normals, areas, valid


def segment_mesh(verts, tris, angle_threshold_deg: float = 15.0):
    """Region-grow by adjacent-normal continuity.

    → (region_of_triangle (M,) int, regions list) with regions sorted by area
    desc (tie: smallest member triangle index) and renumbered. Degenerate
    triangles keep region id -1.

    region dict: {id, n_triangles, area_mm2, normal_spread_deg, mean_normal
    (None when the area-weighted mean collapses — wrap-around curved region),
    bbox_mm, _tri_indices (ndarray — internal, stripped before JSON)}.
    """
    import numpy as np

    normals, areas, valid = triangle_normals_areas(verts, tris)
    m = len(tris)

    # edge → adjacent triangles (only edges shared by exactly 2 connect)
    edge_tris: dict[tuple[int, int], list[int]] = {}
    for t in range(m):
        if not valid[t]:
            continue
        a, b, c = int(tris[t, 0]), int(tris[t, 1]), int(tris[t, 2])
        for i, j in ((a, b), (b, c), (c, a)):
            key = (i, j) if i < j else (j, i)
            edge_tris.setdefault(key, []).append(t)

    neighbors: list[list[int]] = [[] for _ in range(m)]
    for pair in edge_tris.values():
        if len(pair) == 2:
            neighbors[pair[0]].append(pair[1])
            neighbors[pair[1]].append(pair[0])

    cos_thr = math.cos(math.radians(float(angle_threshold_deg)))
    region_of = np.full(m, -1, dtype=np.int64)
    rid = 0
    for seed in range(m):
        if not valid[seed] or region_of[seed] != -1:
            continue
        region_of[seed] = rid
        stack = [seed]
        while stack:
            t = stack.pop()
            nt = normals[t]
            for nb in neighbors[t]:
                if region_of[nb] == -1 and float(nt @ normals[nb]) >= cos_thr:
                    region_of[nb] = rid
                    stack.append(nb)
        rid += 1

    # summarize + deterministic ordering
    raw: list[dict] = []
    for r in range(rid):
        idx = np.nonzero(region_of == r)[0]
        r_areas = areas[idx]
        area = float(np.sum(r_areas))
        w_mean = np.sum(normals[idx] * r_areas[:, None], axis=0)
        w_norm = float(np.linalg.norm(w_mean))
        if w_norm > 1e-9:
            mean_n = w_mean / w_norm
            dots = np.clip(normals[idx] @ mean_n, -1.0, 1.0)
            spread = float(math.degrees(float(np.arccos(np.min(dots)))))
            mean_normal = [float(x) for x in mean_n]
        else:
            # wrap-around curved region: mean normal collapses — honest None
            mean_normal = None
            spread = 180.0
        v_idx = np.unique(tris[idx].ravel())
        pts = verts[v_idx]
        bbox = [float(x) for x in np.min(pts, axis=0)] + \
               [float(x) for x in np.max(pts, axis=0)]
        raw.append({
            "n_triangles": int(len(idx)),
            "area_mm2": area,
            "normal_spread_deg": spread,
            "mean_normal": mean_normal,
            "bbox_mm": bbox,
            "_tri_indices": idx,
            "_min_tri": int(idx[0]) if len(idx) else 0,
        })

    order = sorted(range(len(raw)),
                   key=lambda k: (-raw[k]["area_mm2"], raw[k]["_min_tri"]))
    remap = {old: new for new, old in enumerate(order)}
    out_region_of = np.array(
        [remap[r] if r >= 0 else -1 for r in region_of], dtype=np.int64)
    regions: list[dict] = []
    for new, old in enumerate(order):
        d = raw[old]
        regions.append({
            "id": new,
            "n_triangles": d["n_triangles"],
            "area_mm2": d["area_mm2"],
            "normal_spread_deg": d["normal_spread_deg"],
            "mean_normal": d["mean_normal"],
            "bbox_mm": d["bbox_mm"],
            "_tri_indices": d["_tri_indices"],
        })
    return out_region_of, regions


def segment_body(
    body: Any,
    angle_threshold_deg: float = 15.0,
    weld_tolerance_mm: float = 1e-6,
    linear_deflection_mm: float = 0.1,
    angular_deflection_deg: float = 5.0,
    max_triangles: int = 200000,
):
    """Full pipeline body → (verts, tris, normals, areas, valid, region_of,
    regions, source). Shared by all three scan-to-CAD skills."""
    verts, tris, source = extract_triangles(
        body, linear_deflection_mm, angular_deflection_deg)
    if len(tris) > int(max_triangles):
        raise ValueError(
            f"fm.too_many_triangles: mesh has {len(tris)} triangles, limit "
            f"is {max_triangles}. Decimate first (mesh_decimate / "
            "mesh_simplify) or raise max_triangles.")
    verts, tris = weld_vertices(verts, tris, weld_tolerance_mm)
    normals, areas, valid = triangle_normals_areas(verts, tris)
    region_of, regions = segment_mesh(verts, tris, angle_threshold_deg)
    return verts, tris, normals, areas, valid, region_of, regions, source


def _round3(x: float, nd: int = 6) -> float:
    return round(float(x), nd)


def region_summary_json(regions: list[dict]) -> list[dict]:
    """Strip internal ndarray fields → strict-JSON-safe region list."""
    out = []
    for r in regions:
        out.append({
            "id": int(r["id"]),
            "n_triangles": int(r["n_triangles"]),
            "area_mm2": _round3(r["area_mm2"]),
            "normal_spread_deg": _round3(r["normal_spread_deg"], 3),
            "mean_normal": (
                [_round3(c) for c in r["mean_normal"]]
                if r["mean_normal"] is not None else None),
            "bbox_mm": [_round3(c) for c in r["bbox_mm"]],
        })
    return out


# --------------------------------------------------------------------------- #
# skill
# --------------------------------------------------------------------------- #

@skill(
    name="mesh_segment_regions",
    category="reverse_engineer",
    level="atomic",
    summary="Scan-to-CAD stage 1: segment a triangle mesh (faceted shell from "
            "mesh_import/stl_import, or any solid via deterministic "
            "auto-tessellation) into normal-continuous regions by "
            "edge-adjacency region growing (default 15° threshold). Returns "
            "per-region area/normal-spread/bbox plus a region id per "
            "triangle, sorted by area desc. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["mesh_region_segmentation"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.not_a_mesh", "fm.too_many_triangles"],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="body_present")],
)
class MeshSegmentRegions(SkillBase):
    class Args(BaseModel):
        angle_threshold_deg: float = Field(
            default=15.0, gt=0.0, le=90.0,
            description="Adjacent triangles whose normals differ by more than "
                        "this angle start a new region.")
        weld_tolerance_mm: float = Field(
            default=1e-6, gt=0,
            description="Vertex weld grid — coincident triangle corners "
                        "within this distance become one vertex.")
        linear_deflection_mm: float = Field(
            default=0.1, gt=0,
            description="Auto-tessellation chord tolerance for smooth B-rep "
                        "input (ignored for already-faceted shells).")
        angular_deflection_deg: float = Field(
            default=5.0, gt=0,
            description="Auto-tessellation angular deflection — keeps "
                        "adjacent-normal steps on curved faces well under "
                        "angle_threshold_deg.")
        max_triangles: int = Field(
            default=200000, ge=1,
            description="Guard against pathological meshes "
                        "(fm.too_many_triangles).")
        include_triangle_map: bool = Field(
            default=True,
            description="Include region_of_triangle (one int per triangle) "
                        "in extras. Disable for very large meshes.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        verts, tris, _normals, _areas, valid, region_of, regions, source = \
            segment_body(
                body,
                angle_threshold_deg=args.angle_threshold_deg,
                weld_tolerance_mm=args.weld_tolerance_mm,
                linear_deflection_mm=args.linear_deflection_mm,
                angular_deflection_deg=args.angular_deflection_deg,
                max_triangles=args.max_triangles,
            )
        n_degenerate = int((~valid).sum())
        extras = {
            "mesh_segment_regions": {
                "source": source,
                "n_vertices": int(len(verts)),
                "n_triangles": int(len(tris)),
                "n_degenerate": n_degenerate,
                "angle_threshold_deg": float(args.angle_threshold_deg),
                "n_regions": int(len(regions)),
                "regions": region_summary_json(regions),
                **({"region_of_triangle": [int(r) for r in region_of]}
                   if args.include_triangle_map else {}),
            }
        }
        return SkillResult(body=body, history=EntityHistoryMap(),
                           extras=extras)
