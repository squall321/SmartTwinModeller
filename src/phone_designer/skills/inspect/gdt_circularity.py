"""gdt_circularity — atomic, read-only.

GD&T circularity (roundness) tolerance check. Sample N points along the
matched edge, project onto a best-fit plane, fit a circle in that plane,
and report the maximum radial deviation. A pair passes if deviation ≤
``tolerance_mm``.

Strategy:
    1. Resolve ``edge_selector`` → first edge on body.
    2. Sample N points uniformly along the edge (GCPnts_UniformAbscissa).
    3. Fit a plane to those points (SVD).
    4. Project them into that plane (2D coordinates along plane basis).
    5. Fit a circle to the 2D coords by linear least-squares
       (Coope / Kasa method): each point gives equation
       2*xc*x + 2*yc*y + (R² − xc² − yc²) = x² + y²
       which is linear in (xc, yc, c=R²−xc²−yc²). Solve normal equations.
    6. Compute residuals = sqrt((x−xc)² + (y−yc)²) − R, take max |residual|.

If the underlying curve is exactly a circle (BRepAdaptor_Curve.GetType() ==
GeomAbs_Circle), use OCCT's analytic circle.

extras schema:
    {"circularity": {
        "deviation_mm": float,
        "pass": bool,
        "tolerance_mm": float,
        "sample_count": int,
        "fit_radius_mm": float,
        "fit_center": [x,y,z]
     }}
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _sample_edge_xyz(edge, n_samples: int) -> list[tuple[float, float, float]]:
    """Uniform-arc-length sample of an edge."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_UniformAbscissa

    curve = BRepAdaptor_Curve(edge)
    n = max(4, int(n_samples))
    pts: list[tuple[float, float, float]] = []
    try:
        algo = GCPnts_UniformAbscissa(curve, n)
        if algo.IsDone() and algo.NbPoints() >= 2:
            for i in range(1, algo.NbPoints() + 1):
                u = algo.Parameter(i)
                p = curve.Value(u)
                pts.append((p.X(), p.Y(), p.Z()))
            return pts
    except Exception:
        pts = []

    # Fallback: uniform parameter sampling.
    u_first = curve.FirstParameter()
    u_last = curve.LastParameter()
    if u_last <= u_first:
        return []
    for i in range(n):
        u = u_first + (u_last - u_first) * (i / (n - 1))
        p = curve.Value(u)
        pts.append((p.X(), p.Y(), p.Z()))
    return pts


