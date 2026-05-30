"""detect_rotational_symmetry — atomic, read-only.

For each candidate axis (X, Y, Z, or PCA "auto" axes) and each order n in
[2, max_order], rotate the sampled point cloud by 360°/n about the axis and
measure mean nearest-neighbour distance back to the original. Score in [0, 1]
with 1 = perfect rotational symmetry of that order.

Returned list is sorted by score descending so the highest-confidence
(axis, order) pairs are first. A truly axisymmetric body (cylinder) will score
near 1.0 for many orders along its axis — call this "infinite-order"
heuristically.

extras["rotational_axes"] = [
    {"axis_origin":[x,y,z], "axis_dir":[dx,dy,dz], "order":n, "score":0..1},
    ...
]

Body returned unchanged.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult

from phone_designer.skills.inspect.detect_mirror_symmetry import (
    _bbox,
    _centroid,
    _mean_nearest_distance,
    _occt_shape,
    _pca_axes,
    _sample_surface_points,
    _score_from_distance,
)


def _rotate_point(p: tuple[float, float, float],
                   origin: tuple[float, float, float],
                   axis: tuple[float, float, float],
                   angle_rad: float) -> tuple[float, float, float]:
    """Rotate ``p`` about (origin, axis) by ``angle_rad`` (Rodrigues)."""
    ux, uy, uz = axis
    nm = math.sqrt(ux * ux + uy * uy + uz * uz)
    if nm < 1e-12:
        return p
    ux, uy, uz = ux / nm, uy / nm, uz / nm

    px, py, pz = p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    one_c = 1.0 - c

    # Rodrigues rotation matrix * (px, py, pz)
    r00 = c + ux * ux * one_c
    r01 = ux * uy * one_c - uz * s
    r02 = ux * uz * one_c + uy * s
    r10 = uy * ux * one_c + uz * s
    r11 = c + uy * uy * one_c
    r12 = uy * uz * one_c - ux * s
    r20 = uz * ux * one_c - uy * s
    r21 = uz * uy * one_c + ux * s
    r22 = c + uz * uz * one_c

    qx = r00 * px + r01 * py + r02 * pz
    qy = r10 * px + r11 * py + r12 * pz
    qz = r20 * px + r21 * py + r22 * pz
    return (qx + origin[0], qy + origin[1], qz + origin[2])


def _rotate_points_np(pts, origin, axis, angle_rad):
    """numpy-vectorized rotation. Falls back to per-point if numpy missing."""
    try:
        import numpy as np

        ux, uy, uz = axis
        nm = math.sqrt(ux * ux + uy * uy + uz * uz)
        if nm < 1e-12:
            return list(pts)
        ux, uy, uz = ux / nm, uy / nm, uz / nm
        c = math.cos(angle_rad)
        s = math.sin(angle_rad)
        one_c = 1.0 - c
        R = np.array([
            [c + ux * ux * one_c,        ux * uy * one_c - uz * s, ux * uz * one_c + uy * s],
            [uy * ux * one_c + uz * s,   c + uy * uy * one_c,      uy * uz * one_c - ux * s],
            [uz * ux * one_c - uy * s,   uz * uy * one_c + ux * s, c + uz * uz * one_c],
        ])
        arr = np.asarray(pts, dtype=float)
        o = np.asarray(origin, dtype=float)
        out = ((arr - o) @ R.T) + o
        return [tuple(float(x) for x in row) for row in out]
    except Exception:
        return [_rotate_point(p, origin, axis, angle_rad) for p in pts]


def _axis_alignment(d1, d2) -> float:
    """abs(cos) of angle between two direction vectors (treated as ±)."""
    n1 = math.sqrt(d1[0] ** 2 + d1[1] ** 2 + d1[2] ** 2)
    n2 = math.sqrt(d2[0] ** 2 + d2[1] ** 2 + d2[2] ** 2)
    if n1 < 1e-12 or n2 < 1e-12:
        return 0.0
    return abs((d1[0] * d2[0] + d1[1] * d2[1] + d1[2] * d2[2]) / (n1 * n2))


def _surface_of_revolution_axes(shape) -> list[tuple[float, float, float]]:
    """Return the unit axis directions of every cylindrical / conical / spherical
    face on the body — i.e. the axes about which that face is itself
    rotationally symmetric of infinite order.

    Used as a shortcut: if a candidate axis aligns with any of these, the
    body is at least *partially* axisymmetric about that axis, so we cap the
    nearest-neighbour score from below — the discrete mesh polygon count
    (typically 36-gon at default deflection) would otherwise alias for orders
    that don't divide it (e.g. 5, 7, 8 vs a 36-gon).
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import (
        GeomAbs_Cone,
        GeomAbs_Cylinder,
        GeomAbs_Sphere,
        GeomAbs_Torus,
    )

    from phone_designer.skills._resolvers import _all_faces

    axes: list[tuple[float, float, float]] = []
    for f in _all_faces(shape):
        try:
            surf = BRepAdaptor_Surface(f)
            t = surf.GetType()
            if t == GeomAbs_Cylinder:
                d = surf.Cylinder().Axis().Direction()
            elif t == GeomAbs_Cone:
                d = surf.Cone().Axis().Direction()
            elif t == GeomAbs_Torus:
                d = surf.Torus().Axis().Direction()
            elif t == GeomAbs_Sphere:
                # Sphere is rotationally symmetric about any axis through its
                # centre — we just include the local "Axis" direction as a
                # representative; it'd be more accurate to skip and treat
                # spheres specially, but this is fine for the score floor.
                d = surf.Sphere().Position().Axis().Direction()
            else:
                continue
            axes.append((float(d.X()), float(d.Y()), float(d.Z())))
        except Exception:
            continue
    return axes


