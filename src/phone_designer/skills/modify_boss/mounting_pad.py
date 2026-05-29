"""mounting_pad — atomic. 부품 부착 평탄 pad.

부품의 적용 면 (디스플레이 perimeter, PCB 부착 등) 의 평탄/단단 pad.
Phase 4 v1: rectangle pad on face_named=top/bottom.
extrude_plateau 와 비슷하지만 표면 마감 (roughness/flatness) 메타데이터 차이.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec


@skill(
    name="mounting_pad",
    category="modify/boss",
    level="atomic",
    summary="Flat raised pad for part mounting (display perimeter, PCB attach). "
            "Same geometry as extrude_plateau but with surface-finish metadata "
            "(roughness_ra, flatness) for downstream DFM.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":  HistoryRule.MODIFIED_INHERIT,
        "pad_top":      HistoryRule.GENERATED_NEW,
        "pad_walls":    HistoryRule.GENERATED_NEW,
    },
    produces_features=["mounting_pad"],
    preserves=["outer_envelope_outside_pad"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"surface_finish_ra_um": 1.6, "flatness_mm": 0.05}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_flatness": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"flatness_mm": 0.2}},
    },
    failure_modes=["fm.sketch_outside_face"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class MountingPad(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        sketch: SketchSpec
        height_mm: float = Field(gt=0, le=20)
        required_roughness_ra_um: float | None = Field(
            default=None,
            description="(메타) DFM 의 표면 거칠기 요구 — Phase 5 의 DFM 에서 사용",
        )
        required_flatness_mm: float | None = Field(
            default=None,
            description="(메타) DFM 의 평탄도 요구 — Phase 5 에서 사용",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._sketch_to_solid import build_pocket_tool

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"mounting_pad: face_selector matched 0 — {args.face_selector.model_dump()}"
            )
        target_face = faces[0]

        tool = build_pocket_tool(target_face, args.sketch, args.height_mm, direction="out")

        fuse = BRepAlgoAPI_Fuse(shape, tool)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("mounting_pad fuse 실패")
        new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face": HistoryRule.MODIFIED_INHERIT,
                "pad_top":     HistoryRule.GENERATED_NEW,
                "pad_walls":   HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
