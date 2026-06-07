"""linear_pattern — macro. Repeat a circular feature along ±X / ±Y.

Distributes `count` identical features evenly along the chosen axis with
`spacing_mm` step, starting at `(start_offset_x_mm, start_offset_y_mm)`
relative to the selected face center.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="linear_pattern",
    category="modify/pattern",
    level="macro",
    summary="Repeat a circular feature (pocket | hole | boss) in a row along "
            "X or Y with constant spacing. Starts from a face-local offset.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "pattern_features": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.count_at_least_two",
        "pc.spacing_positive",
    ],
    produces_features=["linear_pattern"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.pattern_exits_face", "fm.features_overlap"],
    cost_hint=0.25,
    expansion=["hole"] * 5,    # conceptual: N atomic feature applications
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class LinearPattern(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        # Feature template
        profile_diameter_mm: float = Field(gt=0, le=10000)
        operation: Literal["pocket", "hole", "boss"]
        feature_depth_mm: float | None = Field(default=None, gt=0, le=10000)
        feature_height_mm: float | None = Field(default=None, gt=0, le=10000)
        # Pattern
        count: int = Field(ge=2, le=10000)
        spacing_mm: float = Field(gt=0, le=10000)
        direction: Literal["X", "Y"]
        start_offset_x_mm: float = 0.0
        start_offset_y_mm: float = 0.0

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pattern import (
            _apply_circular_feature,
            _face_center_and_normal,
            _validate_feature_args,
        )

        _validate_feature_args(
            args.operation, args.feature_depth_mm, args.feature_height_mm,
        )

        # Resolve the face ONCE on the input body — patterns operate on a
        # single (planar ±Z) face. After mutation the face hash changes,
        # so we cache its geometric anchor (center + normal) up front.
        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"linear_pattern: face_selector matched 0 faces: "
                f"{args.face_selector.model_dump()}"
            )
        (fcx, fcy, fcz), normal = _face_center_and_normal(faces[0])
        nz_sign = 1 if normal[2] > 0 else -1

        # Apply features sequentially.
        for k in range(args.count):
            if args.direction == "X":
                dx = args.start_offset_x_mm + k * args.spacing_mm
                dy = args.start_offset_y_mm
            else:  # "Y"
                dx = args.start_offset_x_mm
                dy = args.start_offset_y_mm + k * args.spacing_mm
            shape = _apply_circular_feature(
                shape,
                face_cx=fcx, face_cy=fcy, face_cz=fcz, nz_sign=nz_sign,
                feature_x_mm=dx, feature_y_mm=dy,
                profile_diameter_mm=args.profile_diameter_mm,
                operation=args.operation,
                feature_depth_mm=args.feature_depth_mm,
                feature_height_mm=args.feature_height_mm,
            )

        history = EntityHistoryMap(
            rules={
                "target_face":      HistoryRule.MODIFIED_INHERIT,
                "pattern_features": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(shape), history=history)
