"""geometry_deviation — atomic, read-only.

Geometric ground truth: compare the current body against a reference solid
loaded from a STEP file. Unlike catalog/manifest comparisons this measures
the *geometry itself*, so it breaks the circularity of catalog-vs-catalog
verification (plan item V4).

Algorithm:
    1. Load the reference via ``STEPControl_Reader`` (``reference_step_path``).
    2. ``align='bbox_center'`` optionally translates the reference so its
       bbox center coincides with the body's — needed when comparing
       box-local rebuilds against world-frame originals.
    3. Exact BRep properties via GProp: ``volume_delta_pct`` +
       ``surface_area_delta_pct`` (signed, relative to the reference).
    4. Tessellate both shapes with ``BRepMesh_IncrementalMesh`` at
       ``linear_deflection_mm``.
    5. Bidirectional point-to-triangle deviation: sample up to
       ``max_sample_points`` mesh vertices per direction, query a uniform
       spatial hash grid of the opposing mesh's triangles (cell ≈ 2× median
       triangle edge), and compute exact closest-point-on-triangle distances
       over the 3×3×3 cell neighborhood. When a neighborhood is empty the
       distance falls back to nearest-vertex (counted in
       ``fallback_fraction``).

Caveat: tessellation chord error (≤ linear_deflection_mm per mesh) adds to
the measured deviation — pick the deflection well below the deviation you
want to resolve.

extras schema:
    {"geometry_deviation": {
        "volume_delta_pct": float | None,        # (body - ref) / ref * 100
        "surface_area_delta_pct": float | None,
        "hausdorff_mm": float,                   # max of both directions
        "rms_mm": float,                         # over all samples (both dirs)
        "p95_mm": float,
        "max_body_to_ref_mm": float,
        "max_ref_to_body_mm": float,
        "sample_counts": {"body_to_ref": int, "ref_to_body": int},
        "fallback_fraction": float,              # nearest-vertex fallbacks
        "linear_deflection_mm": float,
        "align": str,
        "reference_step_path": str,
     }}

body 는 변경하지 않는다 (post ``body_present``).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _load_step(path: str):
    """STEP 파일 → OCCT TopoDS_Shape (mm)."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"geometry_deviation: STEP not found: {p}")
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(p))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"geometry_deviation: STEP parse failed: {p} (status={status})")
    reader.TransferRoots()
    return reader.OneShape()


def _bbox_center(shape) -> tuple[float, float, float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        raise ValueError("geometry_deviation: shape has a void bounding box")
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0, (zmin + zmax) / 2.0)


def _translate(shape, dx: float, dy: float, dz: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec

    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(dx, dy, dz))
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def _gprop_volume_area(shape) -> tuple[float, float]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    pv = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, pv)
    pa = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, pa)
    return float(pv.Mass()), float(pa.Mass())


def _delta_pct(current: float, reference: float) -> float | None:
    """Signed (current - reference) / reference * 100; None when ref ≈ 0."""
    if abs(reference) < 1e-12:
        return None
    return (current - reference) / reference * 100.0


def _tessellate(shape, linear_deflection_mm: float) -> None:
    from OCP.BRepMesh import BRepMesh_IncrementalMesh

    mesher = BRepMesh_IncrementalMesh(
        shape,
        float(linear_deflection_mm),
        False,   # relative
        0.5,     # angular deflection (rad) — OCCT default
        True,    # parallel
    )
    mesher.Perform()
    if not mesher.IsDone():
        raise RuntimeError("geometry_deviation: BRepMesh_IncrementalMesh failed")


def _iter_triangulations(shape):
    """Yield (face, Poly_Triangulation, location) for every meshed face.

    Same iteration pattern as ``io/brep_to_mesh.py``.
    """
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


