"""gdt_flatness — atomic, read-only.

GD&T flatness tolerance check. Sample N points on the matched face, fit a
best-fit plane (least-squares via SVD), and report the maximum perpendicular
deviation. A face passes if max deviation ≤ ``tolerance_mm``.

Algorithm:
    1. Resolve ``face_selector`` → first face on body.
    2. Sample a UV grid (sqrt(N) × sqrt(N)) of 3D points on the face.
    3. Centroid the points, build the 3xN deviation matrix and run SVD;
       the smallest singular vector is the best-fit plane normal.
    4. Perpendicular deviation of each point from the plane = dot(p-c, n).
    5. Flatness = max(|deviation|) − for ideally flat (planar) face this
       is ≈ 0; for a curved face this grows with the curvature.

extras schema:
    {"flatness": {
        "deviation_mm": float,           # max |signed deviation|
        "pass": bool,
        "tolerance_mm": float,
        "sample_count": int,
        "plane_origin": [x,y,z],         # centroid of samples
        "plane_normal": [x,y,z]          # unit normal of best-fit plane
     }}

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


def _sample_face_xyz(face, n_samples: int) -> list[tuple[float, float, float]]:
    """Sample a roughly sqrt(N)×sqrt(N) UV grid of 3D points on a face."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    adaptor = BRepAdaptor_Surface(face)
    u0 = adaptor.FirstUParameter()
    u1 = adaptor.LastUParameter()
    v0 = adaptor.FirstVParameter()
    v1 = adaptor.LastVParameter()

    side = max(2, int(math.ceil(math.sqrt(max(1, n_samples)))))
    pts: list[tuple[float, float, float]] = []
    for i in range(side):
        for j in range(side):
            # Center each cell — avoids degenerate parameter edges.
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


def _best_fit_plane(
    pts: list[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], tuple[float, float, float], float]:
    """Least-squares plane fit. Returns (centroid, unit_normal, max_dev_mm).

    Uses numpy SVD if available; otherwise covariance-eigen power iteration.
    The plane is defined by the centroid + the singular vector corresponding
    to the smallest singular value.
    """
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
        # SVD of centered points; smallest singular vector = plane normal.
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        normal = vh[-1, :]
        nmag = float(np.linalg.norm(normal))
        if nmag < 1e-12:
            normal = np.array([0.0, 0.0, 1.0])
        else:
            normal = normal / nmag
        # Signed distance of each centered point from plane.
        deviations = centered @ normal
        max_dev = float(np.max(np.abs(deviations)))
        nx, ny, nz = float(normal[0]), float(normal[1]), float(normal[2])
        return ((cx, cy, cz), (nx, ny, nz), max_dev)
    except Exception:
        pass

    # Pure-Python fallback — 3x3 covariance + power iteration on its inverse
    # (smallest eigenvector). Robust enough for the test surfaces.
    cxx = cxy = cxz = cyy = cyz = czz = 0.0
    for x, y, z in pts:
        dx, dy, dz = x - cx, y - cy, z - cz
        cxx += dx * dx
        cxy += dx * dy
        cxz += dx * dz
        cyy += dy * dy
        cyz += dy * dz
        czz += dz * dz
    # Power iteration on (mu * I - C) to get smallest eigenvector.
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
            nx, ny, nz = 0.0, 0.0, 1.0
            break
        vx, vy, vz = nx / m, ny / m, nz / m
    max_dev = 0.0
    for x, y, z in pts:
        d = (x - cx) * vx + (y - cy) * vy + (z - cz) * vz
        if abs(d) > max_dev:
            max_dev = abs(d)
    return ((cx, cy, cz), (vx, vy, vz), max_dev)


@skill(
    name="gdt_flatness",
    category="inspect",
    level="atomic",
    summary="GD&T flatness: sample the face, fit a best-fit plane (SVD), "
            "and report max perpendicular deviation vs tolerance_mm. "
            "Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["gdt_flatness_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class GdtFlatness(SkillBase):
    class Args(BaseModel):
        face_selector: dict
        tolerance_mm: float = Field(default=0.05, ge=0.0)
        samples: int = Field(default=25, ge=4, le=10000)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_faces

        shape = _occt_shape(body)
        sel = _coerce_selector(args.face_selector)
        faces = resolve_faces(shape, sel, body=body)
        if not faces:
            raise ValueError(
                f"gdt_flatness: face_selector matched 0 faces (kind={sel.kind})"
            )
        face = faces[0]

        pts = _sample_face_xyz(face, args.samples)
        origin, normal, max_dev = _best_fit_plane(pts)
        passed = bool(max_dev <= args.tolerance_mm)

        extras = {
            "flatness": {
                "deviation_mm": round(max_dev, 6),
                "pass": passed,
                "tolerance_mm": float(args.tolerance_mm),
                "sample_count": len(pts),
                "plane_origin": [round(c, 6) for c in origin],
                "plane_normal": [round(c, 6) for c in normal],
            }
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
