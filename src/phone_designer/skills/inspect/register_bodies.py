"""register_bodies — atomic, read-only. (phase-1, 2026-06-14)

Best-fit RIGID transform mapping body B (the current body) onto a reference
A (loaded from STEP or passed directly). The output 4x4 is the matrix that
maps B's frame INTO A's frame — i.e. exactly the ``transform_4x4`` that
``geometry_deviation(align='rigid', ...)`` applies to the REFERENCE. So the
canonical pipeline is::

    reg = RegisterBodies().apply(bodyB, {"reference_step_path": A})
    T   = reg.extras["registration"]["transform_4x4"]
    dev = GeometryDeviation().apply(bodyB, {
        "reference_step_path": A, "align": "rigid", "transform_4x4": T})

Stage 1 (coarse — ``method='principal'``):
    1. Translate B's centroid onto A's centroid (mass_properties centroids).
    2. Rotate via principal-axis alignment. Both inertia tensors are
       eigen-decomposed with ``principal_axes._symm_3x3_eigen`` (reused, not
       forked). Eigenvectors are only defined up to sign AND, when moments
       are near-equal, up to permutation — so the 24 proper-rotation axis
       combinations (sign × permutation, det=+1) are enumerated and the one
       minimizing a cheap point-set residual (B's eigen-frame corner probes
       vs A's) is chosen. Near-degenerate moments (two close eigenvalues)
       set ``axis_ambiguous=True``.

Stage 2 (refine — ``method='feature'``, only when both catalogs exist):
    Rigid Kabsch/Umeyama fit (numpy SVD) over matched feature centroids.
    Pairs come from ``feature_fidelity_diff._greedy_pair`` per kind (imported,
    not forked); the per-kind centroids are gathered in BOTH frames and a
    single rotation+translation is solved that maps B's matched centroids onto
    A's. Requires >= 3 non-collinear matched centroids; otherwise the coarse
    principal result is kept.

``bbox_fallback``: when neither inertia nor catalogs yield a usable rotation
(e.g. a perfectly isotropic body) the transform degrades to a pure
centroid-to-centroid translation (identity rotation).

Stage 2.5 (phase-4, 2026-06-15 — ``icp_refine=True``, default-on): ICP +
symmetry multi-start over the tessellated surfaces. The principal seed leaves
the residual rotation FREE on inertia-degenerate parts (cylinders/plates — two
equal moments) and a discrete ambiguity on globally-symmetric parts, so a
single coarse pick can be confidently WRONG. The top-K coarse seatings (the 24
octahedral orientations already enumerated) are ICP-refined — reusing
``geometry_deviation``'s ``_TriGrid`` + exact closest-point-on-triangle (no new
dep, no kd-tree) — and the MIN-residual seating is kept, measured on the true
closest-point-on-surface residual (geometry, never match_ratio). The loop is
TIME-BOXED (iteration + wall-clock cap — the KR600 900s lesson). The Stage-2
feature fit is adopted only when it does not geometrically REGRESS the ICP
seat, so a corrupted catalog can never override a good geometric seating.

HONEST DOWNGRADE: when no seating achieves a small residual relative to the
part (``rmsd_norm`` large) — a genuinely ambiguous/degenerate part — the skill
emits ``registration_confidence='low'`` + ``axis_ambiguous=True`` rather than a
confidently-WRONG transform, so ``compare_parts`` flags a low-confidence diff.

extras schema::

    {"registration": {
        "transform_4x4": [[...],[...],[...],[0,0,0,1]],  # B -> A frame
        "rmsd_mm": float,            # residual of the chosen fit (mm)
        "rmsd_norm": float | None,   # rmsd / reference bbox diagonal (scale-free)
        "method": "principal" | "icp" | "feature" | "bbox_fallback",
        "axis_ambiguous": bool,      # near-degenerate principal moments
        "registration_confidence": "high" | "medium" | "low",  # honest tier
        "confidence": float,         # 0..1 heuristic (clamped <=0.3 when 'low')
        "icp_rmsd_mm": float | None, # best ICP residual found (diagnostic)
        "centroid_a": [x,y,z],
        "centroid_b": [x,y,z],
    }}

Body is unchanged (post ``body_present``).
"""
from __future__ import annotations

