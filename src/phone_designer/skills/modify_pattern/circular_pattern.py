"""circular_pattern — macro. Repeat a circular feature around a face axis.

Distributes `count` features evenly along an angular sweep on the selected
face. `total_sweep_deg == 360` packs the count uniformly around the full
circle; any other sweep distributes inclusively between the two endpoints.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="circular_pattern",
    category="modify/pattern",
    level="macro",
    summary="Repeat a circular feature (pocket | hole | boss) around an axis "
            "on the selected face. Full 360° ring or partial arc.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "pattern_features": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.count_at_least_two",
        "pc.pitch_radius_positive",
    ],
    produces_features=["circular_pattern"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.pattern_exits_face", "fm.features_overlap"],
    cost_hint=0.3,
    expansion=["hole"] * 6,    # conceptual: N atomic feature applications
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class CircularPattern(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        # Feature template
        profile_diameter_mm: float = Field(gt=0, le=10000)
        operation: Literal["pocket", "hole", "boss"]
        feature_depth_mm: float | None = Field(default=None, gt=0, le=10000)
        feature_height_mm: float | None = Field(default=None, gt=0, le=10000)
        # Pattern
        count: int = Field(ge=2, le=10000)
        pitch_radius_mm: float = Field(gt=0, le=10000)
        center_x_mm: float = 0.0
        center_y_mm: float = 0.0
        start_angle_deg: float = 0.0
        total_sweep_deg: float = Field(default=360.0, gt=0.0, le=360.0)

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

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"circular_pattern: face_selector matched 0 faces: "
                f"{args.face_selector.model_dump()}"
            )
        (fcx, fcy, fcz), normal = _face_center_and_normal(faces[0])
        nz_sign = 1 if normal[2] > 0 else -1

        # Compute angular step. Full circle wraps so we divide by count;
        # partial arc spans endpoints inclusively so divide by (count - 1).
        full_circle = abs(args.total_sweep_deg - 360.0) < 1e-6
        denom = args.count if full_circle else max(1, args.count - 1)
        step_deg = args.total_sweep_deg / denom

        for k in range(args.count):
            theta = math.radians(args.start_angle_deg + k * step_deg)
            dx = args.center_x_mm + args.pitch_radius_mm * math.cos(theta)
            dy = args.center_y_mm + args.pitch_radius_mm * math.sin(theta)
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
