"""add_component — atomic assembly.

외부 STEP 파일 또는 현재 body 자체를 가져와서 transform 한 뒤 multi-body
container (TopoDS_Compound) 에 named sub-shape 으로 추가한다.

Behavior
--------
- body 가 None 이거나 Compound 가 아니면 새 compound 생성 (기존 body 가
  Solid 였다면 그 solid 를 첫 component 로 보존, name 은 ``base``).
- ``source_step_path`` 가 주어지면 STEP 로드 → solid 로 사용.
- 주어지지 않으면 **현재 body 자체** (compound 이면 모든 sub-shape 가 아닌
  body.wrapped) 를 그대로 component 로 사용 — 사실상 "현재 body 를 다른
  이름으로 다시 복제" 용도.
- 결과 compound 의 component_names list 는 SkillResult.extras
  ``component_names`` 에 평행하게 보존.
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
    name="add_component",
    category="assembly",
    level="atomic",
    summary="Add a named component (from an external STEP file or the current body) "
            "into a multi-body assembly compound, optionally translated/rotated.",
    selector_kinds=[],
    history_rules={"output_component": HistoryRule.GENERATED_NEW},
    produces_features=["assembly_component"],
    preserves=[],
    manufacturing={},
    failure_modes=[
        "fm.file_not_found",
        "fm.step_parse_failed",
        "fm.duplicate_component_name",
        "fm.no_source_body",
    ],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class AddComponent(SkillBase):
    class Args(BaseModel):
        name: str = Field(min_length=1, description="component 의 고유 이름")
        source_step_path: str | None = Field(
            default=None,
            description="추가할 STEP 파일 경로. None 이면 현재 body 를 그대로 사용.",
        )
        translation: tuple[float, float, float] = Field(default=(0.0, 0.0, 0.0))
        rotation_axis_deg: tuple[float, float, float, float] = Field(
            default=(0.0, 0.0, 1.0, 0.0),
            description="(axis_x, axis_y, axis_z, theta_deg) — axis 가 (0,0,0) 이면 회전 없음.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        # 1. source shape 확보.
        if args.source_step_path is not None:
            p = Path(args.source_step_path)
            if not p.exists():
                raise FileNotFoundError(f"STEP not found: {p}")
            src_shape = load_step_shape(str(p))
        else:
            if body is None:
                raise RuntimeError(
                    "add_component: source_step_path is None but body is also None — "
                    "nothing to add"
                )
            src_shape = body.wrapped if hasattr(body, "wrapped") else body

        # 2. transform 적용.
        ax, ay, az, theta = args.rotation_axis_deg
        if (ax * ax + ay * ay + az * az) > 1e-12 and abs(theta) > 1e-9:
            rot = make_axis_rotation_trsf(ax, ay, az, math.radians(theta))
            src_shape = apply_transform_shape(src_shape, rot)
        tx, ty, tz = args.translation
        if abs(tx) + abs(ty) + abs(tz) > 1e-12:
            tr = make_translation_trsf(tx, ty, tz)
            src_shape = apply_transform_shape(src_shape, tr)

        # 3. 기존 compound 추출 + duplicate name 검사.
        # body 가 이미 assembly (``_pd_component_names`` attribute) 면 그 component
        # 들을 보존하면서 append. 그렇지 않으면 body 는 "source" 로만 쓰였거나
        # 비어 있으니 새 assembly 를 만든다.
        existing_names: list[str] = []
        existing_shapes: list[Any] = []
        if body is not None and hasattr(body, "_pd_component_names"):
            shape = body.wrapped if hasattr(body, "wrapped") else body
            prior_names = list(body._pd_component_names or [])
            for nm, sub in iter_compound_components(shape, prior_names):
                existing_names.append(nm)
                existing_shapes.append(sub)

        if args.name in existing_names:
            raise RuntimeError(
                f"add_component: duplicate component name '{args.name}'"
            )

        # 4. 새 compound 작성.
        new_names = existing_names + [args.name]
        new_shapes = existing_shapes + [src_shape]
        comp = build_compound(new_shapes)
        result_body = Part(comp)
        # parallel name list 부착 (assembly skill 들이 참조)
        result_body._pd_component_names = list(new_names)

        history = EntityHistoryMap(
            rules={"output_component": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(
            body=result_body,
            history=history,
            extras={"component_names": list(new_names)},
        )
