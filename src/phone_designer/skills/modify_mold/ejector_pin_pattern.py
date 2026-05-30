"""ejector_pin_pattern — atomic. Distribute N ejector pin positions across
a single face using Lloyd's algorithm (5 relaxation iterations).

This is an **inspection / layout** skill: it reports only the proposed pin
positions in ``extras['ejector_positions']`` — the body is returned
unchanged. Drilling the holes is a separate concern (``ejector_pin_clearance``).

Algorithm:
    1. Resolve the face index in the body's deterministic ``_all_faces``
       list (the ``face_selector`` is a thin wrapper around ``face_idx``).
    2. Compute the planar projection — the face's centroid + outward normal
       defines a plane; the face's parametric UV bbox defines a sampling
       rectangle.
    3. Sample candidate points inside the rectangle, reject those whose
       UV distance to the bbox border is less than ``min_edge_distance_mm``.
    4. Run 5 iterations of Lloyd's relaxation: for each centroid, move it
       toward the centroid of its Voronoi cell (approximated by the average
       of grid samples that are nearest to it).
    5. Convert the final UV coordinates back to 3D points (using the
       BRepAdaptor_Surface's UV parameters) and report them.

extras:
    extras["ejector_positions"] = [[x,y,z], ...]
    extras["ejector_pin_diameter_mm"] = float
    extras["ejector_face_idx"]        = int
"""
from __future__ import annotations

import math
import random
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


class _FaceSelector(BaseModel):
    """Minimal selector — pick a face by deterministic index in _all_faces."""
    face_idx: int = Field(..., ge=0)


def _surface_uv_bbox(face) -> tuple[float, float, float, float]:
    """Return (u0, v0, u1, v1) parametric bbox for the face's surface."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    surf = BRepAdaptor_Surface(face)
    return (
        surf.FirstUParameter(),
        surf.FirstVParameter(),
        surf.LastUParameter(),
        surf.LastVParameter(),
    )


def _surface_point_at_uv(face, u: float, v: float) -> tuple[float, float, float]:
    """Evaluate the underlying surface at (u, v)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    surf = BRepAdaptor_Surface(face)
    p = surf.Value(u, v)
    return (p.X(), p.Y(), p.Z())


def _lloyds_relaxation(
    seeds_uv: list[tuple[float, float]],
    u0: float, v0: float, u1: float, v1: float,
    iterations: int,
    grid_resolution: int = 24,
) -> list[tuple[float, float]]:
    """5-iteration Lloyd's: move each seed to the centroid of its Voronoi cell.

    Voronoi cells are approximated by sampling a `grid_resolution x grid_resolution`
    grid and assigning each grid point to its nearest seed.
    """
    if not seeds_uv:
        return seeds_uv
    pts = [tuple(p) for p in seeds_uv]
    for _ in range(iterations):
        # accumulate sum / count per seed
        sums = [[0.0, 0.0, 0] for _ in pts]
        for gi in range(grid_resolution):
            gu = u0 + (u1 - u0) * (gi + 0.5) / grid_resolution
            for gj in range(grid_resolution):
                gv = v0 + (v1 - v0) * (gj + 0.5) / grid_resolution
                # nearest seed
                best_k = 0
                best_d = float("inf")
                for k, (pu, pv) in enumerate(pts):
                    du = gu - pu
                    dv = gv - pv
                    d = du * du + dv * dv
                    if d < best_d:
                        best_d = d
                        best_k = k
                sums[best_k][0] += gu
                sums[best_k][1] += gv
                sums[best_k][2] += 1
        new_pts = []
        for k, (su, sv, cnt) in enumerate(sums):
            if cnt == 0:
                new_pts.append(pts[k])
            else:
                new_pts.append((su / cnt, sv / cnt))
        pts = new_pts
    return pts


