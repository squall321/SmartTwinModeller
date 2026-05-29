"""bom_extract — atomic, read-only assembly inspection.

assembly compound 의 모든 component 를 순회하면서 각각의 axis-aligned bbox
(min/max) 과 volume (mm³) 을 측정한다. 결과는 SkillResult.extras["bom"] 에
``[{"name", "bbox_min", "bbox_max", "size", "volume_mm3"}, ...]`` 형태로
저장한다. body 는 변경 없이 그대로 반환.

Use cases
---------
- BOM (Bill-of-Materials) 출력
- assembly snapshot 의 형상 summary
- 후속 planner 가 "가장 큰 부품" 같은 휴리스틱을 적용할 때

Notes
-----
- bbox 는 ``Bnd_Box`` 기반 → 회전된 부품의 경우 빡빡한 OBB 가 아니라 axis-aligned.
  실제 부품 크기보다 약간 클 수 있다. 부품의 local frame 까지 알려면 별도 skill 필요.
- volume 은 ``BRepGProp.VolumeProperties`` 로 정확하게 계산.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.assembly._compound import (
    get_component_names,
    iter_compound_components,
)


def _measure_component(name: str, shape) -> dict[str, Any]:
    """component shape → {name, bbox_min, bbox_max, size, volume_mm3}."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    bb = Bnd_Box()
    try:
        BRepBndLib.Add_s(shape, bb)
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        bbox_min = (float(xmin), float(ymin), float(zmin))
        bbox_max = (float(xmax), float(ymax), float(zmax))
        size = (
            float(xmax - xmin),
            float(ymax - ymin),
            float(zmax - zmin),
        )
    except Exception:
        bbox_min = (0.0, 0.0, 0.0)
        bbox_max = (0.0, 0.0, 0.0)
        size = (0.0, 0.0, 0.0)

    try:
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        volume = float(props.Mass())
    except Exception:
        volume = 0.0

    return {
        "name": name,
        "bbox_min": bbox_min,
        "bbox_max": bbox_max,
        "size": size,
        "volume_mm3": round(volume, 6),
    }


@skill(
    name="bom_extract",
    category="assembly",
    level="atomic",
    summary="Extract a bill-of-materials from a multi-body assembly — each component's "
            "axis-aligned bbox and volume in result.extras['bom']. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["bom_report"],
    preserves=["assembly_topology", "body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class BomExtract(SkillBase):
    class Args(BaseModel):
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise RuntimeError("bom_extract: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        prior_names = get_component_names(body)
        components = list(iter_compound_components(shape, prior_names))

        bom: list[dict[str, Any]] = []
        for name, sub_shape in components:
            bom.append(_measure_component(name, sub_shape))

        total_volume = round(sum(item["volume_mm3"] for item in bom), 6)

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "bom": bom,
                "component_names": [n for n, _ in components],
                "total_volume_mm3": total_volume,
                "component_count": len(bom),
            },
        )
