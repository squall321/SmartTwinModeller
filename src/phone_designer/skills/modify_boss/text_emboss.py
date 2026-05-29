"""text_emboss — atomic.

지정된 (planar) face 위에 텍스트를 올려놓고 그 높이만큼 body 에서 밖으로 돌출시킨다 (Fuse).
소비자 제품 하우징의 양각 로고 / 모델 라벨 (예: 'iPhone' 양각).

text_engrave 와 동일 백엔드 (OCCT StdPrs_BRepFont) 를 공유한다.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="text_emboss",
    category="modify/boss",
    level="atomic",
    summary="Emboss Unicode text on a planar face by extruding glyph outlines outward along "
            "the face normal and fusing with the body. Standard raised logo/label "
            "(e.g., 'iPhone' on the rear housing).",
    selector_kinds=["faces"],
    history_rules={
        "target_face":   HistoryRule.MODIFIED_INHERIT,
        "emboss_walls":  HistoryRule.GENERATED_NEW,
        "emboss_top":    HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.height_within_envelope",
    ],
    produces_features=["text_emboss"],
    preserves=["outer_envelope_outside_text"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3,
                              "extras": {"min_stroke_width_mm": 0.3, "max_height_mm": 1.0}},
        "die_cast_al":       {"min_wall_mm": 0.5, "min_draft_deg": 1.0,
                              "extras": {"min_stroke_width_mm": 0.4}},
        "injection_mold_pa": {"min_wall_mm": 0.4, "min_draft_deg": 0.5,
                              "extras": {"min_stroke_width_mm": 0.4}},
    },
    failure_modes=["fm.text_outside_face", "fm.emboss_too_tall"],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class TextEmboss(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        text: str = Field(min_length=1)
        font_name: str = "Arial"
        font_size_mm: float = Field(default=5.0, gt=0, le=200)
        height_mm: float = Field(default=0.3, gt=0, le=20)
        center_x_mm: float = 0.0
        center_y_mm: float = 0.0
        rotation_deg: float = 0.0
        bold: bool = False
        italic: bool = False

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

        from phone_designer.skills._resolvers import (
            _face_center,
            _face_normal_at_center,
            resolve_faces,
        )
        from phone_designer.skills.modify_pocket.text_engrave import build_text_solid

        if not args.text or not args.text.strip():
            raise ValueError("text_emboss: text 가 비어 있음")

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"text_emboss: face_selector matched 0 faces — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]

        face_origin = _face_center(target_face)
        face_normal = _face_normal_at_center(target_face)
        if face_normal == (0.0, 0.0, 0.0):
            raise RuntimeError(
                "text_emboss: target face 가 planar 가 아님 — v0 은 planar face 만 지원"
            )

        tool = build_text_solid(
            text=args.text,
            font_name=args.font_name,
            font_size_mm=args.font_size_mm,
            bold=args.bold,
            italic=args.italic,
            center_x_mm=args.center_x_mm,
            center_y_mm=args.center_y_mm,
            rotation_deg=args.rotation_deg,
            face_origin=face_origin,
            face_normal=face_normal,
            height_mm=args.height_mm,
            direction="out",
        )

        fuse = BRepAlgoAPI_Fuse(shape, tool)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("text_emboss: BRepAlgoAPI_Fuse 실패")
        new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":  HistoryRule.MODIFIED_INHERIT,
                "emboss_walls": HistoryRule.GENERATED_NEW,
                "emboss_top":   HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
