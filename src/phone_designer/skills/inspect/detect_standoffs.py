"""detect_standoffs — atomic, read-only.

A standoff is a boss with a central concentric through / blind hole
(``boss_with_hole`` macro output, or the equivalent CNC pattern). This
skill pairs each cylindrical boss detected by ``detect_bosses`` with a
hole face whose axis is collinear with the boss axis.

Algorithm
---------
1. Run ``detect_bosses`` → get cylindrical boss list.
2. Run ``find_features`` → get every cylindrical hole face (inward-
   pointing cylinder).
3. For each cylindrical boss compute its axis and centerline. For each
   hole compute its axis and origin. Pair them when:
     - axes are parallel (|axis_boss · axis_hole| > 0.99), and
     - axis lines are collinear within ``axis_distance_tol_mm`` (= 0.1 mm)
4. Reject pairs where the hole diameter ≥ boss diameter (in that case
   it's just a through-hole, not a standoff).

extras schema::

    extras["standoffs"] = [
        {
            "boss_id": int,
            "hole_id": int,              # index into the find_features
                                          # 'features' list of kind=='hole'
            "axis": [x, y, z],           # unit axis vector
            "boss_diameter_mm": float,
            "hole_diameter_mm": float,
        },
        ...
    ]
    extras["standoff_count"] = int

Body unchanged — ``post_conditions=[body_present]``.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    mag = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if mag < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / mag, v[1] / mag, v[2] / mag)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _vec(p, q):
    return (q[0] - p[0], q[1] - p[1], q[2] - p[2])


def _vmag(v) -> float:
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _line_distance(p1, d1, p2, d2) -> float:
    """Minimum distance between two infinite lines.

    Parallel case (|d1 × d2| ≈ 0): return |(p2 - p1) - ((p2-p1)·d1) d1|.
    Skew case: |((p2 - p1) · (d1 × d2))| / |d1 × d2|.
    """
    d1 = _normalize(d1)
    d2 = _normalize(d2)
    cross = _cross(d1, d2)
    cm = _vmag(cross)
    diff = _vec(p1, p2)
    if cm < 1e-6:
        # parallel — project diff onto plane perpendicular to d1
        proj = diff[0] * d1[0] + diff[1] * d1[1] + diff[2] * d1[2]
        perp = (
            diff[0] - proj * d1[0],
            diff[1] - proj * d1[1],
            diff[2] - proj * d1[2],
        )
        return _vmag(perp)
    # skew
    num = abs(diff[0] * cross[0] + diff[1] * cross[1] + diff[2] * cross[2])
    return num / cm


@skill(
    name="detect_standoffs",
    category="inspect",
    level="atomic",
    summary="Detect standoffs — cylindrical bosses with a central concentric "
            "hole. Pairs detect_bosses output with find_features hole entries "
            "by collinear axes.",
    selector_kinds=[],
    history_rules={},
    produces_features=["standoff_inventory"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class DetectStandoffs(SkillBase):
    class Args(BaseModel):
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.inspect.detect_bosses import (
            DetectBosses,
            _cylinder_info,
            _occt_shape,
        )
        from phone_designer.skills.inspect.find_features import FindFeatures
        from phone_designer.skills._resolvers import _all_faces

        # Bosses
        boss_res = DetectBosses().apply(body, {})
        bosses = boss_res.extras.get("bosses", [])

        # Boss axis lookup — pick the first cylindrical face in the
        # cluster as the axis source. Works for both ``cylindrical`` (the
        # boss outer wall is a cylinder) and ``prismatic`` (the boss is
        # a box with a pin hole inside whose wall is a cylinder).
        shape = _occt_shape(body)
        faces = _all_faces(shape)
        boss_axes: dict[int, tuple[tuple[float, float, float],
                                    tuple[float, float, float],
                                    float]] = {}
        for b in bosses:
            for fi in b["face_indices"]:
                if fi >= len(faces):
                    continue
                info = _cylinder_info(faces[fi])
                if info is None:
                    continue
                r, ax = info
                # For cylindrical bosses we use the reported boss diameter
                # so the hole-< -boss test is meaningful. For prismatic
                # bosses we use the boss bbox cross-size (which is the
                # box width, larger than any pin-hole diameter).
                if b["type"] == "cylindrical":
                    boss_diameter = float(b["diameter_or_size_mm"])
                else:
                    # box-shaped boss → use bbox cross size, not the pin
                    # hole's own radius.
                    boss_diameter = float(b["diameter_or_size_mm"])
                    # The boss_metrics fallback may have reported the pin
                    # hole diameter (small cylinder area filter). Replace
                    # with bbox cross size from face_indices.
                    from phone_designer.skills.inspect.detect_bosses import (
                        _faces_bbox,
                    )
                    cluster_objs = [
                        faces[i] for i in b["face_indices"] if i < len(faces)
                    ]
                    (mn, mx) = _faces_bbox(cluster_objs)
                    size = (
                        mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2],
                    )
                    ax_abs = (abs(ax[0]), abs(ax[1]), abs(ax[2]))
                    ax_idx = ax_abs.index(max(ax_abs))
                    cross = [s for k, s in enumerate(size) if k != ax_idx]
                    if cross:
                        boss_diameter = float(min(cross))
                boss_axes[b["id"]] = (
                    tuple(b["center"]), _normalize(ax), boss_diameter,
                )
                break

        # Holes — find_features lists every cylindrical face. We use those
        # whose center lies *inside* one of the bosses (axis-collinear).
        feat_res = FindFeatures().apply(body, {"kinds": ["hole"]})
        holes = [
            (i, f) for i, f in enumerate(feat_res.extras.get("features", []))
            if f.get("kind") == "hole"
        ]

        # boss bbox lookup for the "hole center inside boss bbox" filter
        from phone_designer.skills.inspect.detect_bosses import _faces_bbox
        boss_bboxes: dict[int, tuple[tuple[float, float, float],
                                       tuple[float, float, float]]] = {}
        for b in bosses:
            cluster_objs = [
                faces[i] for i in b["face_indices"] if i < len(faces)
            ]
            boss_bboxes[b["id"]] = _faces_bbox(cluster_objs)

        standoffs: list[dict] = []
        axis_tol = 0.99       # cos angle threshold
        line_tol_mm = 0.1     # collinearity threshold for the axis lines
        bbox_margin = 0.5     # mm — hole center must be inside boss bbox

        for boss_id, (b_origin, b_axis, b_diam) in boss_axes.items():
            bb_min, bb_max = boss_bboxes.get(boss_id, ((0,0,0),(0,0,0)))
            for hole_idx, h in holes:
                h_axis = _normalize(tuple(h["axis"]))
                h_center = tuple(h["center"])
                h_origin = tuple(h["axis_origin"])
                h_diam = 2.0 * h["radius_mm"]

                # 1) parallel axes (sign-insensitive)
                dot = (
                    b_axis[0] * h_axis[0]
                    + b_axis[1] * h_axis[1]
                    + b_axis[2] * h_axis[2]
                )
                if abs(dot) < axis_tol:
                    continue

                # 2) collinear axis lines
                dist = _line_distance(b_origin, b_axis, h_origin, h_axis)
                if dist > line_tol_mm:
                    continue

                # 3) hole must be smaller than boss
                if h_diam >= b_diam:
                    continue

                # 4) hole face center must lie within (expanded) boss bbox
                #    — distinguishes the boss-with-its-own-hole pair from
                #    coincident axes on neighbours.
                if not (
                    bb_min[0] - bbox_margin <= h_center[0] <= bb_max[0] + bbox_margin
                    and bb_min[1] - bbox_margin <= h_center[1] <= bb_max[1] + bbox_margin
                    and bb_min[2] - bbox_margin <= h_center[2] <= bb_max[2] + bbox_margin
                ):
                    continue

                standoffs.append({
                    "boss_id": boss_id,
                    "hole_id": hole_idx,
                    "axis": [round(v, 4) for v in b_axis],
                    "boss_diameter_mm": round(b_diam, 4),
                    "hole_diameter_mm": round(h_diam, 4),
                })

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "standoffs": standoffs,
                "standoff_count": len(standoffs),
            },
        )