def _triangle_soup(shape) -> tuple[np.ndarray, np.ndarray]:
    """Tessellated shape → (vertices (N,3), triangles (M,3,3)) in world frame."""
    all_verts: list[np.ndarray] = []
    all_tris: list[np.ndarray] = []
    for _face, tri, loc in _iter_triangulations(shape):
        nb_nodes = int(tri.NbNodes())
        nb_tri = int(tri.NbTriangles())
        if nb_nodes == 0 or nb_tri == 0:
            continue
        nodes = np.empty((nb_nodes, 3), dtype=float)
        for i in range(1, nb_nodes + 1):
            p = tri.Node(i)
            nodes[i - 1] = (p.X(), p.Y(), p.Z())
        # face location → 3x4 affine [R | t] applied via numpy.
        trsf = loc.Transformation()
        m = np.array(
            [[trsf.Value(r, c) for c in range(1, 5)] for r in range(1, 4)],
            dtype=float,
        )
        nodes = nodes @ m[:, :3].T + m[:, 3]
        idx = np.empty((nb_tri, 3), dtype=np.int64)
        for i in range(1, nb_tri + 1):
            n1, n2, n3 = tri.Triangle(i).Get()
            idx[i - 1] = (n1 - 1, n2 - 1, n3 - 1)
        all_verts.append(nodes)
        all_tris.append(nodes[idx])
    if not all_verts:
        raise RuntimeError("geometry_deviation: tessellation produced no triangles")
    return np.concatenate(all_verts), np.concatenate(all_tris)


