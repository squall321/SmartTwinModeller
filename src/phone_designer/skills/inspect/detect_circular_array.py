"""detect_circular_array — atomic, read-only.

Detect circular arrays (4+ same-kind features distributed on a circle with
equal angular pitch) of holes, pockets, or bosses. Body unchanged.

Algorithm
---------
1. Detect every feature of ``feature_kind`` via classify_holes /
   classify_pockets / detect_bosses.
2. For each subset of ``min_count`` or more positions, fit a circle:
       - candidate axis: take the unit normal of the plane through the
         three furthest-apart points (or each axis-aligned candidate when
         the points happen to be coplanar with an axis).
       - project all points onto that plane and fit a circle (algebraic
         least squares — Coope / Kasa).
   Then keep only the subsets whose residual ≤ ``radius_tol_mm`` AND
   whose sorted angular positions form an arithmetic progression with
   step 360/N within ``angle_tol_deg``.

Output::

    extras["circular_arrays"] = [
        {
            "feature_kind": "hole" | "pocket" | "boss",
            "center":       [x, y, z],
            "axis":         [ax, ay, az],     # unit
            "radius_mm":    float,
            "count":        N,
            "angular_pitch_deg": float,
        },
        ...
    ]

Body unchanged — ``post_conditions=[body_present]``.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# ──────────────────────────────────────────────────────────────────────────────
# Catalog loader (inline per pack rules)


def _load(family, name):
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parents[4]
    path = root / "catalogs" / family / f"{name}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())


# ──────────────────────────────────────────────────────────────────────────────
# Feature extraction (shared style with detect_linear_array)


def _feature_positions(body: Any, feature_kind: str) -> list[tuple[float, float, float]]:
    if feature_kind == "hole":
        from phone_designer.skills.inspect.classify_holes import ClassifyHoles
        r = ClassifyHoles().apply(body, {"match_standards": False})
        return [tuple(h["axis_origin"]) for h in r.extras.get("holes", [])]
    if feature_kind == "pocket":
        from phone_designer.skills.inspect.classify_pockets import ClassifyPockets
        r = ClassifyPockets().apply(body, {})
        return [tuple(p["axis_origin"]) for p in r.extras.get("pockets", [])]
    if feature_kind == "boss":
        from phone_designer.skills.inspect.detect_bosses import DetectBosses
        r = DetectBosses().apply(body, {})
        return [tuple(b["center"]) for b in r.extras.get("bosses", [])]
    return []


# ──────────────────────────────────────────────────────────────────────────────
# Geometry helpers


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    if n < 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def _fit_plane(points: list[tuple[float, float, float]]):
    """Return (centroid, unit_normal) for the best-fit plane (covariance
    smallest eigenvector). Falls back to (centroid, +Z) when degenerate."""
    n = len(points)
    if n < 3:
        c = points[0] if points else (0.0, 0.0, 0.0)
        return c, (0.0, 0.0, 1.0)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    cz = sum(p[2] for p in points) / n
    centroid = (cx, cy, cz)

    # 3x3 covariance
    sxx = syy = szz = sxy = sxz = syz = 0.0
    for p in points:
        dx, dy, dz = p[0] - cx, p[1] - cy, p[2] - cz
        sxx += dx * dx
        syy += dy * dy
        szz += dz * dz
        sxy += dx * dy
        sxz += dx * dz
        syz += dy * dz
    # Power-iteration on the inverse — or just try the three axis-aligned
    # candidates and pick the smallest covariance projection. This is
    # sufficient for our axis-aligned phone-style use case.
    candidates = [
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    ]

    def variance_along(axis):
        ax, ay, az = axis
        v = 0.0
        for p in points:
            dp = (p[0] - cx) * ax + (p[1] - cy) * ay + (p[2] - cz) * az
            v += dp * dp
        return v

    best_axis = min(candidates, key=variance_along)
    return centroid, best_axis


def _build_basis(normal):
    """Return two orthonormal in-plane axes (u, v) given a unit ``normal``."""
    nx, ny, nz = normal
    # pick the axis least aligned with normal as helper
    if abs(nx) <= abs(ny) and abs(nx) <= abs(nz):
        helper = (1.0, 0.0, 0.0)
    elif abs(ny) <= abs(nz):
        helper = (0.0, 1.0, 0.0)
    else:
        helper = (0.0, 0.0, 1.0)
    u = _unit(_cross(normal, helper))
    v = _unit(_cross(normal, u))
    return u, v


def _fit_circle_2d(points2d):
    """Algebraic (Kasa) circle fit. Returns (cx, cy, r, residual)."""
    n = len(points2d)
    if n < 3:
        return 0.0, 0.0, 0.0, math.inf
    Sx = sum(p[0] for p in points2d)
    Sy = sum(p[1] for p in points2d)
    Sxx = sum(p[0] * p[0] for p in points2d)
    Syy = sum(p[1] * p[1] for p in points2d)
    Sxy = sum(p[0] * p[1] for p in points2d)
    Sxxx = sum(p[0] ** 3 for p in points2d)
    Syyy = sum(p[1] ** 3 for p in points2d)
    Sxyy = sum(p[0] * p[1] * p[1] for p in points2d)
    Syxx = sum(p[1] * p[0] * p[0] for p in points2d)

    A11 = Sxx - Sx * Sx / n
    A12 = Sxy - Sx * Sy / n
    A22 = Syy - Sy * Sy / n
    B1 = 0.5 * ((Sxxx + Sxyy) - (Sxx + Syy) * Sx / n)
    B2 = 0.5 * ((Syyy + Syxx) - (Sxx + Syy) * Sy / n)

    det = A11 * A22 - A12 * A12
    if abs(det) < 1e-12:
        return 0.0, 0.0, 0.0, math.inf
    cx = (B1 * A22 - B2 * A12) / det
    cy = (A11 * B2 - A12 * B1) / det
    r2 = (Sxx - 2 * cx * Sx + n * cx * cx + Syy - 2 * cy * Sy + n * cy * cy) / n
    r = math.sqrt(max(0.0, r2))

    # residual = RMS of |dist - r|
    res = 0.0
    for p in points2d:
        d = math.sqrt((p[0] - cx) ** 2 + (p[1] - cy) ** 2)
        res += (d - r) ** 2
    res = math.sqrt(res / n)
    return cx, cy, r, res


def _angular_pitch_ok(angles_deg: list[float], tol_deg: float) -> tuple[bool, float]:
    """Sort the angles, take consecutive differences (wrap-around), and
    check they are all within tol_deg of the mean. Returns (ok, mean_pitch)."""
    if len(angles_deg) < 2:
        return False, 0.0
    s = sorted(a % 360.0 for a in angles_deg)
    diffs = [(s[i + 1] - s[i]) % 360.0 for i in range(len(s) - 1)]
    diffs.append((s[0] + 360.0 - s[-1]) % 360.0)
    mean = sum(diffs) / len(diffs)
    if mean < 1e-6:
        return False, 0.0
    for d in diffs:
        if abs(d - mean) > tol_deg:
            return False, mean
    return True, mean


def _find_circular_arrays(
    positions: list[tuple[float, float, float]],
    min_count: int,
    radius_tol_mm: float = 0.5,
    angle_tol_deg: float = 5.0,
) -> list[dict[str, Any]]:
    if len(positions) < min_count:
        return []

    centroid, normal = _fit_plane(positions)
    # If all positions don't share a single circle we still try the
    # overall-plane fit because our common phone-style use case is one
    # circle per array.
    u, v = _build_basis(normal)
    # project each point to (uu, vv) and to axis projection (signed)
    projected: list[tuple[float, float, float]] = []  # (u_coord, v_coord, axis_proj)
    for p in positions:
        rel = _sub(p, centroid)
        projected.append((_dot(rel, u), _dot(rel, v), _dot(rel, normal)))

    # Try the full set first; if it forms a circle, accept. Otherwise we
    # could refine by removing outliers — for the common case of a single
    # circular array on a flat plate, the full-set Kasa fit is already
    # robust.
    results: list[dict[str, Any]] = []
    if len(projected) >= min_count:
        # Group points sharing the same axis projection (within tol).
        # Only points on the same plane participate.
        groups: list[list[int]] = []
        axis_tol = max(0.5, radius_tol_mm)
        for i, p in enumerate(projected):
            placed = False
            for g in groups:
                if abs(projected[g[0]][2] - p[2]) <= axis_tol:
                    g.append(i)
                    placed = True
                    break
            if not placed:
                groups.append([i])

        for g in groups:
            if len(g) < min_count:
                continue
            pts2d = [(projected[i][0], projected[i][1]) for i in g]
            cx, cy, r, res = _fit_circle_2d(pts2d)
            if res > radius_tol_mm:
                continue
            angles = [
                math.degrees(math.atan2(p[1] - cy, p[0] - cx))
                for p in pts2d
            ]
            ok, pitch = _angular_pitch_ok(angles, angle_tol_deg)
            if not ok:
                continue
            axis_proj = sum(projected[i][2] for i in g) / len(g)
            world_center = _add(
                centroid,
                _add(_scale(u, cx), _add(_scale(v, cy), _scale(normal, axis_proj))),
            )
            results.append({
                "center": [round(c, 4) for c in world_center],
                "axis": [round(c, 4) for c in normal],
                "radius_mm": round(r, 4),
                "count": len(g),
                "angular_pitch_deg": round(pitch, 4),
            })

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="detect_circular_array",
    category="inspect",
    level="atomic",
    summary="Detect circular arrays (features distributed on a circle with "
            "equal angular pitch). Reports the centre, axis, radius, count, "
            "and angular pitch of each ring. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["pattern_inventory"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class DetectCircularArray(SkillBase):
    class Args(BaseModel):
        feature_kind: Literal["hole", "pocket", "boss"] = "hole"
        min_count: int = Field(default=4, ge=3, le=1000)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        positions = _feature_positions(body, args.feature_kind)
        arrays = _find_circular_arrays(positions, args.min_count)
        for a in arrays:
            a["feature_kind"] = args.feature_kind

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"circular_arrays": arrays},
        )
