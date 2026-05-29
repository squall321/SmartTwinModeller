"""fastener_array — atomic assembly.

같은 fastener STEP 파일을 여러 좌표에 복제 배치한다. 각 인스턴스는 별도
component 이름을 갖고 multi-body compound 의 sub-shape 으로 들어간다.

Behavior
--------
- ``fastener_step_path`` 가 주어지면 STEP 로드 → fastener_template shape.
- 주어지지 않고 body 가 있으면 현재 body 의 root shape 자체를 template 로 사용.
  (e.g., 이전 step 에서 생성한 screw solid 를 곧장 복제).
- ``positions`` 의 각 (x, y, z) 마다 template 를 translate 후 compound 에 추가.
- 이름은 ``name_prefix + "_" + index`` (0-based). 기존 compound 가 있으면 그
  component 들을 보존하고 append.
- 중복 이름이 있으면 RuntimeError.

Args:
    positions: list of (x, y, z) tuples.
    fastener_step_path: STEP file (optional — defaults to current body root).
    name_prefix: 인스턴스 이름 prefix. default "fastener".
    rotation_axis_deg: 각 fastener 인스턴스에 동일하게 적용되는 (ax, ay, az, theta_deg) 회전.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.assembly._compound import (
    apply_transform_shape,
    build_compound,
    iter_compound_components,
    load_step_shape,
    make_axis_rotation_trsf,
    make_translation_trsf,
)


@skill(
    name="fastener_array",
    category="assembly",
    level="atomic",
    summary="Populate N positions with copies of the same fastener (e.g. a screw). "
            "Each copy becomes a named component in the assembly compound.",
    selector_kinds=[],
    history_rules={"output_component": HistoryRule.GENERATED_NEW},
    produces_features=["fastener_array"],
    preserves=[],
    manufacturing={},
    failure_modes=[
        "fm.file_not_found",
        "fm.step_parse_failed",
        "fm.duplicate_component_name",
        "fm.no_source_body",
        "fm.empty_positions",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class FastenerArray(SkillBase):
    class Args(BaseModel):
        positions: list[tuple[float, float, float]] = Field(
            min_length=1,
            description="복제 위치 list. 각 (x, y, z) 마다 한 개의 fastener 인스턴스 생성.",
        )
        fastener_step_path: str | None = Field(
            default=None,
            description="fastener STEP 파일 경로. None 이면 현재 body 를 template 로 사용.",
        )
        name_prefix: str = Field(default="fastener", min_length=1)
        rotation_axis_deg: tuple[float, float, float, float] = Field(
            default=(0.0, 0.0, 1.0, 0.0),
            description="(ax, ay, az, theta_deg) — 모든 인스턴스에 같은 회전 적용.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        if not args.positions:
            raise RuntimeError("fastener_array: positions list is empty")

        # 1. template shape 확보.
        if args.fastener_step_path is not None:
            p = Path(args.fastener_step_path)
            if not p.exists():
                raise FileNotFoundError(f"STEP not found: {p}")
            template = load_step_shape(str(p))
        else:
            if body is None:
                raise RuntimeError(
                    "fastener_array: fastener_step_path is None but body is also None — "
                    "no template available"
                )
            template = body.wrapped if hasattr(body, "wrapped") else body

        # 2. 기존 compound 의 component 들 보존 (body 가 assembly 일 때).
        existing_names: list[str] = []
        existing_shapes: list[Any] = []
        if body is not None and hasattr(body, "_pd_component_names"):
            shape = body.wrapped if hasattr(body, "wrapped") else body
            prior_names = list(body._pd_component_names or [])
            for nm, sub in iter_compound_components(shape, prior_names):
                existing_names.append(nm)
                existing_shapes.append(sub)

        # 3. 인스턴스마다 transform 후 append.
        ax, ay, az, theta = args.rotation_axis_deg
        do_rotate = (ax * ax + ay * ay + az * az) > 1e-12 and abs(theta) > 1e-9

        new_names = list(existing_names)
        new_shapes = list(existing_shapes)
        for i, (tx, ty, tz) in enumerate(args.positions):
            inst_name = f"{args.name_prefix}_{i}"
            if inst_name in new_names:
                raise RuntimeError(
                    f"fastener_array: duplicate component name '{inst_name}' "
                    f"(conflict with existing component)"
                )
            inst = template
            if do_rotate:
                rot = make_axis_rotation_trsf(ax, ay, az, math.radians(theta))
                inst = apply_transform_shape(inst, rot)
            if abs(tx) + abs(ty) + abs(tz) > 1e-12:
                tr = make_translation_trsf(tx, ty, tz)
                inst = apply_transform_shape(inst, tr)
            new_names.append(inst_name)
            new_shapes.append(inst)

        comp = build_compound(new_shapes)
        result_body = Part(comp)
        result_body._pd_component_names = list(new_names)

        history = EntityHistoryMap(
            rules={"output_component": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(
            body=result_body,
            history=history,
            extras={
                "component_names": list(new_names),
                "fastener_count": len(args.positions),
            },
        )
