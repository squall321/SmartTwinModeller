"""surface_area_by_region — atomic, read-only.

For each provided selector (= "region"), resolve the matched faces and sum
their surface areas. LLM agent 는 "top faces 의 총 면적 / dome faces 의 총
면적 / side faces 의 총 면적" 같은 regional metric 을 한 번에 가져온다.

extras schema:
    {"region_count": int,
     "total_area_mm2": float,    # 모든 region 면적 합 (face 중복은 dedup 하지 않음 —
                                 #  여러 region 이 동일 face 를 매칭하면 두 번 더해짐)
     "regions": [
        {"selector_index": i,
         "selector": serialized_selector_dict,
         "area_mm2": float,
         "face_count": int},
        ...
     ]}

body 는 변경하지 않는다 (post ``body_present``).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _selector_to_dict(s: SelectorBase) -> dict:
    try:
        return s.model_dump(mode="json")
    except Exception:
        return {"kind": getattr(s, "kind", "unknown")}


@skill(
    name="surface_area_by_region",
    category="inspect",
    level="atomic",
    summary="For each region selector, resolve matched faces and sum surface "
            "areas. Returns per-region area + total. Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["region_areas"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class SurfaceAreaByRegion(SkillBase):
    class Args(BaseModel):
        region_selectors: list[dict] = Field(default_factory=list)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _face_area, resolve_faces

        shape = _occt_shape(body)

        regions: list[dict[str, Any]] = []
        total_area = 0.0

        for i, raw_sel in enumerate(args.region_selectors):
            entry: dict[str, Any] = {
                "selector_index": i,
                "selector": raw_sel if isinstance(raw_sel, dict) else {},
                "area_mm2": 0.0,
                "face_count": 0,
                "error": None,
            }
            try:
                sel = _coerce_selector(raw_sel)
                entry["selector"] = _selector_to_dict(sel)
                faces = resolve_faces(shape, sel, body=body)
            except Exception as ex:
                entry["error"] = f"{type(ex).__name__}: {ex}"
                regions.append(entry)
                continue

            region_area = 0.0
            for f in faces:
                try:
                    region_area += float(_face_area(f))
                except Exception:
                    pass
            entry["area_mm2"] = round(region_area, 6)
            entry["face_count"] = len(faces)
            total_area += region_area
            regions.append(entry)

        extras = {
            "region_count": len(regions),
            "total_area_mm2": round(total_area, 6),
            "regions": regions,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
