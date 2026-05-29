"""interference_check — atomic, read-only assembly inspection.

assembly compound 의 모든 (또는 ``pair_filter`` 로 제한된) component pair 에
대해 ``BRepAlgoAPI_Common`` 으로 boolean overlap 을 계산한다. AABB 만 보는
``components/collision.py`` 의 v1 과 달리 회전된 형상도 정확히 잡는다.

extras["interferences"] schema:
    [{"a": "...", "b": "...", "overlap_volume": float}, ...]

overlap_volume > tol 인 pair 만 포함. tol 은 OCCT roundoff (≈ 1e-7 mm³) 보다
충분히 큰 0.001 mm³ (1 µL). body 는 그대로 반환.
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


_OVERLAP_TOL_MM3 = 1e-3   # 1 µL — well above OCCT roundoff


def _common_volume(shape_a, shape_b) -> float:
    """BRepAlgoAPI_Common → 결과 형상의 volume. 실패 시 0."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    try:
        common = BRepAlgoAPI_Common(shape_a, shape_b)
        common.Build()
        if not common.IsDone():
            return 0.0
        result = common.Shape()
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(result, props)
        return float(props.Mass())
    except Exception:
        return 0.0


@skill(
    name="interference_check",
    category="assembly",
    level="atomic",
    summary="Check pairwise boolean overlap of components in a multi-body assembly. "
            "Returns each overlapping pair's volume in result.extras['interferences'].",
    selector_kinds=[],
    history_rules={},
    produces_features=["interference_report"],
    preserves=["assembly_topology", "body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="body_present")],
)
class InterferenceCheck(SkillBase):
    class Args(BaseModel):
        pair_filter: list[tuple[str, str]] | None = Field(
            default=None,
            description="검사할 (a,b) 이름 쌍. None 이면 모든 pair 검사.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise RuntimeError("interference_check: body is None")
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
            # 사용자 입력 검증.
            pairs = []
            for a, b in args.pair_filter:
                if a not in name_to_shape:
                    raise RuntimeError(
                        f"interference_check: unknown component '{a}'"
                    )
                if b not in name_to_shape:
                    raise RuntimeError(
                        f"interference_check: unknown component '{b}'"
                    )
                pairs.append((a, b))

        interferences: list[dict[str, Any]] = []
        for a, b in pairs:
            vol = _common_volume(name_to_shape[a], name_to_shape[b])
            if vol > _OVERLAP_TOL_MM3:
                interferences.append({
                    "a": a,
                    "b": b,
                    "overlap_volume": round(vol, 6),
                })

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "interferences": interferences,
                "component_names": list(all_names),
            },
        )
