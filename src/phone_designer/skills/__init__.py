"""Phone Designer skill 라이브러리.

[[lat.md/skills.md]] 카탈로그 + [[lat.md/concepts.md#skill]] 추상화.

Public API:
    @skill           — skill 클래스 데코레이터
    SkillBase        — 모든 skill 의 base
    SkillResult      — apply() 의 반환 (body + history + freeze)
    SkillRegistry    — 전역 registry
    Selector base    — face/edge/vertex 선택
    EntityHistoryMap, SelectorFreeze — PF-1, PF-2
"""
from phone_designer.skills._history import (
    HistoryRule,
    EntityHistoryMap,
    SelectorFreeze,
    topology_signature,
)
from phone_designer.skills._selectors import (
    Selector,
    SelectorRef,
    TaggedSelector,
    FaceNamedSelector,
    AxisAlignedEdgesSelector,
    EdgesOnFaceSelector,
    EdgesByLengthSelector,
    EdgesByPositionSelector,
    FacesByNormalSelector,
    AndSel,
    OrSel,
    NotSel,
    FirstN,
    LargestN,
    selector_from_dict,
)
from phone_designer.skills._spec import (
    SkillSpec,
    SkillLevel,
    ManufacturingSpec,
    ProcessRule,
    SkillBase,
    SkillResult,
)
from phone_designer.skills._registry import (
    skill,
    SkillRegistry,
    registry,
)

__all__ = [
    # history
    "HistoryRule",
    "EntityHistoryMap",
    "SelectorFreeze",
    "topology_signature",
    # selectors
    "Selector",
    "SelectorRef",
    "TaggedSelector",
    "FaceNamedSelector",
    "AxisAlignedEdgesSelector",
    "EdgesOnFaceSelector",
    "EdgesByLengthSelector",
    "EdgesByPositionSelector",
    "FacesByNormalSelector",
    "AndSel",
    "OrSel",
    "NotSel",
    "FirstN",
    "LargestN",
    "selector_from_dict",
    # spec
    "SkillSpec",
    "SkillLevel",
    "ManufacturingSpec",
    "ProcessRule",
    "SkillBase",
    "SkillResult",
    # registry
    "skill",
    "SkillRegistry",
    "registry",
]
