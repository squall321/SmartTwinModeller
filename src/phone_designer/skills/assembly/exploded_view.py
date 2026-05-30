"""exploded_view — atomic assembly visualization.

각 component 를 지정한 방향 / 거리로 분리해서 ``exploded view`` 를 만든다.
방향 모드:
    radial    — 각 component 를 assembly bbox 중심 → component bbox 중심
                벡터 방향으로 i*explode_distance 만큼 translate (i 는 component
                index). 가장 자연스러운 explode.
    axial_z   — 모든 component 를 +Z 로 i*explode_distance translate.
    axial_x   — 모든 component 를 +X 로 i*explode_distance translate.
    custom    — custom_direction (3-vector) 방향으로 translate. 영벡터면 RuntimeError.

extras["exploded_offsets"] 에 ``[{"name", "translation":[dx,dy,dz]}, ...]`` 기록.

원본 component 들의 순서/이름은 보존된다. 단일 solid (compound 아님) 도 안전하게
1-component 로 처리.
"""
from __future__ import annotations

import math
from typing import Any, Literal

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
    make_translation_trsf,
)


def _shape_bbox_center(shape) -> tuple[float, float, float]:
    """axis-aligned bbox center of an OCCT shape. 빈 shape 면 (0,0,0)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.Add_s(shape, bb)
    except Exception:
        return (0.0, 0.0, 0.0)
    if bb.IsVoid():
        return (0.0, 0.0, 0.0)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return (
        0.5 * (xmin + xmax),
        0.5 * (ymin + ymax),
        0.5 * (zmin + zmax),
    )


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / n, v[1] / n, v[2] / n)


@skill(
    name="exploded_view",
    category="assembly",
    level="atomic",
    summary="Translate each component of a multi-body assembly outward (radial / axial / "
            "custom) to produce an exploded view. Offsets are recorded in "
            "result.extras['exploded_offsets'].",
    selector_kinds=[],
    history_rules={"exploded_component": HistoryRule.MODIFIED_INHERIT},
    produces_features=["exploded_view"],
    preserves=["assembly_topology"],
    manufacturing={},
    failure_modes=["fm.zero_direction_vector"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class ExplodedView(SkillBase):
    class Args(BaseModel):
        explode_distance_mm: float = Field(
            default=50.0,
            description="component 마다 i*explode_distance 만큼 outward translate.",
        )
        direction: Literal["radial", "axial_z", "axial_x", "custom"] = "radial"
        custom_direction: tuple[float, float, float] | None = Field(
            default=None,
            description="direction='custom' 일 때 사용. 단위 벡터로 정규화됨.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        if body is None:
            raise RuntimeError("exploded_view: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        prior_names = get_component_names(body)
        components = list(iter_compound_components(shape, prior_names))
        names = [n for n, _ in components]
        shapes = [s for _, s in components]
        n_comp = len(shapes)

        # Assembly bbox center for radial mode.
        assembly_center = _shape_bbox_center(shape)

        # Pre-compute per-component translation directions.
        directions: list[tuple[float, float, float]] = []
        if args.direction == "axial_z":
            directions = [(0.0, 0.0, 1.0)] * n_comp
        elif args.direction == "axial_x":
            directions = [(1.0, 0.0, 0.0)] * n_comp
        elif args.direction == "custom":
            if args.custom_direction is None:
                raise RuntimeError(
                    "exploded_view: direction='custom' 이지만 custom_direction 이 None"
                )
            d = _normalize(args.custom_direction)
            if d == (0.0, 0.0, 0.0):
                raise RuntimeError(
                    "exploded_view: custom_direction 이 영벡터 — 방향 없음"
                )
            directions = [d] * n_comp
        else:   # radial
            for sub in shapes:
                cc = _shape_bbox_center(sub)
                v = (
                    cc[0] - assembly_center[0],
                    cc[1] - assembly_center[1],
                    cc[2] - assembly_center[2],
                )
                d = _normalize(v)
                if d == (0.0, 0.0, 0.0):
                    # component 가 assembly 중심에 정확히 있는 경우 (단일 component
                    # 거나 동심) — 안전 fallback +Z.
                    d = (0.0, 0.0, 1.0)
                directions.append(d)

        # Apply translation = i * explode_distance * dir_i for each component.
        new_shapes: list[Any] = []
        offsets: list[dict[str, Any]] = []
        for i, (sub, dir_i, name) in enumerate(zip(shapes, directions, names)):
            scale = float(i) * float(args.explode_distance_mm)
            dx, dy, dz = dir_i[0] * scale, dir_i[1] * scale, dir_i[2] * scale
            if abs(dx) + abs(dy) + abs(dz) > 1e-12:
                tr = make_translation_trsf(dx, dy, dz)
                moved = apply_transform_shape(sub, tr)
            else:
                moved = sub
            new_shapes.append(moved)
            offsets.append({
                "name": name,
                "translation": [round(dx, 6), round(dy, 6), round(dz, 6)],
            })

        comp = build_compound(new_shapes)
        result_body = Part(comp)
        result_body._pd_component_names = list(names)

        history = EntityHistoryMap(
            rules={"exploded_component": HistoryRule.MODIFIED_INHERIT},
        )
        return SkillResult(
            body=result_body,
            history=history,
            extras={
                "component_names": list(names),
                "exploded_offsets": offsets,
                "direction": args.direction,
                "explode_distance_mm": float(args.explode_distance_mm),
            },
        )
