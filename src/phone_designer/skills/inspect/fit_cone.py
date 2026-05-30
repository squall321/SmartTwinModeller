"""fit_cone — atomic, read-only.

Best-fit cone (apex, axis, half-angle) from face sample points. If the underlying
surface is ``GeomAbs_Cone``, short-circuit and use the analytic OCCT cone.

Otherwise: sample a UV grid on the face. Two-stage point fit:

    1. Axis direction estimate = principal singular vector of the centered
       sample cloud (cone is elongated along its axis just like a cylinder).
    2. Project samples onto the axis (parameter ``t``) and measure their
       perpendicular radius ``r(t)`` from the axis. For an ideal cone these
       form a line ``r(t) = m·t + b``. Least-squares linear fit gives
       ``m = tan(half_angle)`` and the apex is the axis point where r = 0,
       i.e. t_apex = −b / m.

Residual = max |observed radius − line-fit radius|.

extras["fit_cone"] = {
    "apex": [x,y,z],
    "axis": [x,y,z],         # unit, pointing away from apex toward opening
    "half_angle_deg": float, # acute (0..90)
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


def _analytic_cone(face):
    """If face surface is a cone, return (apex, axis, half_angle_rad). Else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cone

    try:
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() != GeomAbs_Cone:
            return None
        cone = surf.Cone()
        apex = cone.Apex()
        ax = cone.Axis().Direction()
        ang = float(cone.SemiAngle())
        return (
            (float(apex.X()), float(apex.Y()), float(apex.Z())),
            (float(ax.X()), float(ax.Y()), float(ax.Z())),
            ang,
        )
    except Exception:
        return None


def _fit_cone_from_points(
    pts: list[tuple[float, float, float]],
) -> tuple[
    tuple[float, float, float], tuple[float, float, float], float, float
]:
    """Returns (apex, axis_unit, half_angle_rad, max_residual)."""
    if len(pts) < 4:
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
        a = vh[0, :]
        mag = float(np.linalg.norm(a))
        if mag > 1e-12:
            a = a / mag
            axis = (float(a[0]), float(a[1]), float(a[2]))
    except Exception:
        pass

    ax, ay, az = axis
    # Project each point onto the axis (parameter t) and measure radius.
    ts: list[float] = []
    rs: list[float] = []
    for x, y, z in pts:
        dx, dy, dz = x - cx, y - cy, z - cz
        t = dx * ax + dy * ay + dz * az
        px = dx - t * ax
        py = dy - t * ay
        pz = dz - t * az
        ts.append(t)
        rs.append(math.sqrt(px * px + py * py + pz * pz))

    # Linear least-squares r = m t + b.
    nf = float(len(ts))
    st = sum(ts)
    sr = sum(rs)
    stt = sum(t * t for t in ts)
    str_ = sum(ts[i] * rs[i] for i in range(len(ts)))
    denom = nf * stt - st * st
    if abs(denom) < 1e-18:
        m = 0.0
        b = sr / nf if nf else 0.0
    else:
        m = (nf * str_ - st * sr) / denom
        b = (sr - m * st) / nf

    # Half angle = atan(|m|). Sign of m says whether radius grows along +axis
    # or −axis. Apex is the t where r = 0.
    if abs(m) < 1e-12:
        # Degenerate (cylinder), apex unresolved → use centroid, angle ≈ 0.
        t_apex = 0.0
        half_angle = 0.0
    else:
        t_apex = -b / m
        half_angle = math.atan(abs(m))

    apex = (cx + t_apex * ax, cy + t_apex * ay, cz + t_apex * az)

    # Ensure axis points from apex toward the centroid (positive-going for
    # most samples).
    if m < 0.0:
        axis = (-ax, -ay, -az)

    # Residual = max|observed r − fit r|.
    max_dev = 0.0
    for i in range(len(ts)):
        fit_r = m * ts[i] + b
        err = abs(rs[i] - fit_r)
        if err > max_dev:
            max_dev = err

    return (apex, axis, half_angle, max_dev)


@skill(
    name="fit_cone",
    category="inspect",
    level="atomic",
    summary="Best-fit cone (apex + axis + half-angle + residual) from face "
            "samples. Short-circuits to OCCT analytic cone when face is "
            "GeomAbs_Cone. Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["fit_cone_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class FitCone(SkillBase):
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
                f"fit_cone: face_selector matched 0 faces (kind={sel.kind})"
            )
        face = faces[0]

        pts = _sample_face_xyz(face, args.samples_per_side)
        analytic = _analytic_cone(face)
        if analytic is not None:
            apex, axis, half_angle = analytic
            # OCCT's SemiAngle may be signed; ``half_angle_deg`` reports the
            # acute magnitude (cones are symmetric about the axis).
            half_angle = abs(half_angle)
            ox, oy, oz = apex
            ax, ay, az = axis
            tan_a = math.tan(half_angle)
            max_dev = 0.0
            for x, y, z in pts:
                dx, dy, dz = x - ox, y - oy, z - oz
                t = dx * ax + dy * ay + dz * az
                px = dx - t * ax
                py = dy - t * ay
                pz = dz - t * az
                r = math.sqrt(px * px + py * py + pz * pz)
                fit_r = abs(t) * tan_a
                err = abs(r - fit_r)
                if err > max_dev:
                    max_dev = err
            is_native = True
        else:
            apex, axis, half_angle, max_dev = _fit_cone_from_points(pts)
            is_native = False

        extras = {
            "fit_cone": {
                "apex": [round(c, 6) for c in apex],
                "axis": [round(c, 6) for c in axis],
                "half_angle_deg": round(math.degrees(half_angle), 6),
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