import itertools
import math
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
# Reused (NOT forked) per task spec.
from phone_designer.skills.inspect.principal_axes import _symm_3x3_eigen


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _load_step(path: str):
    """STEP file -> OCCT TopoDS_Shape (mm). Mirrors geometry_deviation."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"register_bodies: STEP not found: {p}")
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(p))
    if status != IFSelect_RetDone:
        raise RuntimeError(
            f"register_bodies: STEP parse failed: {p} (status={status})")
    reader.TransferRoots()
    return reader.OneShape()


def _mass_frame(shape) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (centroid (3,), eigenvalues (3,) asc, eigenvectors cols (3,3)).

    Eigenvectors are columns; column k is the principal axis with moment
    eigenvalues[k]. Sorted ascending in moment (matching _symm_3x3_eigen).
    On GProp failure → centroid at origin, identity frame, zero moments.
    """
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    c = props.CentreOfMass()
    centroid = np.array([c.X(), c.Y(), c.Z()], dtype=float)
    mat = props.MatrixOfInertia()
    rows = [[float(mat.Value(i + 1, j + 1)) for j in range(3)] for i in range(3)]
    eigs = _symm_3x3_eigen(rows)
    vals = np.array([ev for ev, _vec in eigs], dtype=float)
    vecs = np.array([list(vec) for _ev, vec in eigs], dtype=float).T  # cols
    # Jacobi eigenvectors are orthonormal but not guaranteed right-handed.
    # Force a proper rotation (det=+1) by flipping the last column when the
    # frame is a reflection — otherwise det(Va)·det(Vb) = -1 makes EVERY
    # Va @ S @ Vb.T improper and the 24-orientation enumeration returns none.
    if float(np.linalg.det(vecs)) < 0:
        vecs[:, 2] = -vecs[:, 2]
    return centroid, vals, vecs


def _is_ambiguous(vals: np.ndarray, rel_tol: float = 0.05) -> bool:
    """Near-degenerate moments → principal axes are not uniquely orientable.

    True when any adjacent pair of (ascending) eigenvalues are within
    ``rel_tol`` of each other relative to the spread of moments.
    """
    if vals.size < 3:
        return True
    span = float(vals.max() - vals.min())
    if span <= 1e-9:
        return True  # fully isotropic
    for i in range(2):
        if abs(vals[i + 1] - vals[i]) / span < rel_tol:
            return True
    return False


