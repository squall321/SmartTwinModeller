"""_section_curve — shared section-curve recovery helper (phase-0, 2026-06-13).

Recover a body's planar cross-section as ONE ordered closed loop and emit an
EXECUTABLE sketch dict that the modify_pocket / loft skills can consume directly
(round-trip: section a solid → sketch → extrude → prism matches height·area).

Pipeline
--------
1. ``BRepAlgoAPI_Section`` at the plane — the SAME machinery as
   ``inspect/cross_section.py`` (BRepAlgoAPI_Section + ``_all_edges``). The
   section edges live in 3D ON the cutting plane.
2. Project every section vertex into the plane's orthonormal in-plane frame
   ``(u, v)`` (Gram-Schmidt off ``plane_normal``) so the loop is planar 2D.
   ``recover_section_loop`` walks the shared section vertices to stitch the
   edges into ordered closed loops; if several disjoint loops exist it returns
   the LARGEST by |signed area| and notes the rest in ``n_loops``.
3. Simplify: each section edge is classified straight vs arc via
   ``BRepAdaptor_Curve.GetType()``. Consecutive straight edges are flattened to
   a polyline and Douglas-Peucker-decimated (``simplify_tol_mm``); arc / circle
   / spline edges are preserved as arc / spline segments.
4. Emit an EXECUTABLE sketch dict consumable by ``modify_pocket._sketch`` —
   ``PolygonSketch`` when the loop is all-straight, a ``CompositeSketch`` of
   line/arc/spline segments when it mixes straight + curved runs, and a
   ``BSplineSketch`` (``fit_points``) for a single smooth closed curve (e.g. a
   cylinder section circle). The emitted coordinates are absolute 2D in-plane
   coords (``center_x_mm`` / ``center_y_mm`` left at 0 so the wire builder adds
   nothing).

Guarded by ``DEFAULT_MAX_FACE_COUNT`` (``_face_count_guard``) — a 4000-face
KR600 section must not hang. The same pattern as ``classify_pockets``: when the
input shape has more than ``max_face_count`` faces the helper returns an empty
result with ``skipped: True`` instead of running ``BRepAlgoAPI_Section``.

NOTE (phase-0): standalone read-only helper. It does NOT modify
``cross_section.py`` or ``_sketch.py`` — it imports / reuses only.
"""
from __future__ import annotations

import math
from typing import Any

from phone_designer.skills._face_count_guard import DEFAULT_MAX_FACE_COUNT

# In-plane stitch / dedup tolerance. EPS_CLOSE in _sketch.py is 1e-3 (1µm) for
# segment-endpoint matching; section vertices snapped from OCCT carry a bit
# more numeric noise, so the loop walker uses a looser 1e-4..1e-3 band and the
# emitted segments are rounded to 4 decimals to land inside _sketch's EPS_CLOSE.
_STITCH_TOL = 1.0e-3          # mm — shared-vertex match when walking the loop
_DEDUP_TOL = 1.0e-4           # mm — collapse coincident consecutive points
_ROUND = 4                    # decimal places for emitted coords (matches _sketch)
_DEFAULT_SIMPLIFY_TOL = 0.01  # mm — Douglas-Peucker tol on straight polylines


