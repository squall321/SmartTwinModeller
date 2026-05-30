"""fit_torus — atomic, read-only.

Best-fit torus (center, axis, major + minor radius) from face sample points.
If the underlying surface is ``GeomAbs_Torus``, short-circuit and use the
analytic OCCT torus.

Otherwise (heuristic fit):
    1. Axis ≈ smallest-singular-vector of the centered sample cloud (a torus
       is roughly planar on the major-circle plane).
    2. Center ≈ centroid of the samples projected onto that plane.
    3. For each sample, distance to centroid in plane = d_plane. The average
       d_plane is the major radius R; the deviation of (d_plane − R, axial)
       from a circle of radius minor_r gives the minor radius r as the mean
       of sqrt((d_plane − R)² + z_axial²).

Residual = max |sqrt((d_plane − R)² + z_axial²) − r|.

extras["fit_torus"] = {
    "center": [x,y,z],
    "axis": [x,y,z],
    "major_r_mm": float,
    "minor_r_mm": float,
    "max_residual_mm": float,
    "is_native": bool,
    "sample_count": int,
}

body 는 변경하지 않는다 (post ``body_present``).
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


def _sample_face_xyz(face, n_side: int = 10) -> list[tuple[float, float, float]]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    adaptor = BRepAdaptor_Surface(face)
    u0 = adaptor.FirstUParameter()
    u1 = adaptor.LastUParameter()
    v0 = adaptor.FirstVParameter()
    v1 = adaptor.LastVParameter()

    side = max(3, int(n_side))
    pts: list[tuple[float, float, float]] = []
    for i in range(side):
        for j in range(side):
            fu = (i + 0.5) / side
            fv = (j + 0.5) / side
            u = u0 + (u1 - u0) * fu
            v = v0 + (v1 - v0) * fv
            try:
                p = adaptor.Value(u, v)
                pts.append((p.X(), p.Y(), p.Z()))
            except Exception:
                continue
    return pts


def _analytic_torus(face):
    """If face surface is a torus, return (center, axis, major_r, minor_r). Else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Torus

    try:
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() != GeomAbs_Torus:
            return None
        tor = surf.Torus()
        loc = tor.Location()
        ax = tor.Axis().Direction()
        return (
            (float(loc.X()), float(loc.Y()), float(loc.Z())),
            (float(ax.X()), float(ax.Y()), float(ax.Z())),
            float(tor.MajorRadius()),
            float(tor.MinorRadius()),
        )
    except Exception:
        return None


def _fit_torus_from_points(
    pts: list[tuple[float, float, float]],
) -> tuple[
    tuple[float, float, float], tuple[float, float, float], float, float, float
]:
    """Returns (center, axis_unit, major_r, minor_r, max_residual)."""
    if len(pts) < 6:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 0.0, 0.0, 0.0)

    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    cz = sum(p[2] for p in pts) / n

    axis = (0.0, 0.0, 1.0)
    try:
        import numpy as np

        arr = np.array(pts, dtype=float)
        centered = arr - np.array([cx, cy, cz])
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        # Smallest singular vector ≈ torus axis (variance is smallest
        # perpendicular to the major-circle plane).
        a = vh[-1, :]
        mag = float(np.linalg.norm(a))
        if mag > 1e-12:
            a = a / mag
            axis = (float(a[0]), float(a[1]), float(a[2]))
    except Exception:
        pass

    ax, ay, az = axis
    # For each point: distance from axis line through centroid (planar
    # major-circle distance), and axial coordinate.
    d_plane: list[float] = []
    z_ax: list[float] = []
    for x, y, z in pts:
        dx, dy, dz = x - cx, y - cy, z - cz
        t = dx * ax + dy * ay + dz * az
        px = dx - t * ax
        py = dy - t * ay
        pz = dz - t * az
        d_plane.append(math.sqrt(px * px + py * py + pz * pz))
        z_ax.append(t)

    major_r = sum(d_plane) / len(d_plane)
    # Minor radius = mean distance from major-circle to sample.
    minor_samples: list[float] = []
    for i, _ in enumerate(pts):
        dr = d_plane[i] - major_r
        za = z_ax[i]
        minor_samples.append(math.sqrt(dr * dr + za * za))
    minor_r = sum(minor_samples) / len(minor_samples) if minor_samples else 0.0

    max_dev = 0.0
    for m in minor_samples:
        err = abs(m - minor_r)
        if err > max_dev:
            max_dev = err
    return ((cx, cy, cz), axis, major_r, minor_r, max_dev)


@skill(
    name="fit_torus",
    category="inspect",
    level="atomic",
    summary="Best-fit torus (center + axis + major/minor radius + residual) "
            "from face samples. Short-circuits to OCCT analytic torus when "
            "face is GeomAbs_Torus. Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["fit_torus_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class FitTorus(SkillBase):
    class Args(BaseModel):
        face_selector: dict
        samples_per_side: int = Field(default=10, ge=3, le=200)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_faces

        shape = _occt_shape(body)
        sel = _coerce_selector(args.face_selector)
        faces = resolve_faces(shape, sel, body=body)
        if not faces:
            raise ValueError(
                f"fit_torus: face_selector matched 0 faces (kind={sel.kind})"
            )
        face = faces[0]

        pts = _sample_face_xyz(face, args.samples_per_side)
        analytic = _analytic_torus(face)
        if analytic is not None:
            center, axis, major_r, minor_r = analytic
            ox, oy, oz = center
            ax, ay, az = axis
            max_dev = 0.0
            for x, y, z in pts:
                dx, dy, dz = x - ox, y - oy, z - oz
                t = dx * ax + dy * ay + dz * az
                px = dx - t * ax
                py = dy - t * ay
                pz = dz - t * az
                d_plane = math.sqrt(px * px + py * py + pz * pz)
                m = math.sqrt((d_plane - major_r) ** 2 + t * t)
                err = abs(m - minor_r)
                if err > max_dev:
                    max_dev = err
            is_native = True
        else:
            center, axis, major_r, minor_r, max_dev = _fit_torus_from_points(pts)
            is_native = False

        extras = {
            "fit_torus": {
                "center": [round(c, 6) for c in center],
                "axis": [round(c, 6) for c in axis],
                "major_r_mm": round(major_r, 6),
                "minor_r_mm": round(minor_r, 6),
                "max_residual_mm": round(max_dev, 6),
                "is_native": is_native,
                "sample_count": len(pts),
            }
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