def _mesh_vertices(shape, deflection_mm: float = 1.0) -> np.ndarray:
    """Tessellate ``shape`` and return its mesh vertices (N,3) in world frame.

    A cheap, deterministic point-set for scoring candidate rotations and for
    the final geometric residual. Mirrors geometry_deviation's triangulation
    iteration. Returns an empty (0,3) array on failure.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    mesher = BRepMesh_IncrementalMesh(
        shape, float(deflection_mm), False, 0.5, True)
    mesher.Perform()
    verts: list[np.ndarray] = []
    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        try:
            face = TopoDS.Face_s(it.Current())
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)
            if tri is not None:
                nb = int(tri.NbNodes())
                if nb:
                    nodes = np.empty((nb, 3), dtype=float)
                    for i in range(1, nb + 1):
                        p = tri.Node(i)
                        nodes[i - 1] = (p.X(), p.Y(), p.Z())
                    trsf = loc.Transformation()
                    m = np.array(
                        [[trsf.Value(r, c) for c in range(1, 5)]
                         for r in range(1, 4)], dtype=float)
                    nodes = nodes @ m[:, :3].T + m[:, 3]
                    verts.append(nodes)
        except Exception:
            pass
        it.Next()
    if not verts:
        return np.empty((0, 3), dtype=float)
    return np.concatenate(verts)


def _subsample(pts: np.ndarray, max_points: int) -> np.ndarray:
    """Deterministic even subsample (no RNG -> reproducible)."""
    if len(pts) <= max_points:
        return pts
    idx = np.unique(
        np.linspace(0, len(pts) - 1, max_points).round().astype(np.int64))
    return pts[idx]


def _nn_rmsd(src: np.ndarray, dst: np.ndarray) -> float:
    """Mean nearest-neighbour distance from each src point to the dst cloud.

    Chunked to bound memory. This is the 'cheap point-set residual' used to
    rank the 24 candidate orientations and to report a true geometric rmsd.
    """
    if len(src) == 0 or len(dst) == 0:
        return float("inf")
    total = 0.0
    n = 0
    for start in range(0, len(src), 512):
        chunk = src[start:start + 512]
        d = np.linalg.norm(chunk[:, None, :] - dst[None, :, :], axis=2)
        mn = d.min(axis=1)
        total += float(np.sum(mn * mn))
        n += len(chunk)
    return float(np.sqrt(total / n)) if n else float("inf")


# ── ICP refinement (phase-4, 2026-06-15) ─────────────────────────────────────
# The principal-axis seed leaves a residual rotation unconstrained on inertia-
# degenerate parts (two equal moments → rotation about the symmetry axis is
# free) and a discrete ambiguity on globally-symmetric parts. ICP over the
# tessellated surfaces locks that residual. We REUSE geometry_deviation's
# _TriGrid spatial hash + exact closest-point-on-triangle (no new dep, no
# kd-tree) so each ICP correspondence is a true point-on-surface, not a
# vertex-to-vertex nearest neighbour. TIME-BOXED (cap iterations + wall clock —
# the KR600 900s lesson) so a pathological mesh can never hang the pipeline.

_ICP_MAX_POINTS = 800       # B surface points carried through the ICP loop
_ICP_MAX_ITERS = 12         # hard iteration cap
_ICP_WALL_S = 4.0           # hard wall-clock cap (seconds)
_ICP_CONVERGE = 1e-4        # stop when the per-iter rmsd improvement is tiny


def _closest_points_on_mesh(
    pts: np.ndarray, grid, tris: np.ndarray, verts: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """For each row of ``pts`` return (closest_point_on_mesh, sq_distance).

    Reuses geometry_deviation._TriGrid + _point_triangle_sq_dist: query the
    27-cell neighbourhood of each point's cell and take the nearest triangle's
    exact closest point. Empty neighbourhoods fall back to the nearest mesh
    vertex (so a sparse-mesh region still yields a correspondence).
    """
    from phone_designer.skills.inspect.geometry_deviation import (
        _point_triangle_sq_dist,
    )

    out_pt = np.empty_like(pts)
    out_sq = np.empty(len(pts), dtype=float)
    keys = np.floor((pts - grid.origin) / grid.cell).astype(np.int64)
    uniq, inv, counts = np.unique(
        keys, axis=0, return_inverse=True, return_counts=True)
    order = np.argsort(inv, kind="stable")
    bounds = np.concatenate([[0], np.cumsum(counts)])
    fallback: list[int] = []
    for u in range(len(uniq)):
        sel = order[bounds[u]:bounds[u + 1]]
        cand = grid.candidates(tuple(int(x) for x in uniq[u]))
        if len(cand) == 0:
            fallback.extend(int(i) for i in sel)
            continue
        tri = tris[cand]
        sub = pts[sel]
        sq = _point_triangle_sq_dist(sub, tri[:, 0], tri[:, 1], tri[:, 2])
        nearest = np.argmin(sq, axis=1)
        # Recompute the closest point for the winning triangle per point.
        a = tri[nearest, 0]
        b = tri[nearest, 1]
        c = tri[nearest, 2]
        for li, gi in enumerate(sel):
            cp = _closest_on_triangle(sub[li], a[li], b[li], c[li])
            out_pt[gi] = cp
            out_sq[gi] = float(np.sum((sub[li] - cp) ** 2))
    if fallback:
        fb = np.asarray(fallback, dtype=np.int64)
        for start in range(0, len(fb), 256):
            ch = fb[start:start + 256]
            diff = pts[ch][:, None, :] - verts[None, :, :]
            d2 = (diff * diff).sum(-1)
            j = np.argmin(d2, axis=1)
            out_pt[ch] = verts[j]
            out_sq[ch] = d2[np.arange(len(ch)), j]
    return out_pt, out_sq


def _closest_on_triangle(
    p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray
) -> np.ndarray:
    """Closest point on a single triangle (Ericson RTCD §5.1.5, scalar)."""
    ab = b - a
    ac = c - a
    ap = p - a
    d1 = float(ab @ ap)
    d2 = float(ac @ ap)
    if d1 <= 0 and d2 <= 0:
        return a
    bp = p - b
    d3 = float(ab @ bp)
    d4 = float(ac @ bp)
    if d3 >= 0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3) if (d1 - d3) != 0 else 0.0
        return a + v * ab
    cp = p - c
    d5 = float(ab @ cp)
    d6 = float(ac @ cp)
    if d6 >= 0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6) if (d2 - d6) != 0 else 0.0
        return a + w * ac
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        denom = (d4 - d3) + (d5 - d6)
        w = (d4 - d3) / denom if denom != 0 else 0.0
        return b + w * (c - b)
    denom = va + vb + vc
    if abs(denom) < 1e-18:
        return a
    v = vb / denom
    w = vc / denom
    return a + ab * v + ac * w


def _icp_refine(
    R0: np.ndarray,
    t0: np.ndarray,
    b_local: np.ndarray,
    cb: np.ndarray,
    grid,
    a_tris: np.ndarray,
    a_verts: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float, int]:
    """Point-to-point ICP that locks the residual rotation of a coarse seat.

    Seeds from (R0, t0) — the principal/orientation estimate mapping a
    centroid-relative B point ``q`` to A's frame as ``R0 @ q + (t0 + R0@cb)``.
    Each iteration: map B's surface points through the current (R, t), find the
    closest point on A's mesh (reused _TriGrid closest-point-on-triangle),
    Kabsch-fit B's ORIGINAL points onto those correspondences, repeat. The fit
    is always solved against the originals so it recovers the full B→A motion
    (no compounding drift). Returns (R, t, rmsd_mm, iters_run). Time-boxed.
    """
    import time as _time

    # Work in world B coordinates so the returned (R,t) maps a B point p_b to A:
    #   p_a = R @ p_b + t.  Seed (R0, t0) already maps world-B onto A.
    P = b_local + cb                       # original world-frame B points
    R = R0.copy()
    t = t0.copy()
    prev = float("inf")
    deadline = _time.perf_counter() + _ICP_WALL_S
    iters = 0
    for it in range(_ICP_MAX_ITERS):
        iters = it + 1
        mapped = (R @ P.T).T + t
        corr, sq = _closest_points_on_mesh(mapped, grid, a_tris, a_verts)
        rmsd = float(np.sqrt(np.mean(sq))) if len(sq) else float("inf")
        Rf, tf, _r = _kabsch(P, corr)
        R, t = Rf, tf
        if prev - rmsd < _ICP_CONVERGE * max(1.0, prev):
            # Re-evaluate residual at the just-updated transform before exit.
            mapped = (R @ P.T).T + t
            _c, sq2 = _closest_points_on_mesh(mapped, grid, a_tris, a_verts)
            rmsd = float(np.sqrt(np.mean(sq2))) if len(sq2) else rmsd
            return R, t, rmsd, iters
        prev = rmsd
        if _time.perf_counter() > deadline:
            mapped = (R @ P.T).T + t
            _c, sq2 = _closest_points_on_mesh(mapped, grid, a_tris, a_verts)
            rmsd = float(np.sqrt(np.mean(sq2))) if len(sq2) else rmsd
            return R, t, rmsd, iters
    mapped = (R @ P.T).T + t
    _c, sq2 = _closest_points_on_mesh(mapped, grid, a_tris, a_verts)
    rmsd = float(np.sqrt(np.mean(sq2))) if len(sq2) else prev
    return R, t, rmsd, iters


def _proper_rotations_between_frames(
    Ra: np.ndarray, Rb: np.ndarray
) -> list[np.ndarray]:
    """Enumerate the proper-rotation candidates mapping frame B onto frame A.

    Ra, Rb are 3x3 with eigenvectors as COLUMNS. A rotation that takes B's
    eigen-frame onto A's is ``R = Ra @ S @ Rb.T`` where S is a signed
    permutation of axes (sign flips + axis swaps). Only S with det(S)=+1 that
    keep R a proper rotation (det=+1) are kept → the classic 24 orientations
    of an octahedral frame.
    """
    out: list[np.ndarray] = []
    seen: set[bytes] = set()
    for perm in itertools.permutations(range(3)):
        P = np.zeros((3, 3))
        for col, row in enumerate(perm):
            P[row, col] = 1.0
        for signs in itertools.product((1.0, -1.0), repeat=3):
            S = P * np.array(signs)[None, :]
            if round(float(np.linalg.det(S))) != 1:
                continue
            R = Ra @ S @ Rb.T
            if abs(float(np.linalg.det(R)) - 1.0) > 1e-6:
                continue
            key = np.round(R, 6).tobytes()
            if key in seen:
                continue
            seen.add(key)
            out.append(R)
    return out


def _homogeneous(R: np.ndarray, t: np.ndarray) -> list[list[float]]:
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = t
    return [[float(v) for v in row] for row in M]


def _bbox_diag(pts: np.ndarray) -> float:
    """Bounding-box diagonal of a point cloud (mm). Scale-free yardstick for
    the residual / confidence downgrade. 0.0 on an empty cloud."""
    if pts is None or len(pts) == 0:
        return 0.0
    span = pts.max(axis=0) - pts.min(axis=0)
    return float(np.linalg.norm(span))


def _tessellated_copy(shape, deflection_mm: float = 0.5):
    """Return a DEEP COPY of ``shape`` carrying its own triangulation.

    OCCT stores triangulation on the TopoDS faces, so meshing the shared
    reference shape in place would corrupt any cached mesh a later compare_parts
    stage relies on (the near-symmetric linkrods A-vs-A regression). We copy
    first (BRepBuilderAPI_Copy) and mesh the copy, leaving the caller's shape
    untouched. Coarser default deflection than geometry_deviation — ICP only
    needs a faithful surface sample, and a coarser mesh keeps the closest-point
    queries cheap.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepMesh import BRepMesh_IncrementalMesh

    copy = BRepBuilderAPI_Copy(shape)
    copy.Perform(shape)
    shp = copy.Shape()
    mesher = BRepMesh_IncrementalMesh(shp, float(deflection_mm), False, 0.5, True)
    mesher.Perform()
    return shp


