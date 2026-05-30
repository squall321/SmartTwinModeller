"""fit_cylinder — atomic, read-only.

Best-fit cylinder (axis_origin, axis_direction, radius) from face sample points.
If the underlying surface is ``GeomAbs_Cylinder``, short-circuit and use the
analytic OCCT cylinder.

Otherwise: sample a UV grid on the face. Estimate the cylinder axis as the
largest singular vector of the centered point cloud (cylindrical sample is
elongated along its axis). The radius is the mean distance from the axis,
the residual is max |distance − radius|.

extras["fit_cylinder"] = {
    "axis_origin": [x,y,z],
    "axis_direction": [x,y,z],
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


def _analytic_cylinder(face):
    """If face surface is a cylinder, return (origin, dir_unit, radius). Else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    try:
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() != GeomAbs_Cylinder:
            return None
        cyl = surf.Cylinder()
        ax = cyl.Axis().Direction()
        loc = cyl.Location()
        r = float(cyl.Radius())
        return (
            (float(loc.X()), float(loc.Y()), float(loc.Z())),
            (float(ax.X()), float(ax.Y()), float(ax.Z())),
            r,
        )
    except Exception:
        return None


def _fit_cylinder_from_points(
    pts: list[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], tuple[float, float, float], float, float]:
    """Fit cylinder. Returns (axis_origin, axis_dir_unit, radius, max_residual)."""
    if not pts:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 0.0, 0.0)

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
        # Largest singular vector ≈ axis of elongated cylindrical sample.
        a = vh[0, :]
        mag = float(np.linalg.norm(a))
        if mag > 1e-12:
            a = a / mag
            axis = (float(a[0]), float(a[1]), float(a[2]))
    except Exception:
        pass

    ax, ay, az = axis
    radii: list[float] = []
    for x, y, z in pts:
        dx, dy, dz = x - cx, y - cy, z - cz
        t = dx * ax + dy * ay + dz * az
        px = dx - t * ax
        py = dy - t * ay
        pz = dz - t * az
        radii.append(math.sqrt(px * px + py * py + pz * pz))
    if not radii:
        return ((cx, cy, cz), axis, 0.0, 0.0)
    r_fit = sum(radii) / len(radii)
    max_dev = max(abs(r - r_fit) for r in radii)
    return ((cx, cy, cz), axis, r_fit, max_dev)


@skill(
    name="fit_cylinder",
    category="inspect",
    level="atomic",
    summary="Best-fit cylinder (axis + radius + residual) from face samples. "
            "Short-circuits to OCCT analytic cylinder when face is "
            "GeomAbs_Cylinder. Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["fit_cylinder_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class FitCylinder(SkillBase):
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
                f"fit_cylinder: face_selector matched 0 faces (kind={sel.kind})"
            )
        face = faces[0]

        pts = _sample_face_xyz(face, args.samples_per_side)
        analytic = _analytic_cylinder(face)
        if analytic is not None:
            origin, axis, radius = analytic
            ox, oy, oz = origin
            ax, ay, az = axis
            max_dev = 0.0
            for x, y, z in pts:
                dx, dy, dz = x - ox, y - oy, z - oz
                t = dx * ax + dy * ay + dz * az
                px = dx - t * ax
                py = dy - t * ay
                pz = dz - t * az
                d = math.sqrt(px * px + py * py + pz * pz)
                err = abs(d - radius)
                if err > max_dev:
                    max_dev = err
            is_native = True
        else:
            origin, axis, radius, max_dev = _fit_cylinder_from_points(pts)
            is_native = False

        extras = {
            "fit_cylinder": {
                "axis_origin": [round(c, 6) for c in origin],
                "axis_direction": [round(c, 6) for c in axis],
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
