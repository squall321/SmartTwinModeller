"""bolt_pattern_recognize — atomic, read-only.

face 위 (또는 한 component 위) 의 hole 들을 찾아내고 그 분포가
``linear / circular / grid / random`` 중 어느 패턴인지 분류한다. 결과는
``extras["bolt_pattern"] = {"kind": ..., "params": {...}}`` 에 저장. body 는
그대로 반환.

Detection logic
---------------
1. 입력 face_selector 로 face(들) 을 잡고, 그 face 위에 ``접해 있는`` cylindrical
   hole 의 중심점들을 추출. (face 가 base plate 의 top 면이면 그 face 의 외곽
   loop 안에 있는 cyl_axis 들.)
2. 점이 0 개 → "none" 반환. 1 개 → "single" (linear with n=1).
3. linear: 직선 fit 잔차의 max < 0.5 mm 면 linear. params = {axis_origin,
   axis_dir, count, pitch_mm}.
4. circular: 모든 점이 같은 원 (centre, radius) 위에 ±0.5 mm. params =
   {centre, radius_mm, count, angles_deg}.
5. grid: linear 두 축의 곱(>= 2 x 2). params = {n_x, n_y, pitch_x_mm, pitch_y_mm}.
6. else "random". params = {count, points}.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _all_faces(shape) -> list:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_MapOfShape

    seen = TopTools_MapOfShape()
    out = []
    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        f = it.Current()
        if not seen.Contains(f):
            seen.Add(f)
            out.append(TopoDS.Face_s(f))
        it.Next()
    return out


def _cylinder_info(face):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cylinder:
        return None
    cyl = surf.Cylinder()
    loc = cyl.Location()
    d = cyl.Axis().Direction()
    dx, dy, dz = d.X(), d.Y(), d.Z()
    mag = math.sqrt(dx * dx + dy * dy + dz * dz)
    if mag > 1e-12:
        dx, dy, dz = dx / mag, dy / mag, dz / mag
    return (loc.X(), loc.Y(), loc.Z()), (dx, dy, dz), float(cyl.Radius())


def _plane_info(face):
    """(plane_origin, plane_normal) for a planar face, else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_REVERSED

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Plane:
        return None
    pl = surf.Plane()
    loc = pl.Location()
    n = pl.Axis().Direction()
    nx, ny, nz = n.X(), n.Y(), n.Z()
    if face.Orientation() == TopAbs_REVERSED:
        nx, ny, nz = -nx, -ny, -nz
    return (loc.X(), loc.Y(), loc.Z()), (nx, ny, nz)


def _project_to_plane(point, plane_origin, plane_normal):
    """Drop point onto plane(origin, normal). Returns (px, py, pz)."""
    vx = point[0] - plane_origin[0]
    vy = point[1] - plane_origin[1]
    vz = point[2] - plane_origin[2]
    d = vx * plane_normal[0] + vy * plane_normal[1] + vz * plane_normal[2]
    return (
        point[0] - d * plane_normal[0],
        point[1] - d * plane_normal[1],
        point[2] - d * plane_normal[2],
    )


def _orthonormal_basis(n: tuple[float, float, float]):
    """Pick two orthonormal vectors spanning the plane with normal n."""
    # Choose a helper vector not parallel to n.
    helper = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    # u = normalize(helper x n)
    ux = helper[1] * n[2] - helper[2] * n[1]
    uy = helper[2] * n[0] - helper[0] * n[2]
    uz = helper[0] * n[1] - helper[1] * n[0]
    um = math.sqrt(ux * ux + uy * uy + uz * uz)
    if um < 1e-12:
        # fallback
        u = (1.0, 0.0, 0.0)
    else:
        u = (ux / um, uy / um, uz / um)
    # v = n x u
    v = (
        n[1] * u[2] - n[2] * u[1],
        n[2] * u[0] - n[0] * u[2],
        n[0] * u[1] - n[1] * u[0],
    )
    return u, v


