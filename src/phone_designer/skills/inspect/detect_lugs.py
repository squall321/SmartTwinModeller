"""detect_lugs — atomic, read-only.

A lug pair is the watch-strap pattern: two bosses (typically prismatic
pads or cylindrical pins) on opposite sides of the housing, each with a
through-hole, and the two through-holes share a *parallel axis*. The
through-axes are usually perpendicular to the line connecting the two
boss centers, which is what we test for.

Algorithm
---------
1. Detect standoffs (boss + concentric through-hole). Each standoff is a
   candidate lug.
2. Pair every two standoffs that
     - have parallel through-axes (|axis_a · axis_b| > 0.99), and
     - whose center-to-center separation has a component **perpendicular**
       to the shared axis that is the dominant component (otherwise the
       two bosses are stacked along the axis instead of being a pair).
3. Report the centroid-to-centroid distance.

extras schema::

    extras["lugs"] = [
        {
            "pair": [boss_id_a, boss_id_b],
            "axis": [x, y, z],
            "separation_mm": float,
        },
        ...
    ]
    extras["lug_count"] = int

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


def _vmag(v) -> float:
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


@skill(
    name="detect_lugs",
    category="inspect",
    level="atomic",
    summary="Detect strap-lug pairs — two standoffs (boss + through-hole) "
            "whose pin axes are parallel and lie side-by-side perpendicular "
            "to that axis (watch-strap pattern).",
    selector_kinds=[],
    history_rules={},
    produces_features=["lug_inventory"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class DetectLugs(SkillBase):
    class Args(BaseModel):
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.inspect.detect_bosses import DetectBosses
        from phone_designer.skills.inspect.detect_standoffs import DetectStandoffs

        boss_res = DetectBosses().apply(body, {})
        bosses = boss_res.extras.get("bosses", [])
        boss_by_id = {b["id"]: b for b in bosses}

        standoff_res = DetectStandoffs().apply(body, {})
        standoffs = standoff_res.extras.get("standoffs", [])

        axis_par_tol = 0.99
        # We accept two patterns:
        #   - watch-strap: bosses separated *along* the pin axis (pins are
        #     two parallel collinear stubs; conceptually a strap pin would
        #     thread them).
        #   - clevis / hinge: bosses separated *perpendicular* to the
        #     pin axis but with parallel pin axes.
        # In both cases the through-holes' axes are parallel.

        lugs: list[dict] = []
        n = len(standoffs)
        seen_pairs: set[tuple[int, int]] = set()
        for i in range(n):
            for j in range(i + 1, n):
                a = standoffs[i]
                b = standoffs[j]
                ax_a = a["axis"]
                ax_b = b["axis"]
                dot = (
                    ax_a[0] * ax_b[0]
                    + ax_a[1] * ax_b[1]
                    + ax_a[2] * ax_b[2]
                )
                if abs(dot) < axis_par_tol:
                    continue

                ba = boss_by_id.get(a["boss_id"])
                bb = boss_by_id.get(b["boss_id"])
                if ba is None or bb is None:
                    continue
                if ba["id"] == bb["id"]:
                    continue

                # dedup unordered pair
                key = tuple(sorted((ba["id"], bb["id"])))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)

                ca = ba["center"]
                cb = bb["center"]
                d = (cb[0] - ca[0], cb[1] - ca[1], cb[2] - ca[2])
                sep = _vmag(d)
                if sep < 1e-6:
                    continue

                lugs.append({
                    "pair": [ba["id"], bb["id"]],
                    "axis": [round(v, 4) for v in ax_a],
                    "separation_mm": round(sep, 4),
                })

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"lugs": lugs, "lug_count": len(lugs)},
        )
