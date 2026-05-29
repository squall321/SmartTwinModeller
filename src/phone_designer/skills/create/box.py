"""box — atomic create skill.

가장 단순한 prism. 모든 형상의 출발점.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule, EntityRef
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="box",
    category="create",
    level="atomic",
    summary="Center-aligned rectangular box at Z=0 baseplane (XY centered, Z bottom).",
    selector_kinds=[],
    history_rules={"output_faces": HistoryRule.GENERATED_NEW},
    produces_features=["box_solid"],
    preserves=[],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["zero_or_negative_dimension"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class Box(SkillBase):
    class Args(BaseModel):
        length_mm: float = Field(gt=0)
        width_mm: float = Field(gt=0)
        height_mm: float = Field(gt=0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        # body 는 무시 (create skill 은 새 형상 생성)
        from build123d import Box as B123dBox, Align
        b = B123dBox(
            args.length_mm, args.width_mm, args.height_mm,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )

        # 결과 face 들을 EntityRef 로 기록
        faces = _faces_to_entity_refs(b.wrapped)
        history = EntityHistoryMap(
            rules={"output_faces": HistoryRule.GENERATED_NEW},
            new_entities=faces,
        )
        return SkillResult(body=b, history=history)


def _faces_to_entity_refs(shape) -> list[EntityRef]:
    """OCCT shape 의 모든 face → EntityRef list. 본 파일에서만 사용 (skill 마다 변형 가능)."""
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    refs = []
    it = TopExp_Explorer(shape, TopAbs_FACE)
    idx = 0
    while it.More():
        face = it.Current()
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        c = props.CentreOfMass()
        area = props.Mass()
        refs.append(EntityRef(
            tag=None,
            kind="face",
            bbox_center=(round(c.X(), 3), round(c.Y(), 3), round(c.Z(), 3)),
            measure=round(area, 3),
            convexity="n/a",
        ))
        idx += 1
        it.Next()
    return refs