# Cap on mesh points used to score candidate orientations — keeps the 24-way
# nearest-neighbour scan cheap while still covering the surface.
_SCORE_MAX_POINTS = 600


def _principal_register(
    shape_b, shape_a
):
    """Coarse principal-axis registration B -> A.

    Returns a dict with keys ``R, t, rmsd, axis_ambiguous, ca, cb, candidates,
    a_verts, b_local, scored``. ``R, t`` map a B-frame point p to A-frame:
    ``p_a = R @ p_b + t``. ``candidates`` is the full best-first list of
    (R_i, t_i) for all proper orientations so a caller can disambiguate a
    near-symmetric part by feature pairing or by ICP+hausdorff multi-start.

    The eigenvector sign/permutation is resolved by enumerating the 24 proper
    rotations (sign × permutation, det=+1) that take B's eigen-frame onto A's
    and picking the one minimizing a CHEAP point-set residual: subsampled
    B mesh vertices, mapped through (R, t), against subsampled A mesh
    vertices (nearest-neighbour RMSD). The returned ``rmsd`` is that true
    geometric residual in mm. ``a_verts`` / ``b_local`` (the subsampled A
    cloud and centroid-relative B cloud) are returned so an ICP refine can run
    without re-tessellating.
    """
    ca, va, Va = _mass_frame(shape_a)
    cb, vb, Vb = _mass_frame(shape_b)
    ambiguous = _is_ambiguous(va) or _is_ambiguous(vb)

    a_verts = _subsample(_mesh_vertices(shape_a), _SCORE_MAX_POINTS)
    b_verts_all = _mesh_vertices(shape_b)
    b_verts = _subsample(b_verts_all, _SCORE_MAX_POINTS)
    b_local = b_verts - cb  # centroid-relative B points

    cands = _proper_rotations_between_frames(Va, Vb)
    if not cands:
        cands = [np.eye(3)]

    # Score every candidate orientation by the point-set residual; sort so the
    # Stage-2 feature search (caller) can walk the best-first.
    scored: list[tuple[float, np.ndarray]] = []
    for R in cands:
        mapped = (R @ b_local.T).T + ca
        scored.append((_nn_rmsd(mapped, a_verts), R))
    scored.sort(key=lambda x: x[0])

    best_res, best_R = scored[0]
    t = ca - best_R @ cb
    candidates = [(R_i, ca - R_i @ cb) for _res, R_i in scored]
    return {
        "R": best_R, "t": t, "rmsd": best_res, "axis_ambiguous": ambiguous,
        "ca": ca, "cb": cb, "candidates": candidates,
        "a_verts": a_verts, "b_local": b_local, "scored": scored,
    }