def _classify_pattern(points2d: list[tuple[float, float]]) -> dict[str, Any]:
    """2D point cloud → {kind, params}."""
    n = len(points2d)
    if n == 0:
        return {"kind": "none", "params": {"count": 0, "points": []}}
    if n == 1:
        return {
            "kind": "linear",
            "params": {
                "count": 1,
                "pitch_mm": 0.0,
                "points": [[round(points2d[0][0], 4), round(points2d[0][1], 4)]],
            },
        }

    pts = [(float(x), float(y)) for x, y in points2d]

    # ---- circular fit (algebraic) ----
    # |p - c|^2 = r^2  →  2 cx px + 2 cy py + (r^2 - cx^2 - cy^2) = px^2 + py^2
    # Linear LSQ over (cx, cy, k=r^2 - cx^2 - cy^2).
    if n >= 3:
        import numpy as _np
        A = _np.array(
            [[2.0 * px, 2.0 * py, 1.0] for (px, py) in pts], dtype=float,
        )
        b = _np.array([px * px + py * py for (px, py) in pts], dtype=float)
        try:
            sol, *_ = _np.linalg.lstsq(A, b, rcond=None)
            cx, cy, kk = sol
            r2 = kk + cx * cx + cy * cy
            if r2 > 0.0:
                r = math.sqrt(r2)
                # residuals
                resids = [
                    abs(math.sqrt((px - cx) ** 2 + (py - cy) ** 2) - r)
                    for (px, py) in pts
                ]
                if max(resids) < 0.5 and r > 0.5:
                    angles = sorted(
                        math.degrees(math.atan2(py - cy, px - cx)) for (px, py) in pts
                    )
                    return {
                        "kind": "circular",
                        "params": {
                            "centre": [round(cx, 4), round(cy, 4)],
                            "radius_mm": round(r, 4),
                            "count": n,
                            "angles_deg": [round(a, 3) for a in angles],
                        },
                    }
        except Exception:
            pass

    # ---- linear fit ----
    # PCA: find dominant direction; residuals = distance from line.
    mx = sum(px for px, _ in pts) / n
    my = sum(py for _, py in pts) / n
    sxx = sum((px - mx) ** 2 for px, _ in pts)
    syy = sum((py - my) ** 2 for _, py in pts)
    sxy = sum((px - mx) * (py - my) for px, py in pts)
    # 2x2 covariance matrix [[sxx,sxy],[sxy,syy]]; eigenvector with bigger eigval.
    tr = sxx + syy
    det = sxx * syy - sxy * sxy
    disc = max(0.0, tr * tr / 4.0 - det)
    lam1 = tr / 2.0 + math.sqrt(disc)
    # principal direction
    if abs(sxy) > 1e-12:
        dxv, dyv = lam1 - syy, sxy
    else:
        if sxx >= syy:
            dxv, dyv = 1.0, 0.0
        else:
            dxv, dyv = 0.0, 1.0
    dmag = math.sqrt(dxv * dxv + dyv * dyv)
    if dmag < 1e-12:
        dxv, dyv = 1.0, 0.0
    else:
        dxv, dyv = dxv / dmag, dyv / dmag
    # perpendicular dist of each point to the line through (mx, my) with dir (dxv, dyv)
    nxv, nyv = -dyv, dxv
    perps = [abs((px - mx) * nxv + (py - my) * nyv) for px, py in pts]

    if max(perps) < 0.5:
        # Linear pattern. pitch = mean of sorted projection gaps.
        projs = sorted((px - mx) * dxv + (py - my) * dyv for px, py in pts)
        if n >= 2:
            gaps = [projs[i + 1] - projs[i] for i in range(n - 1)]
            pitch = sum(gaps) / len(gaps)
        else:
            pitch = 0.0
        return {
            "kind": "linear",
            "params": {
                "count": n,
                "pitch_mm": round(pitch, 4),
                "axis_dir_2d": [round(dxv, 4), round(dyv, 4)],
                "anchor_2d": [round(mx, 4), round(my, 4)],
            },
        }

    # ---- grid fit ----
    # Try to find two near-perpendicular linear axes with consistent pitch.
    # Heuristic: round each point's x and y to the nearest cluster and check
    # that all combos are present (n == n_x * n_y).
    xs_sorted = sorted({round(px, 3) for px, _ in pts})
    ys_sorted = sorted({round(py, 3) for _, py in pts})
    # cluster within 0.5 mm
    def _cluster(values: list[float], tol: float) -> list[float]:
        if not values:
            return []
        out = [values[0]]
        for v in values[1:]:
            if abs(v - out[-1]) > tol:
                out.append(v)
        return out
    xs_c = _cluster(xs_sorted, 0.5)
    ys_c = _cluster(ys_sorted, 0.5)
    n_x, n_y = len(xs_c), len(ys_c)
    if n_x >= 2 and n_y >= 2 and n_x * n_y == n:
        # confirm every (xi, yj) is occupied
        ok = True
        for xi in xs_c:
            for yj in ys_c:
                # any point within 0.5 mm?
                if not any(
                    abs(px - xi) < 0.5 and abs(py - yj) < 0.5 for px, py in pts
                ):
                    ok = False
                    break
            if not ok:
                break
        if ok:
            pitch_x = (xs_c[-1] - xs_c[0]) / max(1, n_x - 1)
            pitch_y = (ys_c[-1] - ys_c[0]) / max(1, n_y - 1)
            return {
                "kind": "grid",
                "params": {
                    "n_x": n_x,
                    "n_y": n_y,
                    "pitch_x_mm": round(pitch_x, 4),
                    "pitch_y_mm": round(pitch_y, 4),
                    "count": n,
                },
            }

    # Fallback: random
    return {
        "kind": "random",
        "params": {
            "count": n,
            "points": [[round(px, 4), round(py, 4)] for px, py in pts],
        },
    }


