"""detect_shell_holes — atomic, read-only.

Shell-aware hole detection. Where ``classify_holes`` requires a watertight
solid with analytic cylindrical faces to recognize a bore, ``detect_shell_holes``
works directly on an OPEN MESH-DERIVED SHELL. iPhone scans and other
photogrammetry / teardown meshes routinely come in as multi-shell BREPs whose
camera, button, port, and screw apertures appear as small closed boundary
LOOPS rather than analytic cylinders — ``classify_holes`` therefore reports
zero holes on them. This skill closes that gap.

Algorithm
---------
1. ``ShapeAnalysis_FreeBounds`` on the input shell collects every closed
   boundary wire. (An open wire — one whose first/last vertices differ — is
   not a hole and is ignored.)
2. For each closed wire we

   - sample N points along the wire,
   - compute the wire's planar normal from three well-spread sample points
     (cross product), then refine via least-squares orthogonal regression
     when ``N >= 4``,
   - project the samples onto that plane and fit the best circle
     (algebraic / Kasa) → (center, radius),
   - measure the maximum radial deviation of the samples from the fitted
     circle, and the maximum out-of-plane deviation. We call the wire
     "approximately circular" when

         max_radial_deviation / radius   <= axis_tolerance
         max_planarity_deviation / radius <= axis_tolerance

   - drop wires whose total arc-length is outside
     ``[min_perimeter_mm, max_perimeter_mm]`` (real cutouts on a phone are
     a few mm to a few cm; teardown openings — back glass missing — are
     hundreds of mm).

3. Surviving wires are reported under ``extras["shell_holes"]`` as

       {
           "idx": int,
           "center": [x, y, z],
           "axis":   [nx, ny, nz],            # unit length
           "diameter_mm":     float,
           "perimeter_mm":    float,
           "planarity_score": float,          # 0..1, 1 = perfectly planar
       }

Body is unchanged — ``post_conditions=[body_present]``.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# ──────────────────────────────────────────────────────────────────────────────
# Helpers


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _wire_sample_points(wire, n_samples: int = 64) -> list[tuple[float, float, float]]:
    """Sample N points along a wire by walking its edges in order and emitting
    GCPnts_UniformAbscissa samples per edge proportional to edge length."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.GCPnts import GCPnts_AbscissaPoint, GCPnts_UniformAbscissa

    edges: list[tuple[Any, float]] = []  # (BRepAdaptor_Curve, length)
    total_len = 0.0
    it = BRepTools_WireExplorer(wire)
    while it.More():
        e = it.Current()
        try:
            c = BRepAdaptor_Curve(e)
            L = float(GCPnts_AbscissaPoint.Length_s(c))
        except Exception:
            it.Next()
            continue
        if L > 0.0:
            edges.append((c, L))
            total_len += L
        it.Next()

    if total_len <= 0.0 or not edges:
        return []

    pts: list[tuple[float, float, float]] = []
    for c, L in edges:
        # Proportional allocation, at least 2 points per edge to capture endpoints
        k = max(2, int(round(n_samples * (L / total_len))))
        try:
            ua = GCPnts_UniformAbscissa(c, k)
            if not ua.IsDone() or ua.NbPoints() < 2:
                continue
            for i in range(1, ua.NbPoints() + 1):
                p = c.Value(ua.Parameter(i))
                pts.append((p.X(), p.Y(), p.Z()))
        except Exception:
            continue
    return pts


def _wire_perimeter_mm(wire) -> float:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.GCPnts import GCPnts_AbscissaPoint

    total = 0.0
    it = BRepTools_WireExplorer(wire)
    while it.More():
        e = it.Current()
        try:
            total += float(GCPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(e)))
        except Exception:
            pass
        it.Next()
    return total