# ──────────────────────────────────────────────────────────────────────────────
# OCCT helpers (reuse cross_section.py conventions)


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _inplane_frame(
    n: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Right-handed orthonormal (u, v) in the plane with normal ``n``.

    SAME Gram-Schmidt convention as classify_pockets._fp_inplane_frame and
    extrude_pocket_world (project world +X, or +Y when n is X-dominant) so the
    recovered 2D frame is consistent with the cutting-tool frame.
    """
    nx, ny, nz = n
    if abs(nx) < 0.9:
        ref = (1.0, 0.0, 0.0)
    else:
        ref = (0.0, 1.0, 0.0)
    dot = ref[0] * nx + ref[1] * ny + ref[2] * nz
    ux, uy, uz = ref[0] - dot * nx, ref[1] - dot * ny, ref[2] - dot * nz
    umag = math.sqrt(ux * ux + uy * uy + uz * uz)
    if umag < 1e-12:
        ux, uy, uz, umag = 0.0, 1.0, 0.0, 1.0
    u = (ux / umag, uy / umag, uz / umag)
    v = (
        ny * u[2] - nz * u[1],
        nz * u[0] - nx * u[2],
        nx * u[1] - ny * u[0],
    )
    return u, v


def _curve_kind(edge) -> str:
    """line / circle / ellipse / bspline / other — via BRepAdaptor_Curve."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import (
        GeomAbs_BSplineCurve,
        GeomAbs_BezierCurve,
        GeomAbs_Circle,
        GeomAbs_Ellipse,
        GeomAbs_Line,
    )

    t = BRepAdaptor_Curve(edge).GetType()
    if t == GeomAbs_Line:
        return "line"
    if t == GeomAbs_Circle:
        return "circle"
    if t == GeomAbs_Ellipse:
        return "ellipse"
    if t in (GeomAbs_BSplineCurve, GeomAbs_BezierCurve):
        return "bspline"
    return "other"


def _sample_edge_3d(edge, samples: int) -> list[tuple[float, float, float]]:
    """Uniform-arclength samples of ``edge`` in 3D (cross_section.py logic)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_UniformAbscissa

    curve = BRepAdaptor_Curve(edge)
    n = max(2, int(samples))
    pts: list[tuple[float, float, float]] = []
    try:
        algo = GCPnts_UniformAbscissa(curve, n)
        if algo.IsDone() and algo.NbPoints() >= 2:
            for i in range(1, algo.NbPoints() + 1):
                p = curve.Value(algo.Parameter(i))
                pts.append((p.X(), p.Y(), p.Z()))
            return pts
    except Exception:
        pts = []
    u0, u1 = curve.FirstParameter(), curve.LastParameter()
    if u1 <= u0:
        return []
    for i in range(n):
        p = curve.Value(u0 + (u1 - u0) * (i / (n - 1)))
        pts.append((p.X(), p.Y(), p.Z()))
    return pts


# ──────────────────────────────────────────────────────────────────────────────
# 2D geometry


def _shoelace_area(pts: list[tuple[float, float]]) -> float:
    """Signed polygon area (CCW positive)."""
    n = len(pts)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return 0.5 * a


def _dp_simplify(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker on an OPEN polyline (keeps both endpoints)."""
    if len(pts) <= 2 or tol <= 0.0:
        return list(pts)

    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i0, i1 = stack.pop()
        ax, ay = pts[i0]
        bx, by = pts[i1]
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        dmax, imax = -1.0, -1
        for k in range(i0 + 1, i1):
            px, py = pts[k]
            if seg < 1e-12:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / seg
            if d > dmax:
                dmax, imax = d, k
        if imax >= 0 and dmax > tol:
            keep[imax] = True
            stack.append((i0, imax))
            stack.append((imax, i1))
    return [pts[i] for i in range(len(pts)) if keep[i]]


