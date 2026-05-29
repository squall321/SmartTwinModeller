"""extrude_plateau — atomic.

지정된 face 위에 sketch 모양을 그리고 height 만큼 바깥쪽으로 돌출.
카메라 bump, 크라운 plinth 등.
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
    name="extrude_plateau",
    category="modify/plateau",
    level="atomic",
    summary="Extrude a 2D sketch outward from a face by a given height, unioning to the body. "
            "Standard for camera bump / crown plinth.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":      HistoryRule.MODIFIED_INHERIT,
        "plateau_walls":    HistoryRule.GENERATED_NEW,
        "plateau_top":      HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.height_positive",
    ],
    produces_features=["plateau"],
    preserves=["outer_envelope"],
    manufacturing={
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
        "cnc_3axis":         {"min_wall_mm": 0.5},
    },
    failure_modes=["fm.sketch_outside_face"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class ExtrudePlateau(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        sketch: SketchSpec
        height_mm: float = Field(gt=0, le=50)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._sketch_to_solid import build_pocket_tool

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"face_selector matched 0 faces: {args.face_selector.model_dump()}"
            )
        target_face = faces[0]

        tool = build_pocket_tool(target_face, args.sketch, args.height_mm, direction="out")

        fuse = BRepAlgoAPI_Fuse(shape, tool)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("extrude_plateau fuse 실패")
        new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":   HistoryRule.MODIFIED_INHERIT,
                "plateau_walls": HistoryRule.GENERATED_NEW,
                "plateau_top":   HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