def _candidate_axes(
    test_axes: list[str],
    pts: list[tuple[float, float, float]],
    bbox_center: tuple[float, float, float],
) -> list[tuple[str, tuple[float, float, float], tuple[float, float, float]]]:
    out: list[tuple[str, tuple[float, float, float], tuple[float, float, float]]] = []
    seen: set[str] = set()
    for tax in test_axes:
        key = tax.upper()
        if key in seen:
            continue
        seen.add(key)
        if key == "X":
            out.append(("X", bbox_center, (1.0, 0.0, 0.0)))
        elif key == "Y":
            out.append(("Y", bbox_center, (0.0, 1.0, 0.0)))
        elif key == "Z":
            out.append(("Z", bbox_center, (0.0, 0.0, 1.0)))
        elif key == "AUTO":
            origin = _centroid(pts) if pts else bbox_center
            for i, ax in enumerate(_pca_axes(pts)):
                out.append((f"auto_pc{i}", origin, ax))
    return out


@skill(
    name="detect_rotational_symmetry",
    category="inspect",
    level="atomic",
    summary="Detect rotational-symmetry axes by rotating a sampled point cloud "
            "by 360°/n for n in [2, max_order] about candidate axes (X/Y/Z or "
            "PCA 'auto') and scoring agreement with the original. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["rotational_axes"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="body_present")],
)
class DetectRotationalSymmetry(SkillBase):
    class Args(BaseModel):
        test_axes: list[str] = Field(default_factory=lambda: ["X", "Y", "Z", "auto"])
        max_order: int = Field(default=12, ge=2, le=64)
        sample_count: int = Field(default=200, ge=10, le=5000)
        linear_deflection_mm: float = Field(default=0.5, gt=0)
        min_score: float = Field(
            default=0.0, ge=0.0, le=1.0,
            description="Drop (axis, order) pairs whose score is below this.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        shape = _occt_shape(body)

        pts = _sample_surface_points(
            shape, target=args.sample_count,
            linear_deflection=args.linear_deflection_mm,
        )
        (mn, mx) = _bbox(shape)
        bbox_center = ((mn[0] + mx[0]) / 2.0,
                       (mn[1] + mx[1]) / 2.0,
                       (mn[2] + mx[2]) / 2.0)
        diag = math.sqrt(
            (mx[0] - mn[0]) ** 2 + (mx[1] - mn[1]) ** 2 + (mx[2] - mn[2]) ** 2,
        )

        # Axes of any surface-of-revolution face on the body. Any candidate
        # axis aligned with one of these is "infinitely" symmetric about it,
        # which we use as a score floor for orders that mesh aliasing would
        # otherwise hurt (e.g. 5/7/8 vs a 36-gon tessellation of a cylinder).
        sor_axes = _surface_of_revolution_axes(shape)

        axes = _candidate_axes(args.test_axes, pts, bbox_center)
        results: list[dict[str, Any]] = []

        for _label, origin, axis in axes:
            # Maximum |cos| to any surface-of-revolution axis — 1.0 means
            # the candidate axis IS an SoR axis.
            sor_align = max(
                (_axis_alignment(axis, sa) for sa in sor_axes),
                default=0.0,
            )

            for n in range(2, int(args.max_order) + 1):
                if not pts:
                    score = 0.0
                    mean_dist = float("inf")
                else:
                    angle = 2.0 * math.pi / float(n)
                    rotated = _rotate_points_np(pts, origin, axis, angle)
                    mean_dist = _mean_nearest_distance(rotated, pts)
                    score = _score_from_distance(mean_dist, diag)

                    # If candidate axis aligns with a surface-of-revolution
                    # axis, the body is exactly symmetric about it at any
                    # order — replace the mesh-aliased score with the SoR
                    # alignment confidence (still ≤ 1.0).
                    if sor_align > 0.999:
                        score = max(score, sor_align)
                if score < args.min_score:
                    continue
                results.append({
                    "axis_origin": [round(c, 6) for c in origin],
                    "axis_dir": [round(c, 6) for c in axis],
                    "order": int(n),
                    "score": round(float(score), 6),
                    "mean_distance_mm": round(
                        float(mean_dist) if math.isfinite(mean_dist) else -1.0, 6,
                    ),
                })

        # Highest score first; tiebreak by lower order (simpler symmetry first).
        results.sort(key=lambda r: (-r["score"], r["order"]))

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"rotational_axes": results},
        )