def _kabsch(P: np.ndarray, Q: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Umeyama/Kabsch rigid fit mapping rows of P onto rows of Q.

    Returns (R, t, rmsd) with  Q ≈ (R @ P.T).T + t. Pure rotation (det=+1
    enforced via the reflection-correcting sign on the smallest singular
    vector). Requires len(P) == len(Q) >= 3.
    """
    Pc = P.mean(axis=0)
    Qc = Q.mean(axis=0)
    P0 = P - Pc
    Q0 = Q - Qc
    H = P0.T @ Q0
    U, _S, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1.0, 1.0, d])
    R = Vt.T @ D @ U.T
    t = Qc - R @ Pc
    mapped = (R @ P.T).T + t
    rmsd = float(np.sqrt(np.mean(np.sum((mapped - Q) ** 2, axis=1))))
    return R, t, rmsd


def _centroid_of(entry: dict) -> np.ndarray | None:
    """Pull a 3-vector position from a catalog entry (same key chain as
    feature_fidelity_diff._xyz_of, reused via that module)."""
    from phone_designer.skills.reverse_engineer.feature_fidelity_diff import _xyz_of

    xyz = _xyz_of(entry) if isinstance(entry, dict) else None
    if xyz is None:
        return None
    return np.array(xyz, dtype=float)


def _matched_centroid_pairs(
    cat_a: dict,
    cat_b: dict,
    R_coarse: np.ndarray | None = None,
    t_coarse: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Gather (P_b, Q_a) matched feature centroids across all kinds using
    feature_fidelity_diff._greedy_pair (imported, NOT forked).

    ``R_coarse``/``t_coarse`` (the Stage-1 principal estimate) pre-map B's
    centroids into A's frame ONLY for the pairing gate — the returned P_b are
    B's ORIGINAL centroids so the subsequent Kabsch fit recovers the full
    B->A transform, not a residual on top of the coarse one.

    Returns (B_centroids (N,3), A_centroids (N,3)) for pairs where both sides
    expose a position.
    """
    from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
        _KINDS,
    )

    pb: list[np.ndarray] = []
    qa: list[np.ndarray] = []
    for kind in _KINDS:
        a_list = cat_a.get(kind) or []
        b_list = cat_b.get(kind) or []
        if not a_list or not b_list:
            continue
        # Build a B list whose positional centroid is the coarse-mapped one so
        # the spatial gate fires in A's frame; keep originals for the fit.
        if R_coarse is not None and t_coarse is not None:
            b_mapped = []
            for e in b_list:
                c = _centroid_of(e) if isinstance(e, dict) else None
                if c is None:
                    b_mapped.append(e)
                    continue
                cm = R_coarse @ c + t_coarse
                em = dict(e)
                em["_reg_centroid"] = [float(cm[0]), float(cm[1]), float(cm[2])]
                b_mapped.append(em)
            pair_b_list = b_mapped
        else:
            pair_b_list = b_list
        pairs, _ua, _ub = _greedy_pair_with_override(
            a_list, pair_b_list, kind)
        for ai, bi, _cost in pairs:
            if ai >= len(a_list) or bi >= len(b_list):
                continue
            qa_c = _centroid_of(a_list[ai])
            pb_c = _centroid_of(b_list[bi])  # ORIGINAL B centroid
            if qa_c is None or pb_c is None:
                continue
            qa.append(qa_c)
            pb.append(pb_c)
    if not pb:
        return np.empty((0, 3)), np.empty((0, 3))
    return np.array(pb), np.array(qa)


