"""Selector 시스템 — face/edge/vertex 부분집합 선언적 지정.

[[lat.md/concepts.md#selector]] + [[lat.md/skills.md#selectors]] 구현.

원자 selector (안정성 ★ 순):
    TaggedSelector             ★★★★★ history map propagated tag
    FaceNamedSelector          ★★★★
    AxisAlignedEdgesSelector   ★★★
    EdgesOnFaceSelector        ★★★
    EdgesByLengthSelector      ★★
    EdgesByPositionSelector    ★      (좌표 변경에 약함)
    FacesByNormalSelector      ★★★

조합:
    AndSel, OrSel, NotSel, FirstN, LargestN

JSON 트리 표현 (LLM tool schema 용):
    {"kind": "and", "left": {...}, "right": {...}}
    {"kind": "tagged", "tag": "TOP_RIM"}
    {"kind": "edges_by_position", "bbox": {"min": [...], "max": [...]}}
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Union

# Selector 는 Pydantic 으로 정의해서 manifest 의 JSON Schema 와 LLM tool schema 양쪽 호환.
from pydantic import BaseModel, Field


SelectorKind = Literal[
    "tagged",
    "face_named",
    "axis_aligned_edges",
    "edges_on_face",
    "edges_by_length",
    "edges_by_position",
    "faces_by_normal",
    "faces_by_area",
    "edges_convex_only",
    "edges_concave_only",
    "and",
    "or",
    "not",
    "first_n",
    "largest_n",
]


class SelectorBase(BaseModel):
    """추상 base — resolve 는 subclass 가 OCCT shape 위에서 구현.

    Phase 1 에서는 metadata 만. 실제 resolve 는 _resolvers.py 에서.
    """

    kind: SelectorKind

    def stability_rank(self) -> int:
        """안정성 점수 (높을수록 안정). Planner 휴리스틱용."""
        return _STABILITY_RANK.get(self.kind, 1)


class TaggedSelector(SelectorBase):
    kind: Literal["tagged"] = "tagged"
    tag: str


class FaceNamedSelector(SelectorBase):
    kind: Literal["face_named"] = "face_named"
    name: str   # "top" | "bottom" | "side" | 사용자 tag


class AxisAlignedEdgesSelector(SelectorBase):
    kind: Literal["axis_aligned_edges"] = "axis_aligned_edges"
    axis: Literal["X", "Y", "Z"]


class EdgesOnFaceSelector(SelectorBase):
    kind: Literal["edges_on_face"] = "edges_on_face"
    face: "SelectorRef"   # 재귀 — 다른 selector 가 face 를 가리킴


class EdgesByLengthSelector(SelectorBase):
    kind: Literal["edges_by_length"] = "edges_by_length"
    min: float | None = None
    max: float | None = None


class BBoxModel(BaseModel):
    min: tuple[float, float, float]
    max: tuple[float, float, float]


class EdgesByPositionSelector(SelectorBase):
    kind: Literal["edges_by_position"] = "edges_by_position"
    bbox: BBoxModel


class FacesByNormalSelector(SelectorBase):
    kind: Literal["faces_by_normal"] = "faces_by_normal"
    direction: tuple[float, float, float]
    tol_deg: float = 5.0


class FacesByAreaSelector(SelectorBase):
    kind: Literal["faces_by_area"] = "faces_by_area"
    min: float | None = None
    max: float | None = None


class EdgesConvexOnlySelector(SelectorBase):
    kind: Literal["edges_convex_only"] = "edges_convex_only"


class EdgesConcaveOnlySelector(SelectorBase):
    kind: Literal["edges_concave_only"] = "edges_concave_only"


class AndSel(SelectorBase):
    kind: Literal["and"] = "and"
    left: "SelectorRef"
    right: "SelectorRef"


class OrSel(SelectorBase):
    kind: Literal["or"] = "or"
    left: "SelectorRef"
    right: "SelectorRef"


class NotSel(SelectorBase):
    kind: Literal["not"] = "not"
    inner: "SelectorRef"


class FirstN(SelectorBase):
    kind: Literal["first_n"] = "first_n"
    inner: "SelectorRef"
    n: int = Field(ge=1)
    sort_by: Literal["bbox_x", "bbox_y", "bbox_z", "measure"] = "bbox_z"


class LargestN(SelectorBase):
    kind: Literal["largest_n"] = "largest_n"
    inner: "SelectorRef"
    n: int = Field(ge=1)
    sort_by: Literal["measure", "bbox_volume"] = "measure"


# Union 타입 — Pydantic discriminated union 으로 처리
SelectorRef = Union[
    TaggedSelector,
    FaceNamedSelector,
    AxisAlignedEdgesSelector,
    EdgesOnFaceSelector,
    EdgesByLengthSelector,
    EdgesByPositionSelector,
    FacesByNormalSelector,
    FacesByAreaSelector,
    EdgesConvexOnlySelector,
    EdgesConcaveOnlySelector,
    AndSel,
    OrSel,
    NotSel,
    FirstN,
    LargestN,
]

# Pydantic forward refs 해결
for _cls in [EdgesOnFaceSelector, AndSel, OrSel, NotSel, FirstN, LargestN]:
    _cls.model_rebuild()


# Backwards-compat alias
Selector = SelectorBase


_STABILITY_RANK: dict[str, int] = {
    "tagged": 5,
    "face_named": 4,
    "axis_aligned_edges": 3,
    "edges_on_face": 3,
    "faces_by_normal": 3,
    "edges_convex_only": 3,
    "edges_concave_only": 3,
    "edges_by_length": 2,
    "faces_by_area": 2,
    "edges_by_position": 1,
    # 조합 selector 의 안정성 = inner 의 min (별도 평가)
    "and": 3,
    "or": 3,
    "not": 3,
    "first_n": 3,
    "largest_n": 3,
}


_KIND_TO_CLASS: dict[str, type[SelectorBase]] = {
    "tagged": TaggedSelector,
    "face_named": FaceNamedSelector,
    "axis_aligned_edges": AxisAlignedEdgesSelector,
    "edges_on_face": EdgesOnFaceSelector,
    "edges_by_length": EdgesByLengthSelector,
    "edges_by_position": EdgesByPositionSelector,
    "faces_by_normal": FacesByNormalSelector,
    "faces_by_area": FacesByAreaSelector,
    "edges_convex_only": EdgesConvexOnlySelector,
    "edges_concave_only": EdgesConcaveOnlySelector,
    "and": AndSel,
    "or": OrSel,
    "not": NotSel,
    "first_n": FirstN,
    "largest_n": LargestN,
}


def selector_from_dict(d: dict[str, Any]) -> SelectorBase:
    """JSON 트리 → Pydantic 인스턴스. LLM tool 호출 결과 파싱용."""
    kind = d.get("kind")
    cls = _KIND_TO_CLASS.get(kind)
    if cls is None:
        raise ValueError(f"unknown selector kind: {kind}")
    return cls.model_validate(d)


def selector_json_schemas() -> dict[str, dict]:
    """모든 selector 의 JSON Schema 반환. LLM tool schema + manifest 에 사용."""
    return {kind: cls.model_json_schema() for kind, cls in _KIND_TO_CLASS.items()}