def _sdiv(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    """Element-wise division with NaN/Inf (degenerate triangles) → 0."""
    with np.errstate(divide="ignore", invalid="ignore"):
        out = num / den
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def _point_triangle_sq_dist(
    pts: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray
) -> np.ndarray:
    """Squared distance from each point to each triangle, (P,K).

    Vectorised closest-point-on-triangle (Ericson, RTCD §5.1.5): the seven
    Voronoi regions (3 vertices, 3 edges, interior) are evaluated for the
    whole (P,K) batch and selected by priority masks.
    """
    P = pts[:, None, :]      # (P,1,3)
    A = a[None, :, :]        # (1,K,3)
    B = b[None, :, :]
    C = c[None, :, :]
    ab = B - A
    ac = C - A
    ap = P - A
    d1 = (ab * ap).sum(-1)
    d2 = (ac * ap).sum(-1)
    bp = P - B
    d3 = (ab * bp).sum(-1)
    d4 = (ac * bp).sum(-1)
    cp = P - C
    d5 = (ab * cp).sum(-1)
    d6 = (ac * cp).sum(-1)

    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2

    v_ab = _sdiv(d1, d1 - d3)[..., None]
    w_ac = _sdiv(d2, d2 - d6)[..., None]
    w_bc = _sdiv(d4 - d3, (d4 - d3) + (d5 - d6))[..., None]
    denom = va + vb + vc
    v_in = _sdiv(vb, denom)[..., None]
    w_in = _sdiv(vc, denom)[..., None]

    # Default: interior; then override in *reverse* priority order so the
    # first-matching scalar branch wins.
    closest = A + ab * v_in + ac * w_in
    cond6 = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
    closest = np.where(cond6[..., None], B + (C - B) * w_bc, closest)
    cond5 = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
    closest = np.where(cond5[..., None], A + ac * w_ac, closest)
    cond4 = (d6 >= 0) & (d5 <= d6)
    closest = np.where(cond4[..., None], np.broadcast_to(C, closest.shape), closest)
    cond3 = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
    closest = np.where(cond3[..., None], A + ab * v_ab, closest)
    cond2 = (d3 >= 0) & (d4 <= d3)
    closest = np.where(cond2[..., None], np.broadcast_to(B, closest.shape), closest)
    cond1 = (d1 <= 0) & (d2 <= 0)
    closest = np.where(cond1[..., None], np.broadcast_to(A, closest.shape), closest)

    diff = P - closest
    return (diff * diff).sum(-1)


class _TriGrid:
    """Uniform spatial hash of triangles, keyed by integer cell coords."""

    def __init__(self, tris: np.ndarray):
        self.tris = tris
        edges = np.concatenate([
            np.linalg.norm(tris[:, 0] - tris[:, 1], axis=1),
            np.linalg.norm(tris[:, 1] - tris[:, 2], axis=1),
            np.linalg.norm(tris[:, 2] - tris[:, 0], axis=1),
        ])
        median_edge = float(np.median(edges))
        if not math.isfinite(median_edge) or median_edge <= 1e-9:
            # Degenerate fallback — one cell per bbox-diagonal percent.
            span = tris.reshape(-1, 3)
            median_edge = max(1e-6, float(np.linalg.norm(
                span.max(axis=0) - span.min(axis=0))) / 100.0)
        self.cell = 2.0 * median_edge
        self.origin = tris.reshape(-1, 3).min(axis=0)

        tri_min = tris.min(axis=1)
        tri_max = tris.max(axis=1)
        imin = np.floor((tri_min - self.origin) / self.cell).astype(np.int64)
        imax = np.floor((tri_max - self.origin) / self.cell).astype(np.int64)
        cells: dict[tuple[int, int, int], list[int]] = {}
        for t in range(len(tris)):
            for i in range(imin[t, 0], imax[t, 0] + 1):
                for j in range(imin[t, 1], imax[t, 1] + 1):
                    for k in range(imin[t, 2], imax[t, 2] + 1):
                        cells.setdefault((i, j, k), []).append(t)
        self.cells = {k: np.asarray(v, dtype=np.int64) for k, v in cells.items()}

    def candidates(self, key: tuple[int, int, int]) -> np.ndarray:
        """Triangle indices in the 3×3×3 neighborhood of cell ``key``."""
        i0, j0, k0 = key
        found = [
            self.cells[(i, j, k)]
            for i in (i0 - 1, i0, i0 + 1)
            for j in (j0 - 1, j0, j0 + 1)
            for k in (k0 - 1, k0, k0 + 1)
            if (i, j, k) in self.cells
        ]
        if not found:
            return np.empty(0, dtype=np.int64)
        return np.unique(np.concatenate(found))


def _subsample(pts: np.ndarray, max_points: int) -> np.ndarray:
    if len(pts) <= max_points:
        return pts
    # Deterministic, evenly spaced — no RNG so reruns are reproducible.
    idx = np.unique(np.linspace(0, len(pts) - 1, max_points).round().astype(np.int64))
    return pts[idx]


def _directed_deviation(
    src_verts: np.ndarray,
    dst_grid: _TriGrid,
    dst_verts: np.ndarray,
    max_sample_points: int,
) -> tuple[np.ndarray, int]:
    """Distances from sampled src vertices to the dst triangle mesh.

    Returns (distances (S,), fallback_count). Fallback = nearest dst *vertex*
    when the 27-cell neighborhood holds no triangles.
    """
    pts = _subsample(src_verts, max_sample_points)
    dists = np.empty(len(pts), dtype=float)
    fallback = np.zeros(len(pts), dtype=bool)

    keys = np.floor((pts - dst_grid.origin) / dst_grid.cell).astype(np.int64)
    uniq, inv, counts = np.unique(
        keys, axis=0, return_inverse=True, return_counts=True)
    order = np.argsort(inv, kind="stable")
    bounds = np.concatenate([[0], np.cumsum(counts)])

    for u in range(len(uniq)):
        sel = order[bounds[u]:bounds[u + 1]]
        cand = dst_grid.candidates(tuple(int(x) for x in uniq[u]))
        if len(cand) == 0:
            fallback[sel] = True
            continue
        tri = dst_grid.tris[cand]
        sq = _point_triangle_sq_dist(pts[sel], tri[:, 0], tri[:, 1], tri[:, 2])
        dists[sel] = np.sqrt(np.maximum(sq.min(axis=1), 0.0))

    fb_idx = np.where(fallback)[0]
    for start in range(0, len(fb_idx), 256):
        chunk = fb_idx[start:start + 256]
        diff = pts[chunk][:, None, :] - dst_verts[None, :, :]
        dists[chunk] = np.sqrt((diff * diff).sum(-1)).min(axis=1)

    return dists, int(len(fb_idx))


@skill(
    name="geometry_deviation",
    category="inspect",
    level="atomic",
    summary="Compare body geometry against a reference STEP: exact GProp "
            "volume/surface-area delta % plus bidirectional tessellated "
            "point-to-triangle deviation (hausdorff/rms/p95 mm). Geometric "
            "ground truth, independent of any catalog. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["geometry_deviation_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.step_read_failed", "fm.tessellation_failed"],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="body_present")],
)
class GeometryDeviation(SkillBase):
    class Args(BaseModel):
        reference_step_path: str | None = Field(
            default=None,
            description="Reference solid as a STEP file (loaded via "
                        "STEPControl_Reader). Required.",
        )
        linear_deflection_mm: float = Field(
            default=0.2, gt=0,
            description="Tessellation chord tolerance — bounds the mesh "
                        "error added to the measured deviation.",
        )
        max_sample_points: int = Field(
            default=50000, ge=100, le=1_000_000,
            description="Per-direction cap on sampled mesh vertices.",
        )
        align: Literal["none", "bbox_center"] = Field(
            default="none",
            description="bbox_center translates the reference so both bbox "
                        "centers coincide (box-local rebuild vs world frame).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("geometry_deviation: body is None")
        shape = _occt_shape(body)
        if shape is None:
            raise ValueError("geometry_deviation: body.wrapped is None")
        if not args.reference_step_path:
            raise ValueError(
                "geometry_deviation: reference_step_path is required")

        ref = _load_step(args.reference_step_path)
        if args.align == "bbox_center":
            bc = _bbox_center(shape)
            rc = _bbox_center(ref)
            ref = _translate(ref, bc[0] - rc[0], bc[1] - rc[1], bc[2] - rc[2])

        # (a) exact BRep properties — independent of tessellation.
        vol_body, area_body = _gprop_volume_area(shape)
        vol_ref, area_ref = _gprop_volume_area(ref)

        # (b) tessellate both at the same deflection.
        _tessellate(shape, args.linear_deflection_mm)
        _tessellate(ref, args.linear_deflection_mm)
        body_verts, body_tris = _triangle_soup(shape)
        ref_verts, ref_tris = _triangle_soup(ref)

        # (c) bidirectional point-to-triangle deviation.
        d_b2r, fb_b2r = _directed_deviation(
            body_verts, _TriGrid(ref_tris), ref_verts, args.max_sample_points)
        d_r2b, fb_r2b = _directed_deviation(
            ref_verts, _TriGrid(body_tris), body_verts, args.max_sample_points)
        all_d = np.concatenate([d_b2r, d_r2b])
        total = int(len(all_d))

        report = {
            "volume_delta_pct": _delta_pct(vol_body, vol_ref),
            "surface_area_delta_pct": _delta_pct(area_body, area_ref),
            "hausdorff_mm": round(float(all_d.max()), 6),
            "rms_mm": round(float(np.sqrt(np.mean(all_d * all_d))), 6),
            "p95_mm": round(float(np.percentile(all_d, 95.0)), 6),
            "max_body_to_ref_mm": round(float(d_b2r.max()), 6),
            "max_ref_to_body_mm": round(float(d_r2b.max()), 6),
            "sample_counts": {
                "body_to_ref": int(len(d_b2r)),
                "ref_to_body": int(len(d_r2b)),
            },
            "fallback_fraction": round((fb_b2r + fb_r2b) / total, 6) if total else 0.0,
            "linear_deflection_mm": float(args.linear_deflection_mm),
            "align": args.align,
            "reference_step_path": str(args.reference_step_path),
        }
        for key in ("volume_delta_pct", "surface_area_delta_pct"):
            if report[key] is not None:
                report[key] = round(report[key], 6)

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"geometry_deviation": report},
        )
