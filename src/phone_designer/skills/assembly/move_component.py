"""move_component — atomic assembly.

기존 multi-body compound 안에서 이름으로 component 를 찾아 translate/rotate
한 다음 같은 위치 (compound 안의 같은 index) 에 다시 끼워 넣는다. 다른
component 들은 그대로 보존.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.assembly._compound import (
    apply_transform_shape,
    build_compound,
    get_component_names,
    iter_compound_components,
    make_axis_rotation_trsf,
    make_translation_trsf,
)


@skill(
    name="move_component",
    category="assembly",
    level="atomic",
    summary="Translate and/or rotate a named component inside a multi-body assembly. "
            "Other components are preserved.",
    selector_kinds=[],
    history_rules={"moved_component": HistoryRule.MODIFIED_INHERIT},
    produces_features=[],
    preserves=["assembly_topology"],
    manufacturing={},
    failure_modes=["fm.component_not_found"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class MoveComponent(SkillBase):
    class Args(BaseModel):
        name: str = Field(min_length=1)
        translation: tuple[float, float, float] = Field(default=(0.0, 0.0, 0.0))
        rotation_axis_deg: tuple[float, float, float, float] = Field(
            default=(0.0, 0.0, 1.0, 0.0),
            description="(axis_x, axis_y, axis_z, theta_deg). axis (0,0,0) 또는 theta=0 이면 회전 없음.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        if body is None:
            raise RuntimeError("move_component: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body

        prior_names = get_component_names(body)
        components = list(iter_compound_components(shape, prior_names))
        names = [n for n, _ in components]
        shapes = [s for _, s in components]

        if args.name not in names:
            raise RuntimeError(
                f"move_component: component '{args.name}' not found in assembly "
                f"(known: {names})"
            )
        idx = names.index(args.name)

        ax, ay, az, theta = args.rotation_axis_deg
        moved = shapes[idx]
        if (ax * ax + ay * ay + az * az) > 1e-12 and abs(theta) > 1e-9:
            rot = make_axis_rotation_trsf(ax, ay, az, math.radians(theta))
            moved = apply_transform_shape(moved, rot)
        tx, ty, tz = args.translation
        if abs(tx) + abs(ty) + abs(tz) > 1e-12:
            tr = make_translation_trsf(tx, ty, tz)
            moved = apply_transform_shape(moved, tr)

        new_shapes = list(shapes)
        new_shapes[idx] = moved
        comp = build_compound(new_shapes)
        result_body = Part(comp)
        result_body._pd_component_names = list(names)

        history = EntityHistoryMap(
            rules={"moved_component": HistoryRule.MODIFIED_INHERIT},
        )
        return SkillResult(
            body=result_body,
            history=history,
            extras={"component_names": list(names)},
        )
