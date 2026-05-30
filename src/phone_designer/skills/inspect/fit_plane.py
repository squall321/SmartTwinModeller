"""fit_plane — atomic, read-only.

Best-fit plane from face sample points. If the underlying surface is already
``GeomAbs_Plane``, short-circuit and use the analytic OCCT plane (residual ≈ 0).

Otherwise: sample a UV grid on the face, build a least-squares plane via SVD
(centroid + smallest singular vector), and report max perpendicular residual.

extras["fit_plane"] = {
    "origin": [x,y,z],          # centroid of samples (or OCCT plane Location)
    "normal": [nx,ny,nz],       # unit normal
    "max_residual_mm": float,   # max |signed perpendicular distance| over samples
    "is_planar": bool,          # True if analytic plane was used
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
    """Sample a ``n_side`` × ``n_side`` UV grid on the face."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    adaptor = BRepAdaptor_Surface(face)
    u0 = adaptor.FirstUParameter()
    u1 = adaptor.LastUParameter()
    v0 = adaptor.FirstVParameter()
    v1 = adaptor.LastVParameter()

    side = max(2, int(n_side))
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


def _analytic_plane(face):
    """If face surface is a plane, return (origin, unit_normal). Else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane

    try:
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() != GeomAbs_Plane:
            return None
        pln = surf.Plane()
        loc = pln.Location()
        ax = pln.Axis().Direction()
        return (
            (float(loc.X()), float(loc.Y()), float(loc.Z())),
            (float(ax.X()), float(ax.Y()), float(ax.Z())),
        )
    except Exception:
        return None


def _fit_plane_svd(
    pts: list[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], tuple[float, float, float], float]:
    """Least-squares plane fit (SVD). Returns (centroid, unit_normal, max_dev)."""
    if not pts:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 0.0)

    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    cz = sum(p[2] for p in pts) / n

    try:
        import numpy as np

        arr = np.array(pts, dtype=float)
        centered = arr - np.array([cx, cy, cz])
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        normal = vh[-1, :]
        nmag = float(np.linalg.norm(normal))
        if nmag < 1e-12:
            normal = np.array([0.0, 0.0, 1.0])
        else:
            normal = normal / nmag
        deviations = centered @ normal
        max_dev = float(np.max(np.abs(deviations)))
        return (
            (cx, cy, cz),
            (float(normal[0]), float(normal[1]), float(normal[2])),
            max_dev,
        )
    except Exception:
        pass

    # Pure-python fallback (covariance + power iteration on (mu*I - C)).
    cxx = cxy = cxz = cyy = cyz = czz = 0.0
    for x, y, z in pts:
        dx, dy, dz = x - cx, y - cy, z - cz
        cxx += dx * dx
        cxy += dx * dy
        cxz += dx * dz
        cyy += dy * dy
        cyz += dy * dz
        czz += dz * dz
    mu = cxx + cyy + czz + 1.0
    a00, a01, a02 = mu - cxx, -cxy, -cxz
    a10, a11, a12 = -cxy, mu - cyy, -cyz
    a20, a21, a22 = -cxz, -cyz, mu - czz
    vx, vy, vz = 1.0, 1.0, 1.0
    for _ in range(64):
        nx = a00 * vx + a01 * vy + a02 * vz
        ny = a10 * vx + a11 * vy + a12 * vz
        nz = a20 * vx + a21 * vy + a22 * vz
        m = math.sqrt(nx * nx + ny * ny + nz * nz)
        if m < 1e-12:
            vx, vy, vz = 0.0, 0.0, 1.0
            break
        vx, vy, vz = nx / m, ny / m, nz / m
    max_dev = 0.0
    for x, y, z in pts:
        d = (x - cx) * vx + (y - cy) * vy + (z - cz) * vz
        if abs(d) > max_dev:
            max_dev = abs(d)
    return ((cx, cy, cz), (vx, vy, vz), max_dev)


@skill(
    name="fit_plane",
    category="inspect",
    level="atomic",
    summary="Best-fit plane (origin + normal + residual) from face samples. "
            "Short-circuits to OCCT analytic plane when face is GeomAbs_Plane. "
            "Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["fit_plane_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class FitPlane(SkillBase):
    class Args(BaseModel):
        face_selector: dict
        samples_per_side: int = Field(default=10, ge=2, le=200)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_faces

        shape = _occt_shape(body)
        sel = _coerce_selector(args.face_selector)
        faces = resolve_faces(shape, sel, body=body)
        if not faces:
            raise ValueError(
                f"fit_plane: face_selector matched 0 faces (kind={sel.kind})"
            )
        face = faces[0]

        pts = _sample_face_xyz(face, args.samples_per_side)
        analytic = _analytic_plane(face)
        if analytic is not None:
            origin, normal = analytic
            ox, oy, oz = origin
            nx, ny, nz = normal
            max_dev = 0.0
            for x, y, z in pts:
                d = (x - ox) * nx + (y - oy) * ny + (z - oz) * nz
                if abs(d) > max_dev:
                    max_dev = abs(d)
            is_planar = True
        else:
            origin, normal, max_dev = _fit_plane_svd(pts)
            is_planar = False

        extras = {
            "fit_plane": {
                "origin": [round(c, 6) for c in origin],
                "normal": [round(c, 6) for c in normal],
                "max_residual_mm": round(max_dev, 6),
                "is_planar": is_planar,
                "sample_count": len(pts),
            }
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
