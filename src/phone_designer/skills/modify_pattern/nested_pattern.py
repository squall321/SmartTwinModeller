"""nested_pattern — macro. Pattern of patterns.

Generates a grid of positions by composing an OUTER pattern with an INNER
pattern. For each outer position (px, py), the inner pattern's positions
(ix, iy) are translated by (px, py) and a feature is placed at each
(px + ix, py + iy).

Supported sub-patterns:
    "linear"   — count, spacing_mm, direction ("X"|"Y"),
                 start_offset_x_mm, start_offset_y_mm
    "circular" — count, pitch_radius_mm, center_x_mm, center_y_mm,
                 start_angle_deg, total_sweep_deg

Total feature count = outer_count × inner_count.
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


def _pattern_offsets(
    kind: str, params: dict[str, Any]
) -> list[tuple[float, float]]:
    """Return list of (dx, dy) face-local offsets for the given sub-pattern."""
    if kind == "linear":
        count = int(params.get("count", 0))
        if count < 1:
            raise ValueError(
                f"nested_pattern: linear sub-pattern count must be ≥1, "
                f"got {count}"
            )
        spacing = float(params.get("spacing_mm", 0.0))
        if spacing <= 0:
            raise ValueError(
                f"nested_pattern: linear sub-pattern spacing_mm must be >0, "
                f"got {spacing}"
            )
        direction = params.get("direction", "X")
        if direction not in ("X", "Y"):
            raise ValueError(
                f"nested_pattern: linear direction must be 'X'|'Y', "
                f"got {direction!r}"
            )
        ox = float(params.get("start_offset_x_mm", 0.0))
        oy = float(params.get("start_offset_y_mm", 0.0))
        offsets: list[tuple[float, float]] = []
        for k in range(count):
            if direction == "X":
                offsets.append((ox + k * spacing, oy))
            else:
                offsets.append((ox, oy + k * spacing))
        return offsets

    if kind == "circular":
        count = int(params.get("count", 0))
        if count < 1:
            raise ValueError(
                f"nested_pattern: circular sub-pattern count must be ≥1, "
                f"got {count}"
            )
        r = float(params.get("pitch_radius_mm", 0.0))
        if r <= 0:
            raise ValueError(
                f"nested_pattern: circular pitch_radius_mm must be >0, "
                f"got {r}"
            )
        cx = float(params.get("center_x_mm", 0.0))
        cy = float(params.get("center_y_mm", 0.0))
        start_deg = float(params.get("start_angle_deg", 0.0))
        sweep = float(params.get("total_sweep_deg", 360.0))
        if not (0.0 < sweep <= 360.0):
            raise ValueError(
                f"nested_pattern: total_sweep_deg must be in (0, 360], "
                f"got {sweep}"
            )
        full_circle = abs(sweep - 360.0) < 1e-6
        denom = count if full_circle else max(1, count - 1)
        step = sweep / denom
        offsets = []
        for k in range(count):
            theta = math.radians(start_deg + k * step)
            offsets.append((cx + r * math.cos(theta), cy + r * math.sin(theta)))
        return offsets

    raise ValueError(
        f"nested_pattern: pattern kind must be 'linear'|'circular', "
        f"got {kind!r}"
    )


@skill(
    name="nested_pattern",
    category="modify/pattern",
    level="macro",
    summary="Pattern of patterns — compose two sub-patterns (linear|circular) "
            "to produce outer_count × inner_count feature instances.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "pattern_features": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.count_at_least_two",
    ],
    produces_features=["nested_pattern"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.pattern_exits_face", "fm.features_overlap"],
    cost_hint=0.5,
    expansion=["hole"] * 9,
    post_conditions=[PostCondition(kind="volume_changed")],
)
class NestedPattern(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        # Feature template
        profile_diameter_mm: float = Field(gt=0, le=10000)
        operation: Literal["pocket", "hole", "boss"]
        feature_depth_mm: float | None = Field(default=None, gt=0, le=10000)
        feature_height_mm: float | None = Field(default=None, gt=0, le=10000)
        # Outer/inner sub-pattern selection
        outer_pattern_kind: Literal["linear", "circular"]
        outer_params: dict[str, Any]
        inner_pattern_kind: Literal["linear", "circular"]
        inner_params: dict[str, Any]

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
                f"nested_pattern: face_selector matched 0 faces: "
                f"{args.face_selector.model_dump()}"
            )
        (fcx, fcy, fcz), normal = _face_center_and_normal(faces[0])
        nz_sign = 1 if normal[2] > 0 else -1

        outer = _pattern_offsets(args.outer_pattern_kind, args.outer_params)
        inner = _pattern_offsets(args.inner_pattern_kind, args.inner_params)

        if not outer or not inner:
            raise ValueError(
                f"nested_pattern: outer={len(outer)} inner={len(inner)} — "
                f"both must produce ≥1 position"
            )

        for (ox, oy) in outer:
            for (ix, iy) in inner:
                dx = ox + ix
                dy = oy + iy
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