def _vec_sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _vec_cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )
def _vec_dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _vec_norm(a): return math.sqrt(_vec_dot(a, a))
def _vec_unit(a):
    n = _vec_norm(a)
    if n < 1e-12:
        return (0.0, 0.0, 1.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def _centroid(pts):
    n = len(pts)
    if n == 0:
        return (0.0, 0.0, 0.0)
    sx = sum(p[0] for p in pts) / n
    sy = sum(p[1] for p in pts) / n
    sz = sum(p[2] for p in pts) / n
    return (sx, sy, sz)


def _best_normal(pts):
    """Best-fit plane normal via 3-point cross-product fallback, refined by
    picking the cross-product of the two SAMPLE-CENTROID vectors that are
    most orthogonal — robust even when consecutive samples are nearly
    collinear."""
    n = len(pts)
    if n < 3:
        return None
    c = _centroid(pts)
    # Vectors from centroid
    vs = [_vec_sub(p, c) for p in pts]
    # Find a vector with non-trivial magnitude
    mag2 = [_vec_dot(v, v) for v in vs]
    i0 = max(range(n), key=lambda i: mag2[i])
    if mag2[i0] < 1e-18:
        return None
    v0 = vs[i0]
    # Find a second vector most orthogonal to v0 (minimal |cos θ|)
    best_j = -1
    best_orthogonality = -1.0
    for j in range(n):
        if j == i0:
            continue
        mj = mag2[j]
        if mj < 1e-18:
            continue
        cos = abs(_vec_dot(v0, vs[j])) / math.sqrt(mag2[i0] * mj)
        score = 1.0 - cos
        if score > best_orthogonality:
            best_orthogonality = score
            best_j = j
    if best_j < 0:
        return None
    nrm = _vec_cross(v0, vs[best_j])
    if _vec_norm(nrm) < 1e-12:
        return None
    return _vec_unit(nrm)


def _project_to_plane(pts, origin, normal):
    """Return 2D coords (u, v) of each pt projected onto the plane through
    origin with given unit normal, along with signed out-of-plane distance."""
    # Build a plane basis (u, v) such that u × v = normal
    nx, ny, nz = normal
    # pick the world axis least aligned with the normal
    if abs(nx) <= abs(ny) and abs(nx) <= abs(nz):
        seed = (1.0, 0.0, 0.0)
    elif abs(ny) <= abs(nx) and abs(ny) <= abs(nz):
        seed = (0.0, 1.0, 0.0)
    else:
        seed = (0.0, 0.0, 1.0)
    u = _vec_unit(_vec_cross(normal, seed))
    v = _vec_unit(_vec_cross(normal, u))

    out_2d = []
    out_dist = []
    for p in pts:
        rel = _vec_sub(p, origin)
        out_2d.append((_vec_dot(rel, u), _vec_dot(rel, v)))
        out_dist.append(_vec_dot(rel, normal))
    return out_2d, out_dist


def _fit_circle_2d(pts2d):
    """Algebraic (Kasa) circle fit: minimize
        Σ (x² + y² - 2ax - 2by - c)² ,
    solving 3×3 normal equations. Returns (cx, cy, r) or None on singular."""
    n = len(pts2d)
    if n < 3:
        return None
    Sx = Sy = Sxx = Syy = Sxy = Sxxx = Syyy = Sxyy = Sxxy = Sxxxx = Syyyy = 0.0
    for x, y in pts2d:
        Sx += x
        Sy += y
        Sxx += x * x
        Syy += y * y
        Sxy += x * y
        Sxxx += x * x * x
        Syyy += y * y * y
        Sxyy += x * y * y
        Sxxy += x * x * y
    # Solve
    #   [ Sxx Sxy Sx ] [a]   [ (Sxxx + Sxyy) / 2 ]
    #   [ Sxy Syy Sy ] [b] = [ (Syyy + Sxxy) / 2 ]
    #   [ Sx  Sy  N  ] [c]   [ (Sxx + Syy) / 2   ]
    # where center = (a, b), radius² = c + a² + b².
    A = [
        [Sxx, Sxy, Sx],
        [Sxy, Syy, Sy],
        [Sx, Sy, float(n)],
    ]
    b = [
        0.5 * (Sxxx + Sxyy),
        0.5 * (Syyy + Sxxy),
        0.5 * (Sxx + Syy),
    ]
    # 3x3 Cramer
    def _det3(m):
        return (
            m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
        )

    D = _det3(A)
    if abs(D) < 1e-18:
        return None

    def _replace_col(m, col, vec):
        return [[(vec[r] if c == col else m[r][c]) for c in range(3)] for r in range(3)]

    a = _det3(_replace_col(A, 0, b)) / D
    b_ = _det3(_replace_col(A, 1, b)) / D
    c_ = _det3(_replace_col(A, 2, b)) / D
    r2 = c_ + a * a + b_ * b_
    if r2 <= 0.0:
        return None
    return (a, b_, math.sqrt(r2))


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="detect_shell_holes",
    category="inspect",
    level="atomic",
    summary="Shell-aware hole detection. Walks every closed free-boundary wire "
            "on an open (mesh-derived) shell, fits each wire to a circle, and "
            "emits the wires whose perimeter falls in [min_perimeter_mm, "
            "max_perimeter_mm] and whose radial / planarity deviation is "
            "below axis_tolerance. Catches camera, button, port, and screw "
            "cutouts on iPhone-style scans that classify_holes (which "
            "requires analytic cylindrical faces on a closed solid) reports "
            "as zero. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["shell_hole_inventory"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_closed_boundaries"],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class DetectShellHoles(SkillBase):
    class Args(BaseModel):
        min_perimeter_mm: float = Field(
            default=2.0, gt=0,
            description="Discard wires whose total arc length is below this. "
                        "Below ~2 mm the wire is usually mesh noise or a "
                        "decimation artifact rather than a real cutout.",
        )
        max_perimeter_mm: float = Field(
            default=80.0, gt=0,
            description="Discard wires whose total arc length exceeds this. "
                        "Real phone cutouts are at most a few cm in "
                        "perimeter; openings larger than ~80 mm tend to be "
                        "teardown openings (missing back glass, missing "
                        "motherboard).",
        )
        axis_tolerance_deg: float = Field(
            default=10.0, gt=0, le=90.0,
            description="Maximum allowed (max_radial_deviation / radius) and "
                        "(max_planarity_deviation / radius), expressed as the "
                        "sine of this angle. Looser values let near-elliptical "
                        "or slightly off-plane loops in; tighter values "
                        "restrict to clean drilled holes.",
        )
        n_samples: int = Field(
            default=64, ge=8, le=512,
            description="Total number of sample points per wire used for the "
                        "circle and plane fits. More samples → tighter "
                        "deviation estimates but slower.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
        from OCP.TopAbs import TopAbs_WIRE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        shape = _occt_shape(body)

        # 1. Closed free-boundary wires only — open boundary chains are not
        # hole candidates.
        fb = ShapeAnalysis_FreeBounds(shape, False, True)  # split_closed=True
        closed_compound = fb.GetClosedWires()

        wires: list = []
        it = TopExp_Explorer(closed_compound, TopAbs_WIRE)
        while it.More():
            wires.append(TopoDS.Wire_s(it.Current()))
            it.Next()

        # Tolerance: max_deviation / radius <= sin(axis_tolerance_deg)
        tol_ratio = math.sin(math.radians(float(args.axis_tolerance_deg)))

        shell_holes: list[dict[str, Any]] = []
        total_wires = len(wires)
        rejected_perimeter = 0
        rejected_geometry = 0

        for w in wires:
            try:
                perim = _wire_perimeter_mm(w)
            except Exception:
                perim = 0.0
            if perim < args.min_perimeter_mm or perim > args.max_perimeter_mm:
                rejected_perimeter += 1
                continue

            try:
                pts = _wire_sample_points(w, n_samples=int(args.n_samples))
            except Exception:
                pts = []
            if len(pts) < 6:
                rejected_geometry += 1
                continue

            normal = _best_normal(pts)
            if normal is None:
                rejected_geometry += 1
                continue

            origin = _centroid(pts)
            pts2d, dists = _project_to_plane(pts, origin, normal)

            fit = _fit_circle_2d(pts2d)
            if fit is None:
                rejected_geometry += 1
                continue
            cx_2d, cy_2d, radius = fit
            if radius <= 1e-6:
                rejected_geometry += 1
                continue

            # Lift center back to 3D
            # Reconstruct the same u/v basis used in _project_to_plane
            nx, ny, nz = normal
            if abs(nx) <= abs(ny) and abs(nx) <= abs(nz):
                seed = (1.0, 0.0, 0.0)
            elif abs(ny) <= abs(nx) and abs(ny) <= abs(nz):
                seed = (0.0, 1.0, 0.0)
            else:
                seed = (0.0, 0.0, 1.0)
            u = _vec_unit(_vec_cross(normal, seed))
            v = _vec_unit(_vec_cross(normal, u))
            center_3d = (
                origin[0] + cx_2d * u[0] + cy_2d * v[0],
                origin[1] + cx_2d * u[1] + cy_2d * v[1],
                origin[2] + cx_2d * u[2] + cy_2d * v[2],
            )

            # Radial deviation (in-plane)
            max_radial = 0.0
            for (px, py) in pts2d:
                dx = px - cx_2d
                dy = py - cy_2d
                dev = abs(math.sqrt(dx * dx + dy * dy) - radius)
                if dev > max_radial:
                    max_radial = dev

            # Out-of-plane deviation
            max_planarity = 0.0
            for d in dists:
                ad = abs(d)
                if ad > max_planarity:
                    max_planarity = ad

            radial_ratio = max_radial / radius
            planarity_ratio = max_planarity / radius

            if radial_ratio > tol_ratio or planarity_ratio > tol_ratio:
                rejected_geometry += 1
                continue

            # Wire-perimeter / fitted-circumference ratio. Reported but NOT
            # used as a hard reject — on decimated meshes (chord segments) the
            # wire's measured perimeter is genuinely shorter than the analytic
            # circle's circumference, and on rounded-rectangle cutouts (iPhone
            # camera bezel) the perimeter exceeds it. The planarity and
            # radial-deviation tests already enforce a roundness gate.
            expected_circumference = 2.0 * math.pi * radius
            arc_completeness = 1.0
            if expected_circumference > 0.0:
                arc_completeness = perim / expected_circumference

            shell_holes.append({
                "idx": len(shell_holes),
                "center": [round(c, 4) for c in center_3d],
                "axis": [round(c, 6) for c in normal],
                "diameter_mm": round(2.0 * radius, 4),
                "perimeter_mm": round(perim, 4),
                "planarity_score": round(
                    max(0.0, 1.0 - planarity_ratio / max(tol_ratio, 1e-9)), 4,
                ),
                "arc_completeness": round(arc_completeness, 4),
            })

        extras: dict[str, Any] = {
            "shell_holes": shell_holes,
            "shell_holes_summary": {
                "closed_wires_found": total_wires,
                "holes_reported": len(shell_holes),
                "rejected_perimeter": rejected_perimeter,
                "rejected_geometry": rejected_geometry,
            },
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
