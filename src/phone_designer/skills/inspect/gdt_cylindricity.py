"""gdt_cylindricity — atomic, read-only.

GD&T cylindricity tolerance check. Sample points on the matched face, fit a
best-fit cylinder, and report the maximum radial deviation (= |distance from
fit axis − fit radius|). A face passes if max deviation ≤ ``tolerance_mm``.

Strategy:
    1. If the underlying surface IS a cylinder (GeomAbs_Cylinder), use the
       analytic axis + radius from OCCT — the only deviation then comes from
       sampling noise (essentially 0). This is the common case for drilled
       holes / turned shafts.
    2. Otherwise, sample N points, then:
         a. Estimate axis direction as the smallest singular vector of the
            centered point cloud (cylinder is rank-2 in the cross-section,
            extended along the axis).
         b. Project points onto the plane normal to the axis through the
            centroid; the radius is the mean distance to that 2D centroid.
         c. Max |distance − radius| = deviation.

extras schema:
    {"cylindricity": {
        "deviation_mm": float,
        "pass": bool,
        "tolerance_mm": float,
        "sample_count": int,
        "fit_radius_mm": float,
        "axis_origin": [x,y,z],
        "axis_direction": [x,y,z]
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


def _sample_face_xyz(face, n_samples: int) -> list[tuple[float, float, float]]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    adaptor = BRepAdaptor_Surface(face)
    u0 = adaptor.FirstUParameter()
    u1 = adaptor.LastUParameter()
    v0 = adaptor.FirstVParameter()
    v1 = adaptor.LastVParameter()

    side = max(3, int(math.ceil(math.sqrt(max(1, n_samples)))))
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
        # Normalize axis direction (gp_Dir is already unit).
        d = (ax.X(), ax.Y(), ax.Z())
        return ((loc.X(), loc.Y(), loc.Z()), d, r)
    except Exception:
        return None


def _fit_cylinder_from_points(
    pts: list[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], tuple[float, float, float], float, float]:
    """Fit cylinder to points. Returns (axis_origin, axis_dir_unit, radius, max_dev).

    axis_dir = principal direction (largest singular value of centered cloud)
    — for a cylindrical sample, this is the axis (variance is largest along
    the length of the cylinder).
    """
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
        # Largest singular vector = principal direction = cylinder axis.
        a = vh[0, :]
        mag = float(np.linalg.norm(a))
        if mag > 1e-12:
            a = a / mag
            axis = (float(a[0]), float(a[1]), float(a[2]))
    except Exception:
        pass

    # Project each point onto plane through centroid perpendicular to axis,
    # compute distance to centroid in that plane = radial distance.
    radii: list[float] = []
    ax, ay, az = axis
    for x, y, z in pts:
        dx, dy, dz = x - cx, y - cy, z - cz
        # Component along axis:
        t = dx * ax + dy * ay + dz * az
        # Perp component:
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
    name="gdt_cylindricity",
    category="inspect",
    level="atomic",
    summary="GD&T cylindricity: fit a cylinder to the matched face and "
            "report max radial deviation vs tolerance_mm. Uses OCCT analytic "
            "cylinder when surface is exactly cylindrical, else SVD point fit. "
            "Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["gdt_cylindricity_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class GdtCylindricity(SkillBase):
    class Args(BaseModel):
        face_selector: dict
        tolerance_mm: float = Field(default=0.05, ge=0.0)
        samples: int = Field(default=36, ge=4, le=10000)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_faces

        shape = _occt_shape(body)
        sel = _coerce_selector(args.face_selector)
        faces = resolve_faces(shape, sel, body=body)
        if not faces:
            raise ValueError(
                f"gdt_cylindricity: face_selector matched 0 faces (kind={sel.kind})"
            )
        face = faces[0]

        pts = _sample_face_xyz(face, args.samples)
        analytic = _analytic_cylinder(face)
        if analytic is not None:
            origin, axis, radius = analytic
            # Even for an analytic cylinder, compute max sample deviation —
            # acts as a sanity check (should be ≈ 0 down to floating noise).
            ax, ay, az = axis
            ox, oy, oz = origin
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
        else:
            origin, axis, radius, max_dev = _fit_cylinder_from_points(pts)

        passed = bool(max_dev <= args.tolerance_mm)
        extras = {
            "cylindricity": {
                "deviation_mm": round(max_dev, 6),
                "pass": passed,
                "tolerance_mm": float(args.tolerance_mm),
                "sample_count": len(pts),
                "fit_radius_mm": round(radius, 6),
                "axis_origin": [round(c, 6) for c in origin],
                "axis_direction": [round(c, 6) for c in axis],
            }
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
