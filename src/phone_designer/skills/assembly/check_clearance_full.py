"""check_clearance_full — atomic, read-only assembly inspection.

assembly compound 의 모든 (a, b) pair 에 대해 ``BRepExtrema_DistShapeShape`` 로
정확한 body-body min distance 를 측정한다. AABB-only 검사 (components/collision.py
v1) 보다 정확. 단순한 AABB 가 떨어져 있어도 곡면이 가까이 붙어있으면 잡힌다.

extras["clearance_violations"]:
    [{"a", "b", "min_dist_mm": float, "point_a": [x,y,z], "point_b": [x,y,z]}, ...]

violation 조건: min_dist_mm < min_clearance_mm. (overlap 인 경우 distance=0 → 위반.)
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.assembly._compound import (
    get_component_names,
    iter_compound_components,
)


def _min_distance_between_shapes(
    shape_a, shape_b,
) -> tuple[float, tuple[float, float, float], tuple[float, float, float]]:
    """Return (min_dist_mm, point_on_a, point_on_b) — robust to overlap (returns
    0.0 with one of the contact points). 실패 시 (inf, (0,0,0), (0,0,0))."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    try:
        ext = BRepExtrema_DistShapeShape(shape_a, shape_b)
        ext.Perform()
        if not ext.IsDone() or ext.NbSolution() < 1:
            return float("inf"), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        d = float(ext.Value())
        p1 = ext.PointOnShape1(1)
        p2 = ext.PointOnShape2(1)
        return (
            d,
            (p1.X(), p1.Y(), p1.Z()),
            (p2.X(), p2.Y(), p2.Z()),
        )
    except Exception:
        return float("inf"), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)


@skill(
    name="check_clearance_full",
    category="assembly",
    level="atomic",
    summary="Exact pairwise body-body minimum distance check on a multi-body assembly. "
            "Reports every pair whose distance is below min_clearance_mm in "
            "result.extras['clearance_violations']. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["clearance_report"],
    preserves=["assembly_topology", "body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.5,
    post_conditions=[PostCondition(kind="body_present")],
)
class CheckClearanceFull(SkillBase):
    class Args(BaseModel):
        min_clearance_mm: float = Field(
            default=0.5,
            ge=0.0,
            description="허용 최소 간격. 이 값보다 가까운 pair 는 violation 으로 기록.",
        )
        pair_filter: list[tuple[str, str]] | None = Field(
            default=None,
            description="검사할 (a,b) 이름 쌍. None 이면 모든 pair.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise RuntimeError("check_clearance_full: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        prior_names = get_component_names(body)
        components = list(iter_compound_components(shape, prior_names))
        name_to_shape: dict[str, Any] = {n: s for n, s in components}
        all_names = [n for n, _ in components]

        if args.pair_filter is None:
            pairs: list[tuple[str, str]] = []
            for i in range(len(all_names)):
                for j in range(i + 1, len(all_names)):
                    pairs.append((all_names[i], all_names[j]))
        else:
            pairs = []
            for a, b in args.pair_filter:
                if a not in name_to_shape:
                    raise RuntimeError(
                        f"check_clearance_full: unknown component '{a}'"
                    )
                if b not in name_to_shape:
                    raise RuntimeError(
                        f"check_clearance_full: unknown component '{b}'"
                    )
                pairs.append((a, b))

        violations: list[dict[str, Any]] = []
        for a, b in pairs:
            d, pa, pb = _min_distance_between_shapes(
                name_to_shape[a], name_to_shape[b]
            )
            if d < args.min_clearance_mm:
                violations.append({
                    "a": a,
                    "b": b,
                    "min_dist_mm": round(d, 6),
                    "point_a": [round(pa[0], 6), round(pa[1], 6), round(pa[2], 6)],
                    "point_b": [round(pb[0], 6), round(pb[1], 6), round(pb[2], 6)],
                })

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "clearance_violations": violations,
                "component_names": list(all_names),
                "min_clearance_mm": float(args.min_clearance_mm),
                "pair_count_checked": len(pairs),
            },
        )