@skill(
    name="bolt_pattern_recognize",
    category="assembly",
    level="atomic",
    summary="Find cylindrical hole centres on the given face(s) and classify their "
            "layout as linear / circular / grid / random. Result is attached to "
            "result.extras['bolt_pattern']; body is unchanged.",
    selector_kinds=["faces"],
    history_rules={},
    produces_features=["bolt_pattern_report"],
    preserves=["assembly_topology", "body_topology"],
    manufacturing={},
    failure_modes=["fm.selector_no_match"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class BoltPatternRecognize(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef = Field(
            description="Planar face whose hole positions are to be analyzed.",
        )
        radius_filter_min_mm: float | None = Field(
            default=None,
            description="If set, only cylinders with radius >= this are used.",
        )
        radius_filter_max_mm: float | None = Field(
            default=None,
            description="If set, only cylinders with radius <= this are used.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_faces

        if body is None:
            raise RuntimeError("bolt_pattern_recognize: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body

        sel_faces = resolve_faces(shape, args.face_selector)
        if not sel_faces:
            raise RuntimeError(
                f"bolt_pattern_recognize: selector matched 0 faces "
                f"({args.face_selector.model_dump()})"
            )

        # Use the first planar face's frame for projection.
        plane = None
        for f in sel_faces:
            p = _plane_info(f)
            if p is not None:
                plane = p
                break
        if plane is None:
            raise RuntimeError(
                "bolt_pattern_recognize: selector face(s) are not planar"
            )
        plane_origin, plane_normal = plane
        u, v = _orthonormal_basis(plane_normal)

        # Collect all cylindrical face centres on the whole body.
        all_holes: list[tuple[float, float, float]] = []
        for f in _all_faces(shape):
            info = _cylinder_info(f)
            if info is None:
                continue
            origin, _axis, r = info
            if args.radius_filter_min_mm is not None and r < args.radius_filter_min_mm:
                continue
            if args.radius_filter_max_mm is not None and r > args.radius_filter_max_mm:
                continue
            # Project axis origin to plane; only keep ones close to plane.
            proj = _project_to_plane(origin, plane_origin, plane_normal)
            # distance from plane
            d_off = math.sqrt(
                sum((origin[i] - proj[i]) ** 2 for i in range(3))
            )
            # Only keep holes whose axis origin is within 50 mm of the plane —
            # ample for the same face's holes but excludes holes on the back
            # of the part. (Hole `axis_origin` from OCCT can land deep inside
            # the body.)
            if d_off > 50.0:
                continue
            all_holes.append(origin)

        # 2D coordinates in plane frame.
        points2d: list[tuple[float, float]] = []
        for origin in all_holes:
            proj = _project_to_plane(origin, plane_origin, plane_normal)
            vx = proj[0] - plane_origin[0]
            vy = proj[1] - plane_origin[1]
            vz = proj[2] - plane_origin[2]
            pu = vx * u[0] + vy * u[1] + vz * u[2]
            pv = vx * v[0] + vy * v[1] + vz * v[2]
            points2d.append((pu, pv))

        # Dedup near-duplicates (within 0.1 mm).
        deduped: list[tuple[float, float]] = []
        for p in points2d:
            if not any(
                abs(p[0] - q[0]) < 0.1 and abs(p[1] - q[1]) < 0.1 for q in deduped
            ):
                deduped.append(p)

        report = _classify_pattern(deduped)

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "bolt_pattern": report,
                "hole_count": len(deduped),
                "plane_origin": [round(c, 4) for c in plane_origin],
                "plane_normal": [round(c, 4) for c in plane_normal],
            },
        )
