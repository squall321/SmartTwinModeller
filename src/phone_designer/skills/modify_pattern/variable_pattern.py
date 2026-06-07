"""variable_pattern — macro. Linear array where instances scale & rotate by law.

Distributes `count` features along a direction with constant `spacing_mm`,
but each instance has its profile diameter scaled and its position rotated
about a local origin according to a linear law parameterized by k/(count-1).

For k in 0..count-1:
    t            = k / (count - 1)
    scale_k      = (1 - t) * scale_start + t * scale_end
    rotation_k   = (1 - t) * rotation_start_deg + t * rotation_end_deg
    position_k   = base_offset + step_k * direction_unit
    position_k'  = rotate(position_k, rotation_k about (0, 0))   # face-local

The feature diameter for instance k is `profile_diameter_mm * scale_k`.
This produces e.g. a tapered row of holes or a curved fan when the law has
non-zero rotation.

All transforms happen in face-local XY on a planar +Z / -Z face.
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
    name="variable_pattern",
    category="modify/pattern",
    level="macro",
    summary="Linear array where each instance scales and rotates by a linear "
            "law (start→end). Produces tapered rows or fan arrays.",
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
    produces_features=["variable_pattern"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.pattern_exits_face", "fm.features_overlap"],
    cost_hint=0.4,
    expansion=["hole"] * 5,
    post_conditions=[PostCondition(kind="volume_changed")],
)
class VariablePattern(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        # Feature template (base diameter — scaled per instance)
        profile_diameter_mm: float = Field(gt=0, le=10000)
        operation: Literal["pocket", "hole", "boss"]
        feature_depth_mm: float | None = Field(default=None, gt=0, le=10000)
        feature_height_mm: float | None = Field(default=None, gt=0, le=10000)
        # Pattern (linear backbone)
        count: int = Field(ge=2, le=10000)
        spacing_mm: float = Field(gt=0, le=10000)
        direction: Literal["X", "Y"] = "X"
        start_offset_x_mm: float = 0.0
        start_offset_y_mm: float = 0.0
        # Laws — linear interpolation start→end over t = k/(count-1)
        scale_start: float = Field(default=1.0, gt=0.01, le=10.0)
        scale_end: float = Field(default=1.0, gt=0.01, le=10.0)
        rotation_start_deg: float = 0.0
        rotation_end_deg: float = 0.0
        # Rotation pivot (face-local XY origin). Position vectors are rotated
        # about this point before being added to the face center.
        rotation_pivot_x_mm: float = 0.0
        rotation_pivot_y_mm: float = 0.0

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pattern import (
            _apply_circular_feature,
            _face_center_and_normal,
            _validate_feature_args,
        )

        # Base validation only — per-instance diameter is scaled below but
        # the operation × required-arg pairing is identical for all instances.
        _validate_feature_args(
            args.operation, args.feature_depth_mm, args.feature_height_mm,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"variable_pattern: face_selector matched 0 faces: "
                f"{args.face_selector.model_dump()}"
            )
        (fcx, fcy, fcz), normal = _face_center_and_normal(faces[0])
        nz_sign = 1 if normal[2] > 0 else -1

        denom = max(1, args.count - 1)
        pivx = args.rotation_pivot_x_mm
        pivy = args.rotation_pivot_y_mm

        for k in range(args.count):
            t = k / denom
            scale_k = (1.0 - t) * args.scale_start + t * args.scale_end
            rot_k_deg = (
                (1.0 - t) * args.rotation_start_deg
                + t * args.rotation_end_deg
            )

            # Linear backbone position in face-local XY.
            if args.direction == "X":
                bx = args.start_offset_x_mm + k * args.spacing_mm
                by = args.start_offset_y_mm
            else:  # "Y"
                bx = args.start_offset_x_mm
                by = args.start_offset_y_mm + k * args.spacing_mm

            # Rotate about (pivx, pivy).
            theta = math.radians(rot_k_deg)
            ct = math.cos(theta)
            st = math.sin(theta)
            rx = bx - pivx
            ry = by - pivy
            dx = pivx + (rx * ct - ry * st)
            dy = pivy + (rx * st + ry * ct)

            diameter_k = args.profile_diameter_mm * scale_k
            # Clamp to schema bounds for the per-instance value too.
            if diameter_k <= 0:
                raise ValueError(
                    f"variable_pattern: instance {k} scaled diameter "
                    f"= {diameter_k:.4f} (must be > 0)"
                )

            shape = _apply_circular_feature(
                shape,
                face_cx=fcx, face_cy=fcy, face_cz=fcz, nz_sign=nz_sign,
                feature_x_mm=dx, feature_y_mm=dy,
                profile_diameter_mm=diameter_k,
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
