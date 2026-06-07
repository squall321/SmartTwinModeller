"""extrude_pocket — atomic.

지정된 face 위에 sketch 모양을 그리고 depth 만큼 안쪽으로 파냄.
디스플레이 step-down, 카메라 lens recess 등.
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
    name="extrude_pocket",
    category="modify/pocket",
    level="atomic",
    summary="Extrude a 2D sketch into a face, subtracting from the body to create a pocket. "
            "Standard step-down (display bezel, camera lens recess).",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "pocket_walls":    HistoryRule.GENERATED_NEW,
        "pocket_bottom":   HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["pocket"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5, "extras": {"max_aspect_ratio": 4.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.pocket_exits_body", "fm.sketch_outside_face"],
    cost_hint=0.15,
    # Catches the silent face-orientation bug — pocket must remove material.
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class ExtrudePocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        sketch: SketchSpec
        depth_mm: float = Field(gt=0, le=10000)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._sketch_to_solid import build_pocket_tool

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"face_selector matched 0 faces: {args.face_selector.model_dump()}"
            )
        target_face = faces[0]

        tool = build_pocket_tool(target_face, args.sketch, args.depth_mm, direction="into")

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("extrude_pocket cut 실패")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":   HistoryRule.MODIFIED_INHERIT,
                "pocket_walls":  HistoryRule.GENERATED_NEW,
                "pocket_bottom": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