def _drop_collinear_closed(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Remove collinear vertices on a CLOSED loop (wrap-around aware).

    Douglas-Peucker on an open polyline pins both endpoints, so a collinear
    vertex that happens to land at the loop's start index survives (e.g. the
    seam vertex on a fused L-shape's straight wall). This pass treats the loop
    as cyclic and drops any vertex whose perpendicular distance to the chord
    through its two neighbours is ≤ ``tol`` — leaving only true corners.
    """
    if len(pts) <= 3 or tol < 0.0:
        return list(pts)
    work = list(pts)
    changed = True
    while changed and len(work) > 3:
        changed = False
        for i in range(len(work)):
            a = work[i - 1]
            b = work[i]
            c = work[(i + 1) % len(work)]
            dx, dy = c[0] - a[0], c[1] - a[1]
            seg = math.hypot(dx, dy)
            if seg < 1e-12:
                continue
            d = abs(dy * b[0] - dx * b[1] + c[0] * a[1] - c[1] * a[0]) / seg
            if d <= tol:
                work.pop(i)
                changed = True
                break
    return work


def _dedup(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in pts:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > tol:
            out.append(p)
    # close-wrap: drop a final point coincident with the first
    if len(out) >= 2 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out


# ──────────────────────────────────────────────────────────────────────────────
# loop stitching


class _Seg:
    """One section edge as a 2D ordered point list + its curve kind."""

    __slots__ = ("pts", "kind")

    def __init__(self, pts: list[tuple[float, float]], kind: str):
        self.pts = pts
        self.kind = kind

    @property
    def a(self) -> tuple[float, float]:
        return self.pts[0]

    @property
    def b(self) -> tuple[float, float]:
        return self.pts[-1]

    def reversed(self) -> "_Seg":
        return _Seg(list(reversed(self.pts)), self.kind)


def _stitch_loops(segs: list[_Seg], tol: float) -> list[list[_Seg]]:
    """Walk shared endpoints to chain ``segs`` into ordered closed loops.

    Greedy nearest-endpoint chaining: start from an unused segment, then keep
    appending the segment whose free endpoint is within ``tol`` of the running
    chain's tail (flipping it if needed). When no continuation is found the
    chain is closed and a new one is seeded. Handles multiple disjoint loops.
    """
    def close(p: tuple[float, float], q: tuple[float, float]) -> bool:
        return math.hypot(p[0] - q[0], p[1] - q[1]) <= tol

    remaining = list(segs)
    loops: list[list[_Seg]] = []
    while remaining:
        chain = [remaining.pop(0)]
        extended = True
        while extended and remaining:
            extended = False
            tail = chain[-1].b
            best_i, best_seg = -1, None
            for i, s in enumerate(remaining):
                if close(tail, s.a):
                    best_i, best_seg = i, s
                    break
                if close(tail, s.b):
                    best_i, best_seg = i, s.reversed()
                    break
            if best_i >= 0:
                remaining.pop(best_i)
                chain.append(best_seg)
                extended = True
        loops.append(chain)
    return loops


def _loop_polyline(chain: list[_Seg], dedup_tol: float) -> list[tuple[float, float]]:
    """Flatten an ordered chain of segments to a single dense point loop."""
    pts: list[tuple[float, float]] = []
    for s in chain:
        for p in s.pts:
            if not pts or math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > dedup_tol:
                pts.append(p)
    return _dedup(pts, dedup_tol)


# ──────────────────────────────────────────────────────────────────────────────
# sketch emission


def _all_straight(chain: list[_Seg]) -> bool:
    return all(s.kind == "line" for s in chain)


def _emit_polygon(loop_pts: list[tuple[float, float]]) -> dict[str, Any]:
    verts = [(round(x, _ROUND), round(y, _ROUND)) for (x, y) in loop_pts]
    return {
        "kind": "polygon",
        "vertices": verts,
        "corner_fillet_r": 0.0,
        "center_x_mm": 0.0,
        "center_y_mm": 0.0,
    }


def _emit_bspline(loop_pts: list[tuple[float, float]]) -> dict[str, Any]:
    fit = [(round(x, _ROUND), round(y, _ROUND)) for (x, y) in loop_pts]
    return {
        "kind": "bspline_closed",
        "fit_points": fit,
        "degree": 3,
        "center_x_mm": 0.0,
        "center_y_mm": 0.0,
    }


def _emit_composite(chain: list[_Seg], simplify_tol: float) -> dict[str, Any]:
    """Mixed straight/curved loop → CompositeSketch (line/arc/spline segments).

    Straight runs are coalesced + Douglas-Peucker-simplified into LineSegments;
    circle/ellipse edges become 3-point arc-fitted SplineSegments (robust to
    OCCT arc-sense), bspline edges become SplineSegments through their samples.
    Endpoints are snapped so consecutive segments share an exact (rounded) point
    — _sketch.CompositeSketch enforces end==start within EPS_CLOSE.
    """
    def rnd(p):
        return (round(p[0], _ROUND), round(p[1], _ROUND))

    segments: list[dict[str, Any]] = []
    n = len(chain)
    i = 0
    while i < n:
        s = chain[i]
        if s.kind == "line":
            # coalesce a maximal straight run
            run = [s.a, s.b]
            j = i + 1
            while j < n and chain[j].kind == "line":
                run.append(chain[j].b)
                j += 1
            run = _dp_simplify(run, simplify_tol)
            for k in range(len(run) - 1):
                segments.append({
                    "kind": "line", "start": rnd(run[k]), "end": rnd(run[k + 1]),
                })
            i = j
        else:
            # curved edge — emit as an interpolating spline through its samples
            pts = [rnd(p) for p in s.pts]
            # ensure ≥2 distinct points
            clean = [pts[0]]
            for p in pts[1:]:
                if math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > _DEDUP_TOL:
                    clean.append(p)
            if len(clean) >= 2:
                segments.append({"kind": "spline", "points": clean})
            i += 1

    # snap chain so end[i] == start[i+1] and last.end == first.start (EPS_CLOSE)
    if segments:
        for k in range(len(segments)):
            cur = segments[k]
            nxt = segments[(k + 1) % len(segments)]
            cur_end = cur.get("end") or cur["points"][-1]
            if "start" in nxt:
                nxt["start"] = cur_end
            else:
                nxt["points"][0] = cur_end
    return {
        "kind": "composite",
        "segments": segments,
        "corner_fillet_r": 0.0,
        "center_x_mm": 0.0,
        "center_y_mm": 0.0,
    }


# ──────────────────────────────────────────────────────────────────────────────
# public API


def recover_section_loop(
    shape,
    plane_origin: tuple[float, float, float],
    plane_normal: tuple[float, float, float],
    *,
    max_face_count: int | None = DEFAULT_MAX_FACE_COUNT,
    simplify_tol_mm: float = _DEFAULT_SIMPLIFY_TOL,
    samples_per_arc: int = 24,
) -> dict[str, Any]:
    """Recover a body's planar cross-section as ONE closed loop + a sketch.

    Args:
        shape: build123d body OR raw TopoDS_Shape.
        plane_origin: section plane origin (x, y, z).
        plane_normal: section plane normal (need not be unit; must be non-zero).
        max_face_count: face-count guard (``_face_count_guard`` default). When
            the shape has more faces than this, the helper SKIPS the section and
            returns ``{"skipped": True, ...}`` — a 4000-face KR600 section must
            not hang. ``None`` disables the guard.
        simplify_tol_mm: Douglas-Peucker tolerance on straight polyline runs.
        samples_per_arc: sample count per curved section edge.

    Returns:
        {
          "sketch": <executable sketch dict | None>,   # polygon|composite|bspline_closed
          "area_mm2": float,                            # |signed area| of the loop
          "n_points": int,                              # vertices in the emitted loop
          "closed": bool,                               # loop closes within tol
          "n_loops": int,                               # disjoint loops found
          "loop_kind": "polygon"|"composite"|"bspline_closed"|None,
          ... ("skipped"/"reason"/"face_count" when the guard fires,
               "empty": True when the plane misses the body)
        }
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

    occ = _occt_shape(shape)

    nx, ny, nz = (float(plane_normal[0]), float(plane_normal[1]), float(plane_normal[2]))
    nmag = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nmag < 1e-9:
        raise ValueError("plane_normal must be non-zero")
    n_unit = (nx / nmag, ny / nmag, nz / nmag)

    # ── face-count guard (classify_pockets pattern) ────────────────────────────
    if max_face_count is not None:
        from phone_designer.skills._resolvers import _all_faces

        face_count = len(_all_faces(occ))
        if face_count > max_face_count:
            return {
                "sketch": None,
                "area_mm2": 0.0,
                "n_points": 0,
                "closed": False,
                "n_loops": 0,
                "loop_kind": None,
                "skipped": True,
                "reason": "too_big",
                "face_count": face_count,
                "limit": max_face_count,
            }

    # ── BRepAlgoAPI_Section (cross_section.py machinery) ───────────────────────
    origin = gp_Pnt(float(plane_origin[0]), float(plane_origin[1]), float(plane_origin[2]))
    plane = gp_Pln(origin, gp_Dir(*n_unit))
    section = BRepAlgoAPI_Section(occ, plane)
    section.ComputePCurveOn1(False)
    section.Approximation(False)
    section.Build()

    empty_result = {
        "sketch": None,
        "area_mm2": 0.0,
        "n_points": 0,
        "closed": False,
        "n_loops": 0,
        "loop_kind": None,
        "empty": True,
    }
    if not section.IsDone():
        return empty_result

    from phone_designer.skills._resolvers import _all_edges

    edges = _all_edges(section.Shape())
    if not edges:
        return empty_result

    # ── project section edges into the plane's (u, v) frame ────────────────────
    o = (origin.X(), origin.Y(), origin.Z())
    u, v = _inplane_frame(n_unit)

    def to2d(p3: tuple[float, float, float]) -> tuple[float, float]:
        dx, dy, dz = p3[0] - o[0], p3[1] - o[1], p3[2] - o[2]
        return (dx * u[0] + dy * u[1] + dz * u[2], dx * v[0] + dy * v[1] + dz * v[2])

    segs: list[_Seg] = []
    for e in edges:
        kind = _curve_kind(e)
        npt = 2 if kind == "line" else max(3, int(samples_per_arc))
        pts3 = _sample_edge_3d(e, npt)
        if len(pts3) < 2:
            continue
        pts2 = [to2d(p) for p in pts3]
        # collapse degenerate edges
        if math.hypot(pts2[0][0] - pts2[-1][0], pts2[0][1] - pts2[-1][1]) < _DEDUP_TOL and len(pts2) == 2:
            continue
        segs.append(_Seg(pts2, kind))

    if not segs:
        return empty_result

    # ── stitch + pick largest loop by |area| ───────────────────────────────────
    loops = _stitch_loops(segs, _STITCH_TOL)
    n_loops = len(loops)

    scored: list[tuple[float, list[_Seg], list[tuple[float, float]]]] = []
    for chain in loops:
        poly = _loop_polyline(chain, _DEDUP_TOL)
        scored.append((abs(_shoelace_area(poly)), chain, poly))
    scored.sort(key=lambda t: t[0], reverse=True)
    area, chain, loop_pts = scored[0]

    if len(loop_pts) < 3:
        return {**empty_result, "n_loops": n_loops}

    # CCW-orient the loop (positive signed area) so emitted sketches are
    # consistently wound — matches build123d/_sketch face-builder expectations.
    if _shoelace_area(loop_pts) < 0.0:
        loop_pts = [loop_pts[0]] + list(reversed(loop_pts[1:]))
        chain = [s.reversed() for s in reversed(chain)]

    closed = math.hypot(
        chain[0].a[0] - chain[-1].b[0], chain[0].a[1] - chain[-1].b[1]
    ) <= _STITCH_TOL

    # ── emit sketch ────────────────────────────────────────────────────────────
    if _all_straight(chain):
        simplified = _dp_simplify(loop_pts + [loop_pts[0]], simplify_tol_mm)
        # drop the duplicated wrap point
        if len(simplified) >= 2 and math.hypot(
            simplified[0][0] - simplified[-1][0], simplified[0][1] - simplified[-1][1]
        ) <= _DEDUP_TOL:
            simplified = simplified[:-1]
        # cyclic collinear cleanup — DP pins endpoints, so a collinear vertex
        # at the loop start (e.g. a fused-body seam on a straight wall) needs a
        # wrap-around pass to be removed.
        simplified = _drop_collinear_closed(simplified, simplify_tol_mm)
        if len(simplified) >= 3:
            sketch = _emit_polygon(simplified)
            loop_kind = "polygon"
            out_pts = simplified
        else:
            sketch = _emit_polygon(loop_pts)
            loop_kind = "polygon"
            out_pts = loop_pts
    elif len(chain) == 1 and chain[0].kind in ("circle", "ellipse", "bspline"):
        # single smooth closed curve (e.g. cylinder section circle) → bspline
        sketch = _emit_bspline(loop_pts)
        loop_kind = "bspline_closed"
        out_pts = loop_pts
    else:
        sketch = _emit_composite(chain, simplify_tol_mm)
        loop_kind = "composite"
        out_pts = loop_pts

    return {
        "sketch": sketch,
        "area_mm2": round(float(area), _ROUND),
        "n_points": len(out_pts),
        "closed": bool(closed),
        "n_loops": n_loops,
        "loop_kind": loop_kind,
    }
