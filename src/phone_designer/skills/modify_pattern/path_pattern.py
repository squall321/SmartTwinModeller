"""path_pattern — macro. Distribute features evenly along a polyline path.

Parameterizes the polyline by chord length so each of `count` features sits
at equal arc-length intervals (t = k / (count-1) for k in 0..count-1). The
endpoints are always sampled.

`path_points` are given in WORLD coords. Their Z is ignored — features are
anchored to the selected planar ±Z face plane. Only the XY components map
to face-local positions (after subtracting the face center).
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
    name="path_pattern",
    category="modify/pattern",
    level="macro",
    summary="Distribute a circular feature evenly along a polyline path "
            "(chord-length parameterization). Endpoints always included.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "pattern_features": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.count_at_least_two",
        "pc.path_has_two_points",
    ],
    produces_features=["path_pattern"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.pattern_exits_face", "fm.features_overlap"],
    cost_hint=0.35,
    expansion=["hole"] * 8,    # conceptual: N atomic feature applications
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class PathPattern(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        # Feature template
        profile_diameter_mm: float = Field(gt=0, le=100)
        operation: Literal["pocket", "hole", "boss"]
        feature_depth_mm: float | None = Field(default=None, gt=0, le=200)
        feature_height_mm: float | None = Field(default=None, gt=0, le=200)
        # Pattern
        path_points: list[tuple[float, float, float]] = Field(min_length=2)
        count: int = Field(ge=2, le=500)

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
                f"path_pattern: face_selector matched 0 faces: "
                f"{args.face_selector.model_dump()}"
            )
        (fcx, fcy, fcz), normal = _face_center_and_normal(faces[0])
        nz_sign = 1 if normal[2] > 0 else -1

        # Precompute cumulative chord lengths along the (XY-projected) polyline.
        pts2d = [(p[0], p[1]) for p in args.path_points]
        cum: list[float] = [0.0]
        for i in range(1, len(pts2d)):
            ax, ay = pts2d[i - 1]
            bx, by = pts2d[i]
            cum.append(cum[-1] + math.hypot(bx - ax, by - ay))
        total = cum[-1]
        if total <= 1e-9:
            raise ValueError(
                "path_pattern: polyline total length is 0 — all path_points "
                "coincide in XY"
            )

        # For each k compute target arc length and binary-search the segment.
        for k in range(args.count):
            t = k / (args.count - 1)
            target = t * total
            # Find segment i such that cum[i] <= target <= cum[i+1].
            # Linear scan — count is small (≤ 500).
            seg = len(cum) - 2
            for i in range(len(cum) - 1):
                if cum[i + 1] >= target - 1e-12:
                    seg = i
                    break
            seg_len = cum[seg + 1] - cum[seg]
            u = 0.0 if seg_len < 1e-12 else (target - cum[seg]) / seg_len
            ax, ay = pts2d[seg]
            bx, by = pts2d[seg + 1]
            wx = ax + u * (bx - ax)
            wy = ay + u * (by - ay)
            # Convert world XY → face-local (cylinder anchor Z stays at face).
            dx = wx - fcx
            dy = wy - fcy
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
