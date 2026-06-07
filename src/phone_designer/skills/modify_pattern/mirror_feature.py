"""mirror_feature — macro. Reflect one circular feature across a world plane.

Strict 2-feature mirror: applies the feature at `(original_x_mm, original_y_mm)`
on the selected face, then again at the position obtained by reflecting
`(original_x + face_cx, original_y + face_cy, face_cz)` across
`mirror_plane` through `mirror_origin`, and re-expressing the reflected
point back in face-local XY.

Mirror planes (named after the plane's normal axis — i.e. the axis that the
reflection NEGATES):
    YZ  →  flip X   (plane normal = X)
    XZ  →  flip Y   (plane normal = Y)
    XY  →  flip Z   (plane normal = Z)
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
    name="mirror_feature",
    category="modify/pattern",
    level="macro",
    summary="Strict 2-feature mirror — apply a circular feature once at the "
            "given face-local origin and once at its reflection across a "
            "world plane (XY | YZ | XZ).",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "pattern_features": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.count_is_two",
    ],
    produces_features=["mirror_pair"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.pattern_exits_face", "fm.features_overlap"],
    cost_hint=0.15,
    expansion=["hole", "hole"],    # exactly 2 atomic feature applications
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class MirrorFeature(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        # Feature template
        profile_diameter_mm: float = Field(gt=0, le=10000)
        operation: Literal["pocket", "hole", "boss"]
        feature_depth_mm: float | None = Field(default=None, gt=0, le=10000)
        feature_height_mm: float | None = Field(default=None, gt=0, le=10000)
        # Pattern
        count: Literal[2] = 2   # strict mirror — exactly 2 features
        mirror_plane: Literal["XZ", "YZ", "XY"]
        mirror_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        original_x_mm: float
        original_y_mm: float

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
                f"mirror_feature: face_selector matched 0 faces: "
                f"{args.face_selector.model_dump()}"
            )
        (fcx, fcy, fcz), normal = _face_center_and_normal(faces[0])
        nz_sign = 1 if normal[2] > 0 else -1

        # Original feature center in WORLD coords.
        ox_w = fcx + args.original_x_mm
        oy_w = fcy + args.original_y_mm
        oz_w = fcz  # feature lives on the face plane

        # Mirror across the plane through `mirror_origin`.
        mox, moy, _moz = args.mirror_origin
        if args.mirror_plane == "YZ":
            # Plane normal = X → flip X about mox.
            mx_w = 2 * mox - ox_w
            my_w = oy_w
        elif args.mirror_plane == "XZ":
            # Plane normal = Y → flip Y about moy.
            mx_w = ox_w
            my_w = 2 * moy - oy_w
        else:  # "XY"
            # Plane normal = Z → flip Z. The feature stays on the same face
            # in XY, so face-local XY is unchanged. (For pocket/hole/boss on
            # a ±Z face this collapses to two coincident features. Rare but
            # legal — the second boolean is a no-op.)
            mx_w = ox_w
            my_w = oy_w

        # Re-express both feature centers in face-local XY (cylinder anchor
        # Z stays at the face plane).
        features_local = [
            (ox_w - fcx, oy_w - fcy),
            (mx_w - fcx, my_w - fcy),
        ]

        for dx, dy in features_local:
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