def _greedy_pair_with_override(a_list, b_list, kind):
    """Wrapper around feature_fidelity_diff._greedy_pair that lets a B entry
    carry a ``_reg_centroid`` (coarse-mapped position) consulted FIRST by the
    spatial gate. We do not fork _greedy_pair — we substitute a thin b list
    whose ``entry_origin`` is the mapped centroid for gating purposes, while
    callers read the original entries by index afterward.
    """
    from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
        _greedy_pair,
    )

    if not any(isinstance(e, dict) and "_reg_centroid" in e for e in b_list):
        return _greedy_pair(a_list, b_list, kind)
    # Replace each b entry's leading positional key with the mapped centroid so
    # _xyz_of (entry_origin first) reads the A-frame position. Preserve the
    # primary-dim fields untouched so the dim gate still works.
    gate_b = []
    for e in b_list:
        if isinstance(e, dict) and "_reg_centroid" in e:
            eg = dict(e)
            eg["entry_origin"] = e["_reg_centroid"]
            gate_b.append(eg)
        else:
            gate_b.append(e)
    return _greedy_pair(a_list, gate_b, kind)


def _rank3(pts: np.ndarray) -> bool:
    """True when the point cloud spans 3 dimensions (non-collinear,
    non-coplanar-degenerate enough for a stable rotation fit)."""
    if len(pts) < 3:
        return False
    c = pts - pts.mean(axis=0)
    sv = np.linalg.svd(c, compute_uv=False)
    if sv[0] < 1e-9:
        return False
    # Two independent directions are enough to pin a rotation in 3D when
    # combined with the centroid; require the 2nd singular value to be
    # non-trivial relative to the 1st.
    return bool(sv[1] / sv[0] > 1e-3)


