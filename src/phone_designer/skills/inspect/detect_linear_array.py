"""detect_linear_array — atomic, read-only.

Detect linear arrays (3+ same-kind features collinear with constant spacing)
of holes, pockets, or bosses. Body unchanged.

Algorithm
---------
1. Detect every feature of ``feature_kind`` via the corresponding inspect
   skill (``classify_holes`` / ``classify_pockets`` / ``detect_bosses``).
2. For each unordered subset of ``min_count`` or more features, check if
   the centres lie on a line within tolerance AND adjacent spacings are
   equal within tolerance. We do this by first sorting features along
   each axis (X, Y, Z) and growing maximal collinear+equi-spaced runs.

Each run is reported as::

    {
        "feature_kind": "hole" | "pocket" | "boss",
        "positions":    [(x, y, z), ...],
        "direction":    (dx, dy, dz)   # unit vector of the line
        "spacing_mm":   float,
        "count":        N,
    }

``extras["linear_arrays"] = [...]``.

Body is unchanged — ``post_conditions=[body_present]``.
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
# Feature extraction


def _feature_positions(body: Any, feature_kind: str) -> list[tuple[float, float, float]]:
    """Return a list of feature centres for the requested kind."""
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
# Linear-array detection


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float]:
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if m < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _dist(a, b) -> float:
    dx, dy, dz = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _collinear(a, b, c, line_tol_mm: float = 0.2) -> bool:
    """Check perpendicular distance from b to line(a,c) is small."""
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    cx = ab[1] * ac[2] - ab[2] * ac[1]
    cy = ab[2] * ac[0] - ab[0] * ac[2]
    cz = ab[0] * ac[1] - ab[1] * ac[0]
    cross_mag = math.sqrt(cx * cx + cy * cy + cz * cz)
    ac_mag = math.sqrt(ac[0] ** 2 + ac[1] ** 2 + ac[2] ** 2)
    if ac_mag < 1e-9:
        return True
    perp = cross_mag / ac_mag
    return perp <= line_tol_mm


def _find_linear_runs(
    positions: list[tuple[float, float, float]],
    min_count: int,
    line_tol_mm: float = 0.3,
    spacing_tol_mm: float = 0.3,
) -> list[dict[str, Any]]:
    """Greedy enumeration of maximal collinear, equi-spaced runs of size
    ≥ ``min_count``.

    For each ordered pair (i, j) treat the segment from positions[i] to
    positions[j] as the seed; extend forward by repeatedly stepping the
    same direction*spacing and snapping to the nearest unused feature
    within tolerance. Backtrack-extend by the same logic in reverse.
    Deduplicate runs by the sorted set of position indices.
    """
    n = len(positions)
    if n < min_count:
        return []

    seen: set[tuple[int, ...]] = set()
    runs: list[dict[str, Any]] = []

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            spacing = _dist(positions[i], positions[j])
            if spacing < 1e-6:
                continue
            direction = _unit((
                positions[j][0] - positions[i][0],
                positions[j][1] - positions[i][1],
                positions[j][2] - positions[i][2],
            ))
            # build run starting at i, step j
            run_idx = [i, j]
            used = {i, j}
            # forward
            last = positions[j]
            while True:
                target = (
                    last[0] + direction[0] * spacing,
                    last[1] + direction[1] * spacing,
                    last[2] + direction[2] * spacing,
                )
                best_k = -1
                best_d = spacing_tol_mm + 1.0
                for k in range(n):
                    if k in used:
                        continue
                    d = _dist(positions[k], target)
                    if d <= spacing_tol_mm and d < best_d:
                        # also check collinearity with i,j
                        if _collinear(positions[i], positions[k], positions[j],
                                       line_tol_mm=line_tol_mm):
                            best_d = d
                            best_k = k
                if best_k < 0:
                    break
                run_idx.append(best_k)
                used.add(best_k)
                last = positions[best_k]
            # backward
            first = positions[i]
            while True:
                target = (
                    first[0] - direction[0] * spacing,
                    first[1] - direction[1] * spacing,
                    first[2] - direction[2] * spacing,
                )
                best_k = -1
                best_d = spacing_tol_mm + 1.0
                for k in range(n):
                    if k in used:
                        continue
                    d = _dist(positions[k], target)
                    if d <= spacing_tol_mm and d < best_d:
                        if _collinear(positions[i], positions[k], positions[j],
                                       line_tol_mm=line_tol_mm):
                            best_d = d
                            best_k = k
                if best_k < 0:
                    break
                run_idx.insert(0, best_k)
                used.add(best_k)
                first = positions[best_k]

            if len(run_idx) < min_count:
                continue
            sig = tuple(sorted(run_idx))
            if sig in seen:
                continue
            seen.add(sig)

            # Re-order along direction for the stored positions list.
            ordered = sorted(
                run_idx,
                key=lambda k: (
                    positions[k][0] * direction[0]
                    + positions[k][1] * direction[1]
                    + positions[k][2] * direction[2]
                ),
            )
            ordered_positions = [positions[k] for k in ordered]
            # final spacing = average of consecutive distances along the run
            d_list = [
                _dist(ordered_positions[a + 1], ordered_positions[a])
                for a in range(len(ordered_positions) - 1)
            ]
            mean_spacing = sum(d_list) / len(d_list) if d_list else spacing
            runs.append({
                "positions": [tuple(round(c, 4) for c in p)
                              for p in ordered_positions],
                "direction": tuple(round(c, 4) for c in direction),
                "spacing_mm": round(mean_spacing, 4),
                "count": len(ordered_positions),
            })

    # Drop subsets that are fully contained in a larger run of the same
    # direction & spacing.
    def _is_subset(small, big) -> bool:
        small_set = {tuple(round(c, 2) for c in p) for p in small["positions"]}
        big_set = {tuple(round(c, 2) for c in p) for p in big["positions"]}
        return small_set.issubset(big_set) and small["count"] < big["count"]

    filtered: list[dict[str, Any]] = []
    for r in runs:
        if any(_is_subset(r, other) for other in runs if other is not r):
            continue
        filtered.append(r)
    return filtered


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="detect_linear_array",
    category="inspect",
    level="atomic",
    summary="Detect linear arrays (3+ same-kind features collinear and equi-"
            "spaced) of holes, pockets or bosses. Each run is reported with its "
            "ordered positions, direction, spacing, and count. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["pattern_inventory"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class DetectLinearArray(SkillBase):
    class Args(BaseModel):
        feature_kind: Literal["hole", "pocket", "boss"] = "hole"
        min_count: int = Field(default=3, ge=3, le=1000)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        positions = _feature_positions(body, args.feature_kind)
        runs = _find_linear_runs(positions, args.min_count)
        for r in runs:
            r["feature_kind"] = args.feature_kind

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"linear_arrays": runs},
        )
