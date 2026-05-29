"""History map + freeze (PF-1, PF-2).

[[lat.md/persistent-naming]] + [[lat.md/plan-determinism]] 구현.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HistoryRule(str, Enum):
    """OCCT history map propagation 의 4종 분류 (PF-1)."""

    MODIFIED_INHERIT = "modified_inherit"   # face/edge 변형되어도 tag 유지
    SPLIT_BRANCH     = "split_branch"        # 1→N, 모든 결과가 부모 tag + 분기 idx
    CONSUMED         = "consumed"            # 입력 entity 소멸
    GENERATED_NEW    = "generated_new"       # 입력에 없던 새 entity


@dataclass
class EntityRef:
    """Tag + OCCT entity 의 wrapper. 실제 OCCT shape 는 별도 store 에."""
    tag: str | None         # 사용자 지정 tag 또는 자동
    kind: str               # "face" | "edge" | "vertex"
    bbox_center: tuple[float, float, float]
    measure: float          # 면적 (face) 또는 길이 (edge)
    convexity: str = "n/a"  # "convex" | "concave" | "mixed" | "n/a"


@dataclass
class EntityHistoryMap:
    """1 skill apply 의 history 결과.

    rules:    input role → 적용된 HistoryRule
    children: input entity tag → 결과 entity tag 들 (SPLIT_BRANCH 용)
    new:      GENERATED_NEW 의 신규 entity 들
    consumed: CONSUMED 의 사라진 entity 들
    """

    rules: dict[str, HistoryRule] = field(default_factory=dict)
    children: dict[str, list[str]] = field(default_factory=dict)
    new_entities: list[EntityRef] = field(default_factory=list)
    consumed: list[EntityRef] = field(default_factory=list)
    inherited: list[EntityRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules": {k: v.value for k, v in self.rules.items()},
            "children": self.children,
            "new_entities": [e.__dict__ for e in self.new_entities],
            "consumed": [e.__dict__ for e in self.consumed],
            "inherited": [e.__dict__ for e in self.inherited],
        }


def topology_signature(entities: list[EntityRef]) -> str:
    """[[plan-determinism#topology_signature-계산식]] 의 sha256 prefix."""
    items = []
    for e in entities:
        items.append((
            e.kind,
            tuple(round(c, 3) for c in e.bbox_center),
            round(e.measure, 3),
            e.convexity,
        ))
    items.sort()
    h = hashlib.sha256(json.dumps(items).encode()).hexdigest()
    return h[:16]


@dataclass
class SelectorFreeze:
    """Plan step 의 selector 매칭 결과 동결 (PF-2)."""

    matched_count: int
    sort_key: str = "lexicographic_bbox_center"
    topology_signature: str = ""

    def matches(self, other: "SelectorFreeze") -> bool:
        return (
            self.matched_count == other.matched_count
            and self.topology_signature == other.topology_signature
        )

    @classmethod
    def from_entities(cls, entities: list[EntityRef]) -> "SelectorFreeze":
        # 정렬 키 = bbox center lexicographic
        sorted_ents = sorted(entities, key=lambda e: e.bbox_center)
        return cls(
            matched_count=len(sorted_ents),
            sort_key="lexicographic_bbox_center",
            topology_signature=topology_signature(sorted_ents),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_count": self.matched_count,
            "sort_key": self.sort_key,
            "topology_signature": self.topology_signature,
        }