def _analytic_circle(edge):
    """If edge curve is a circle, return (center_xyz, radius). Else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle

    try:
        curve = BRepAdaptor_Curve(edge)
        if curve.GetType() != GeomAbs_Circle:
            return None
        c = curve.Circle()
        loc = c.Location()
        r = float(c.Radius())
        return ((loc.X(), loc.Y(), loc.Z()), r)
    except Exception:
        return None


def _fit_circle_in_3d(
    pts: list[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], float, float]:
    """Best-fit circle (3D) to points. Returns (center_xyz, radius, max_dev).

    Plane via SVD, then circle via Kasa linear LS in plane coords.
    """
    if len(pts) < 3:
        return ((0.0, 0.0, 0.0), 0.0, 0.0)

    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    cz = sum(p[2] for p in pts) / n

    try:
        import numpy as np

        arr = np.array(pts, dtype=float)
        centered = arr - np.array([cx, cy, cz])
        # Plane: smallest singular vector is normal; the other two span the
        # in-plane basis.
        _, _, vh = np.linalg.svd(centered, full_matrices=True)
        # vh rows are right singular vectors (V^T). Use first two as basis.
        e1 = vh[0, :]
        e2 = vh[1, :]
        # 2D coords:
        xy = np.stack([centered @ e1, centered @ e2], axis=1)
        # Kasa: A * (a, b, c) = d, where 2a*x + 2b*y + c = x² + y², R² = c + a² + b².
        A = np.column_stack([2.0 * xy[:, 0], 2.0 * xy[:, 1], np.ones(len(xy))])
        d = xy[:, 0] ** 2 + xy[:, 1] ** 2
        sol, *_ = np.linalg.lstsq(A, d, rcond=None)
        a, b, c = float(sol[0]), float(sol[1]), float(sol[2])
        r2 = c + a * a + b * b
        if r2 < 0.0:
            r2 = 0.0
        radius = math.sqrt(r2)
        # Convert (a, b) plane center back to 3D:
        center = np.array([cx, cy, cz]) + a * e1 + b * e2
        # Residuals: distance from each pt to fit circle. In 3D, distance to
        # the circle (not plane-projected) is sqrt(d_plane² + d_axis²) where
        # d_plane = |√((x−a)²+(y−b)²) − R| and d_axis = out-of-plane distance.
        n_ax = vh[2, :]
        residuals = []
        for p in arr:
            dv = p - center
            # In-plane distance:
            ip_x = float(np.dot(dv, e1))
            ip_y = float(np.dot(dv, e2))
            r_in = math.sqrt(ip_x * ip_x + ip_y * ip_y)
            # Out-of-plane distance:
            op = float(np.dot(dv, n_ax))
            # Distance to the circle's nearest point:
            err = math.sqrt((r_in - radius) ** 2 + op * op)
            residuals.append(err)
        max_dev = max(residuals) if residuals else 0.0
        return (
            (float(center[0]), float(center[1]), float(center[2])),
            float(radius),
            float(max_dev),
        )
    except Exception:
        pass

    # Pure-Python fallback — assume points are roughly co-planar at z=cz.
    radii = []
    for x, y, z in pts:
        radii.append(math.sqrt((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2))
    r_fit = sum(radii) / len(radii)
    max_dev = max(abs(r - r_fit) for r in radii)
    return ((cx, cy, cz), r_fit, max_dev)


@skill(
    name="gdt_circularity",
    category="inspect",
    level="atomic",
    summary="GD&T circularity (roundness): sample a circular edge, fit a "
            "best-fit circle (plane via SVD, circle via Kasa LS), report max "
            "radial deviation vs tolerance_mm. Read-only — body unchanged.",
    selector_kinds=[
        "axis_aligned_edges", "edges_on_face", "edges_by_length",
        "edges_by_position", "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["gdt_circularity_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class GdtCircularity(SkillBase):
    class Args(BaseModel):
        edge_selector: dict
        tolerance_mm: float = Field(default=0.05, ge=0.0)
        samples: int = Field(default=36, ge=4, le=10000)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_edges

        shape = _occt_shape(body)
        sel = _coerce_selector(args.edge_selector)
        edges = resolve_edges(shape, sel)
        if not edges:
            raise ValueError(
                f"gdt_circularity: edge_selector matched 0 edges (kind={sel.kind})"
            )
        edge = edges[0]

        pts = _sample_edge_xyz(edge, args.samples)

        analytic = _analytic_circle(edge)
        if analytic is not None:
            center, radius = analytic
            # Sample-vs-analytic deviation (≈ 0 for true circles).
            max_dev = 0.0
            cx0, cy0, cz0 = center
            for x, y, z in pts:
                d = math.sqrt((x - cx0) ** 2 + (y - cy0) ** 2 + (z - cz0) ** 2)
                err = abs(d - radius)
                if err > max_dev:
                    max_dev = err
        else:
            center, radius, max_dev = _fit_circle_in_3d(pts)

        passed = bool(max_dev <= args.tolerance_mm)
        extras = {
            "circularity": {
                "deviation_mm": round(max_dev, 6),
                "pass": passed,
                "tolerance_mm": float(args.tolerance_mm),
                "sample_count": len(pts),
                "fit_radius_mm": round(radius, 6),
                "fit_center": [round(c, 6) for c in center],
            }
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