@skill(
    name="ejector_pin_pattern",
    category="modify/mold",
    level="atomic",
    summary="Distribute ejector pin positions evenly across the selected "
            "face via Lloyd's relaxation (5 iterations). Reports positions "
            "only — body is unchanged. Use ejector_pin_clearance to actually "
            "drill the holes.",
    selector_kinds=["face_named"],
    history_rules={},
    produces_features=["ejector_pin_pattern"],
    preserves=["body_topology"],
    manufacturing={
        "injection_mold_pa": {"extras": {"ejector_aware": True}},
    },
    failure_modes=["fm.face_too_small", "fm.no_face_found"],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="body_present")],
)
class EjectorPinPattern(SkillBase):
    class Args(BaseModel):
        face_selector: _FaceSelector
        pin_diameter_mm: float = Field(default=2.0, gt=0.0, le=50.0)
        target_count: int = Field(default=4, ge=1, le=200)
        min_edge_distance_mm: float = Field(default=5.0, ge=0.0, le=500.0)
        random_seed: int = Field(default=42, description="seed for initial layout")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        shape = _occt_shape(body)
        faces = _all_faces(shape)
        idx = args.face_selector.face_idx
        if idx >= len(faces):
            raise RuntimeError(
                f"ejector_pin_pattern: face_idx {idx} out of range "
                f"(body has {len(faces)} faces)"
            )
        face = faces[idx]

        u0, v0, u1, v1 = _surface_uv_bbox(face)
        if not all(math.isfinite(x) for x in (u0, v0, u1, v1)):
            raise RuntimeError(
                "ejector_pin_pattern: face has infinite parametric domain"
            )

        du = u1 - u0
        dv = v1 - v0
        if du <= 0.0 or dv <= 0.0:
            raise RuntimeError(
                "ejector_pin_pattern: degenerate UV bbox on selected face"
            )

        # Convert min_edge_distance_mm (a 3D length) into a UV-space margin
        # using a rough metric derived from two diagonal corners.
        p00 = _surface_point_at_uv(face, u0, v0)
        p11 = _surface_point_at_uv(face, u1, v1)
        diag_3d = math.sqrt(
            (p11[0] - p00[0]) ** 2
            + (p11[1] - p00[1]) ** 2
            + (p11[2] - p00[2]) ** 2,
        )
        diag_uv = math.sqrt(du * du + dv * dv)
        if diag_3d <= 0.0:
            raise RuntimeError(
                "ejector_pin_pattern: face is too small to place pins"
            )
        uv_per_mm = diag_uv / diag_3d
        edge_margin_uv = args.min_edge_distance_mm * uv_per_mm

        # Shrink the valid sampling rectangle by the edge margin.
        su0 = u0 + edge_margin_uv
        sv0 = v0 + edge_margin_uv
        su1 = u1 - edge_margin_uv
        sv1 = v1 - edge_margin_uv
        if su1 <= su0 or sv1 <= sv0:
            raise RuntimeError(
                "ejector_pin_pattern: min_edge_distance_mm too large for "
                "this face — no valid placement region"
            )

        # Initial seeds: deterministic pseudo-random scatter.
        rnd = random.Random(args.random_seed)
        seeds = []
        for _ in range(args.target_count):
            uu = su0 + (su1 - su0) * rnd.random()
            vv = sv0 + (sv1 - sv0) * rnd.random()
            seeds.append((uu, vv))

        # 5 Lloyd relaxation iterations confined to the shrunk rectangle.
        relaxed = _lloyds_relaxation(seeds, su0, sv0, su1, sv1, iterations=5)

        # Convert UV back to 3D.
        positions: list[list[float]] = []
        for (uu, vv) in relaxed:
            # clamp to shrunk bbox in case Lloyd's drifted slightly
            uu = max(su0, min(su1, uu))
            vv = max(sv0, min(sv1, vv))
            try:
                pt = _surface_point_at_uv(face, uu, vv)
            except Exception:
                continue
            positions.append([round(pt[0], 4), round(pt[1], 4), round(pt[2], 4)])

        extras = {
            "ejector_positions":       positions,
            "ejector_pin_diameter_mm": args.pin_diameter_mm,
            "ejector_face_idx":        idx,
        }

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