@skill(
    name="register_bodies",
    category="inspect",
    level="atomic",
    summary="Best-fit RIGID transform mapping the current body B onto a "
            "reference A (STEP or body). Stage 1 principal-axis alignment "
            "(24-orientation disambiguation), Stage 2 ICP + symmetry multi-"
            "start over the tessellated surfaces (locks the residual rotation "
            "on inertia-degenerate / symmetric parts, min-hausdorff seating), "
            "optional Stage 2 feature-centroid Kabsch refine. Emits the 4x4 "
            "that geometry_deviation(align='rigid') consumes plus a "
            "registration_confidence (high/medium/low) — never a confidently "
            "WRONG transform on an ambiguous part. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["registration"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.step_read_failed"],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class RegisterBodies(SkillBase):
    class Args(BaseModel):
        reference_step_path: str | None = Field(
            default=None,
            description="Reference solid A as a STEP file. Either this or "
                        "reference_shape must be given.",
        )
        reference_shape: Any | None = Field(
            default=None,
            description="Reference solid A as a build123d Part / OCCT shape "
                        "(in-memory alternative to reference_step_path).",
        )
        catalog_a: dict | None = Field(
            default=None,
            description="Optional feature catalog for the REFERENCE (A). When "
                        "both catalogs are present a Stage-2 feature-centroid "
                        "Kabsch refine runs on top of the principal estimate.",
        )
        catalog_b: dict | None = Field(
            default=None,
            description="Optional feature catalog for the CURRENT body (B).",
        )
        icp_refine: bool = Field(
            default=True,
            description="phase-4 (2026-06-15): run the ICP + symmetry multi-"
                        "start refine over tessellated surfaces. Seeds from the "
                        "principal candidates, locks the residual rotation on "
                        "inertia-degenerate / symmetric parts, and picks the "
                        "min-hausdorff seating. Time-boxed. Set False to keep "
                        "the legacy principal/feature-only behaviour.",
        )

        model_config = {"arbitrary_types_allowed": True}

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("register_bodies: body is None")
        shape_b = _occt_shape(body)

        if args.reference_shape is not None:
            shape_a = _occt_shape(args.reference_shape)
        elif args.reference_step_path:
            shape_a = _load_step(args.reference_step_path)
        else:
            raise ValueError(
                "register_bodies: reference_step_path or reference_shape "
                "is required")

        # ── Stage 1: principal-axis coarse registration ───────────────────
        pr = _principal_register(shape_b, shape_a)
        R, t, rmsd = pr["R"], pr["t"], pr["rmsd"]
        ambiguous = pr["axis_ambiguous"]
        ca, cb, candidates = pr["ca"], pr["cb"], pr["candidates"]
        a_verts, b_local, scored = pr["a_verts"], pr["b_local"], pr["scored"]
        method = "principal"

        # Reference bbox diagonal — the scale-free yardstick for the residual
        # and the confidence downgrade (a 1mm error means very different things
        # on a 3mm chip vs a 600mm robot arm — the KR600 lesson).
        diag = _bbox_diag(a_verts)

        # bbox_fallback: a fully isotropic body yields no usable rotation —
        # _is_ambiguous(span==0) flags it; degrade to pure translation.
        _cb_c, vb_vals, _vb_vecs = _mass_frame(shape_b)
        isotropic = float(vb_vals.max() - vb_vals.min()) <= 1e-9
        if isotropic:
            R = np.eye(3)
            t = ca - cb
            rmsd = float(np.linalg.norm((R @ cb + t) - ca))
            method = "bbox_fallback"
            candidates = [(R, t)]

        # Build A's surface grid ONCE (shared by ICP + the feature geometric
        # gate). Mesh a COPY of A so the shared reference's cached triangulation
        # is never mutated (the near-symmetric linkrods A-vs-A regression).
        grid = None
        a_tris = a_av = None
        b_pts = _subsample(b_local, _ICP_MAX_POINTS)
        if not isotropic and len(a_verts) and len(b_local):
            try:
                from phone_designer.skills.inspect.geometry_deviation import (
                    _TriGrid,
                    _triangle_soup,
                )
                a_copy = _tessellated_copy(shape_a)
                a_av, a_tris = _triangle_soup(a_copy)
                grid = _TriGrid(a_tris)
            except Exception:
                grid = None

        def _geom_residual(Rc: np.ndarray, tc: np.ndarray) -> float:
            """True closest-point-on-A residual (mm) of a candidate seating —
            the SAME geometry the hausdorff measures. Used to rank seatings so
            we never adopt a worse seat just because a later stage 'trusts'
            itself. inf when no grid (caller falls back to the stage rmsd)."""
            if grid is None or a_tris is None or a_av is None:
                return float("inf")
            mapped = (Rc @ (b_pts + cb).T).T + tc
            _cp, sq = _closest_points_on_mesh(mapped, grid, a_tris, a_av)
            return float(np.sqrt(np.mean(sq))) if len(sq) else float("inf")

        # ── Stage 2a: ICP + symmetry multi-start over the surfaces ─────────
        # The principal seed leaves the residual rotation FREE on inertia-
        # degenerate parts and a discrete ambiguity on symmetric parts, so a
        # single coarse pick can be confidently WRONG. We ICP-refine the top-K
        # coarse seatings (multi-start over the symmetry hypotheses already
        # enumerated as the 24 octahedral orientations + the principal best)
        # and KEEP THE MIN-RESIDUAL one, measured on the true closest-point-on-
        # surface ICP residual — geometry, not match_ratio. Time-boxed.
        icp_best_rmsd: float | None = None
        if args.icp_refine and grid is not None:
            try:
                # Multi-start: the principal best + the next few candidate
                # seatings (covers the 90/180-deg symmetry flips the inertia
                # tensor cannot distinguish).
                n_start = 6 if ambiguous else 3
                seeds = [Ri for _res, Ri in scored[:n_start]]
                best: tuple[float, np.ndarray, np.ndarray] | None = None
                for R_seed in seeds:
                    t_seed = ca - R_seed @ cb
                    Rr, tr, r_icp, _it = _icp_refine(
                        R_seed, t_seed, b_pts, cb, grid, a_tris, a_av)
                    if best is None or r_icp < best[0]:
                        best = (r_icp, Rr, tr)
                if best is not None and math.isfinite(best[0]):
                    icp_best_rmsd = best[0]
                    # Adopt the ICP seating only when it does not REGRESS the
                    # coarse residual (a degenerate ICP on a bad seed could
                    # drift); on a clean prismatic part this is a near no-op.
                    if best[0] <= rmsd + 1e-6:
                        R, t, rmsd = best[1], best[2], best[0]
                        method = "icp" if method != "bbox_fallback" else method
            except Exception:
                # Honest fallback: keep the principal seat; ICP is best-effort.
                icp_best_rmsd = None

        # ── Stage 2b: feature-centroid Kabsch refine (when both catalogs) ──
        # Each coarse candidate (R_i, t_i) pre-maps B's centroids into A's
        # frame for the pairing gate only; the Kabsch fit then solves the FULL
        # B->A rigid transform from the original matched centroids. For a
        # near-symmetric part (axis_ambiguous) the best point-set candidate may
        # be the WRONG orientation, so we walk the best-first candidate list
        # and keep the orientation that yields the most matched feature pairs —
        # the features break the symmetry the inertia tensor cannot.
        #
        # phase-4 (2026-06-15): the feature fit is adopted only when it does NOT
        # materially REGRESS the geometric residual the ICP/principal stage
        # already achieved. A corrupted catalog (e.g. jittered centroids) used
        # to confidently override a good ICP seat with a worse transform; we now
        # GEOMETRICALLY verify the feature seat and keep the better one — the
        # same min-residual discipline ICP uses. The feature fit still WINS when
        # it breaks a symmetry the geometry alone cannot (its residual is then
        # comparable-or-better).
        if args.catalog_a and args.catalog_b:
            best_fit: tuple[np.ndarray, np.ndarray, float, int] | None = None
            for R_i, t_i in candidates:
                P_b, Q_a = _matched_centroid_pairs(
                    args.catalog_a, args.catalog_b,
                    R_coarse=R_i, t_coarse=t_i)
                n = len(P_b)
                if n >= 3 and _rank3(P_b) and _rank3(Q_a):
                    Rf, tf, rmsd_f = _kabsch(P_b, Q_a)
                    # Prefer more matched pairs; break ties by lower residual.
                    if (best_fit is None or n > best_fit[3]
                            or (n == best_fit[3] and rmsd_f < best_fit[2])):
                        best_fit = (Rf, tf, rmsd_f, n)
            if best_fit is not None:
                Rf, tf, rmsd_f, _nf = best_fit
                # Geometric residual of the feature seat vs the incumbent seat.
                geom_feat = _geom_residual(Rf, tf)
                geom_curr = _geom_residual(R, t)
                # Adopt the feature fit unless it geometrically REGRESSES the
                # incumbent by a meaningful margin (tol relative to bbox diag).
                tol = max(1e-6, 0.01 * diag)
                if (not math.isfinite(geom_feat) or not math.isfinite(geom_curr)
                        or geom_feat <= geom_curr + tol):
                    R, t = Rf, tf
                    rmsd = (geom_feat if math.isfinite(geom_feat) else rmsd_f)
                    method = "feature"

        # ── Confidence + the HONEST downgrade ─────────────────────────────
        # Residual normalised by the reference bbox diagonal: a part is only
        # well-seated when BOTH some seating achieves a small residual AND
        # (for an ambiguous part) the residual genuinely resolved the symmetry.
        # When the best achievable residual is still large relative to the part
        # we DOWNGRADE rather than return a confidently-wrong transform.
        rmsd_norm = (float(rmsd) / diag) if diag > 1e-9 else None

        # phase-4 (2026-06-15): tiers tuned to the tessellation chord error —
        # a clean prismatic recovery lands well under 0.5% of the diagonal.
        if method == "feature":
            conf = float(max(0.0, min(1.0, 1.0 - rmsd / 5.0)))
            conf = max(conf, 0.5)
        elif method == "bbox_fallback":
            conf = 0.2
        else:
            conf = float(max(0.0, min(1.0, 1.0 - rmsd / 10.0)))
            if ambiguous:
                conf *= 0.5

        # Discrete confidence label — the field compare_parts / similarity flag
        # a low-confidence diff on. 'low' on ambiguous parts whose best seating
        # still leaves a large normalised residual (the ICP could not lock the
        # rotation); 'high' on a crisp resolvable seat.
        if rmsd_norm is None:
            registration_confidence = "low"
        elif method == "feature":
            registration_confidence = "high" if rmsd_norm < 0.02 else "medium"
        elif method == "bbox_fallback":
            registration_confidence = "low"
        elif rmsd_norm < 0.01:
            registration_confidence = "high"
        elif rmsd_norm < 0.05:
            registration_confidence = "medium" if not ambiguous else "low"
        else:
            registration_confidence = "low"

        if registration_confidence == "low":
            conf = min(conf, 0.3)

        registration = {
            "transform_4x4": _homogeneous(R, t),
            "rmsd_mm": round(float(rmsd), 6),
            "rmsd_norm": round(float(rmsd_norm), 6) if rmsd_norm is not None
            else None,
            "method": method,
            "axis_ambiguous": bool(ambiguous),
            "registration_confidence": registration_confidence,
            "confidence": round(float(conf), 4),
            "icp_rmsd_mm": (round(float(icp_best_rmsd), 6)
                            if icp_best_rmsd is not None else None),
            "centroid_a": [round(float(v), 6) for v in ca],
            "centroid_b": [round(float(v), 6) for v in cb],
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"registration": registration},
        )
