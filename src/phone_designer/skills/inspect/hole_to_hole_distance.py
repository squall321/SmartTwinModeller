"""hole_to_hole_distance — atomic, read-only.

Detect every cylindrical face on the body (treat each as a "hole" or boss
shaft) and compute pairwise distances between their axis lines. For each
unordered (i, j) pair with i < j, distance is:

  - perpendicular line-to-line distance if the two axes are non-parallel
    (cross product magnitude / |a × b|)
  - parallel-axis distance (point-to-line) if axes are within
    ``parallel_axis_tolerance_deg`` of each other

Useful for the LLM to dimension hole patterns (e.g., "two M3 holes
38.5 mm apart") without manually selecting each face.

extras schema:
    {
      "hole_count": int,
      "holes": [
        {"index": i,
         "center": [x,y,z],
         "radius_mm": float,
         "axis": [x,y,z],
         "axis_origin": [x,y,z]},
        ...
      ],
      "hole_distances": [
        (i, j, distance_mm),       # tuples, i < j
        ...
      ],
      "pair_details": [
        {"i": i, "j": j,
         "distance_mm": float,
         "kind": "parallel" | "skew" | "intersecting",
         "axis_angle_deg": float},
        ...
      ]
    }

body unchanged (post ``body_present``).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _collect_cylinders(shape) -> list[dict[str, Any]]:
    """Collect every cylindrical face → (center, radius, axis_dir, axis_origin)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    from phone_designer.skills._resolvers import _all_faces, _face_center

    holes: list[dict[str, Any]] = []
    for f in _all_faces(shape):
        try:
            surf = BRepAdaptor_Surface(f)
            if surf.GetType() != GeomAbs_Cylinder:
                continue
            cyl = surf.Cylinder()
            ax = cyl.Axis().Direction()
            loc = cyl.Location()
            r = float(cyl.Radius())
            c = _face_center(f)
        except Exception:
            continue
        # unit-length axis
        mag = math.sqrt(ax.X() ** 2 + ax.Y() ** 2 + ax.Z() ** 2)
        if mag < 1e-12:
            continue
        axis = (ax.X() / mag, ax.Y() / mag, ax.Z() / mag)
        holes.append({
            "center": c,
            "radius_mm": r,
            "axis": axis,
            "axis_origin": (loc.X(), loc.Y(), loc.Z()),
        })
    return holes


def _line_line_distance(
    p1: tuple[float, float, float],
    d1: tuple[float, float, float],
    p2: tuple[float, float, float],
    d2: tuple[float, float, float],
    parallel_cos_tol: float,
) -> tuple[float, str, float]:
    """Return (distance, kind, axis_angle_deg).

    kind ∈ {"parallel", "skew", "intersecting"}.
    """
    # angle between axes (unsigned)
    dot = d1[0] * d2[0] + d1[1] * d2[1] + d1[2] * d2[2]
    abs_dot = abs(dot)
    abs_dot = max(0.0, min(1.0, abs_dot))
    angle_deg = math.degrees(math.acos(abs_dot))

    # cross product d1 × d2
    cx = d1[1] * d2[2] - d1[2] * d2[1]
    cy = d1[2] * d2[0] - d1[0] * d2[2]
    cz = d1[0] * d2[1] - d1[1] * d2[0]
    cross_mag = math.sqrt(cx * cx + cy * cy + cz * cz)

    if abs_dot >= parallel_cos_tol or cross_mag < 1e-9:
        # Parallel — point-to-line distance from p2 to (p1, d1)
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        dz = p2[2] - p1[2]
        # |(p2-p1) × d1|  (|d1|=1)
        ex = dy * d1[2] - dz * d1[1]
        ey = dz * d1[0] - dx * d1[2]
        ez = dx * d1[1] - dy * d1[0]
        d = math.sqrt(ex * ex + ey * ey + ez * ez)
        return d, "parallel", angle_deg

    # Skew or intersecting: distance = |((p2-p1) · (d1×d2))| / |d1×d2|
    wx = p2[0] - p1[0]
    wy = p2[1] - p1[1]
    wz = p2[2] - p1[2]
    triple = abs(wx * cx + wy * cy + wz * cz) / cross_mag
    kind = "intersecting" if triple < 1e-6 else "skew"
    return triple, kind, angle_deg


@skill(
    name="hole_to_hole_distance",
    category="inspect",
    level="atomic",
    summary="Detect all cylindrical faces on the body and report pairwise "
            "axis-line distances. For parallel pairs uses point-to-line "
            "distance; for skew pairs uses common-perpendicular distance. "
            "Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["hole_pair_distances"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class HoleToHoleDistance(SkillBase):
    class Args(BaseModel):
        parallel_axis_tolerance_deg: float = Field(default=1.0, ge=0.0, le=90.0)
        max_pairs: int = Field(default=200, ge=1, le=10_000)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        shape = _occt_shape(body)
        holes = _collect_cylinders(shape)

        parallel_cos_tol = math.cos(math.radians(args.parallel_axis_tolerance_deg))

        rows = []
        for i, h in enumerate(holes):
            rows.append({
                "index": i,
                "center": [round(c, 4) for c in h["center"]],
                "radius_mm": round(h["radius_mm"], 4),
                "axis": [round(c, 4) for c in h["axis"]],
                "axis_origin": [round(c, 4) for c in h["axis_origin"]],
            })

        distances: list[tuple[int, int, float]] = []
        details: list[dict[str, Any]] = []
        n = len(holes)
        for i in range(n):
            for j in range(i + 1, n):
                if len(distances) >= args.max_pairs:
                    break
                d, kind, ang = _line_line_distance(
                    holes[i]["axis_origin"], holes[i]["axis"],
                    holes[j]["axis_origin"], holes[j]["axis"],
                    parallel_cos_tol,
                )
                distances.append((i, j, round(d, 6)))
                details.append({
                    "i": i, "j": j,
                    "distance_mm": round(d, 6),
                    "kind": kind,
                    "axis_angle_deg": round(ang, 4),
                })
            if len(distances) >= args.max_pairs:
                break

        extras = {
            "hole_count": len(holes),
            "holes": rows,
            "hole_distances": distances,
            "pair_details": details,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
