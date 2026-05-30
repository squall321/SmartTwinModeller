"""fit_sphere — atomic, read-only.

Best-fit sphere (center, radius) from face sample points. If the underlying
surface is ``GeomAbs_Sphere``, short-circuit and use the analytic OCCT sphere.

Otherwise: sample a UV grid on the face. The algebraic sphere fit linearizes
|p − c|² = r² → 2 cx X + 2 cy Y + 2 cz Z + (r² − |c|²) = X² + Y² + Z². Solving
the resulting 4 × N least-squares system yields (cx, cy, cz, k) with
r = sqrt(k + cx² + cy² + cz²). Residual = max ||p − c| − r|.

extras["fit_sphere"] = {
    "center": [x,y,z],
    "radius_mm": float,
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


def _analytic_sphere(face):
    """If face surface is a sphere, return (center, radius). Else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Sphere

    try:
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() != GeomAbs_Sphere:
            return None
        sph = surf.Sphere()
        loc = sph.Location()
        r = float(sph.Radius())
        return (
            (float(loc.X()), float(loc.Y()), float(loc.Z())),
            r,
        )
    except Exception:
        return None


def _fit_sphere_from_points(
    pts: list[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], float, float]:
    """Algebraic sphere fit. Returns (center, radius, max_residual)."""
    if len(pts) < 4:
        if not pts:
            return ((0.0, 0.0, 0.0), 0.0, 0.0)
        # Trivial single-point: zero radius.
        return (pts[0], 0.0, 0.0)

    try:
        import numpy as np

        arr = np.array(pts, dtype=float)
        # A · [cx, cy, cz, k] = b, where k = r² − |c|².
        A = np.hstack([2.0 * arr, np.ones((arr.shape[0], 1))])
        b = np.sum(arr * arr, axis=1)
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        cx, cy, cz, k = float(sol[0]), float(sol[1]), float(sol[2]), float(sol[3])
        r_sq = k + cx * cx + cy * cy + cz * cz
        r = math.sqrt(max(r_sq, 0.0))
        max_dev = 0.0
        for x, y, z in pts:
            d = math.sqrt(
                (x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2
            )
            err = abs(d - r)
            if err > max_dev:
                max_dev = err
        return ((cx, cy, cz), r, max_dev)
    except Exception:
        pass

    # Pure-python fallback — centroid + mean distance (no LS refinement).
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    cz = sum(p[2] for p in pts) / n
    radii: list[float] = []
    for x, y, z in pts:
        radii.append(math.sqrt((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2))
    r = sum(radii) / len(radii) if radii else 0.0
    max_dev = max((abs(d - r) for d in radii), default=0.0)
    return ((cx, cy, cz), r, max_dev)


@skill(
    name="fit_sphere",
    category="inspect",
    level="atomic",
    summary="Best-fit sphere (center + radius + residual) from face samples. "
            "Short-circuits to OCCT analytic sphere when face is GeomAbs_Sphere. "
            "Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["fit_sphere_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class FitSphere(SkillBase):
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
                f"fit_sphere: face_selector matched 0 faces (kind={sel.kind})"
            )
        face = faces[0]

        pts = _sample_face_xyz(face, args.samples_per_side)
        analytic = _analytic_sphere(face)
        if analytic is not None:
            center, radius = analytic
            cx, cy, cz = center
            max_dev = 0.0
            for x, y, z in pts:
                d = math.sqrt(
                    (x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2
                )
                err = abs(d - radius)
                if err > max_dev:
                    max_dev = err
            is_native = True
        else:
            center, radius, max_dev = _fit_sphere_from_points(pts)
            is_native = False

        extras = {
            "fit_sphere": {
                "center": [round(c, 6) for c in center],
                "radius_mm": round(radius, 6),
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
