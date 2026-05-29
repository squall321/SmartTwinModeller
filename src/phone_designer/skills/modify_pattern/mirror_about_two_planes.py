"""mirror_about_two_planes — macro. 4-quadrant mirror across two world planes.

Apply ONE circular feature at `(original_x_mm, original_y_mm)` (face-local),
then reflect it across two orthogonal world planes (default: XZ and YZ)
producing 4 instances at:
    (+ox, +oy)
    (-ox, +oy)   ← plane_1 = YZ flips X
    (+ox, -oy)   ← plane_2 = XZ flips Y
    (-ox, -oy)   ← composition

Useful for diagonal-symmetric mounting bosses / corner pockets.

Mirror plane semantics (named by the plane's normal axis):
    YZ  → flip X about mirror_origin.x
    XZ  → flip Y about mirror_origin.y
    XY  → flip Z (coincident on a ±Z face — collapses to a coincident hit)
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _reflect(
    ox_w: float, oy_w: float, plane: str, mox: float, moy: float
) -> tuple[float, float]:
    """Reflect a world XY point across the given plane through (mox, moy)."""
    if plane == "YZ":
        return (2 * mox - ox_w, oy_w)
    if plane == "XZ":
        return (ox_w, 2 * moy - oy_w)
    # "XY" plane reflection flips Z. On a ±Z face the in-plane XY is
    # unchanged — the feature collapses onto itself. We keep the value to
    # allow expression of the intent; the boolean is a no-op for coincident
    # features.
    return (ox_w, oy_w)


@skill(
    name="mirror_about_two_planes",
    category="modify/pattern",
    level="macro",
    summary="Apply a circular feature then reflect across TWO orthogonal world "
            "planes (e.g. XZ + YZ) → 4-quadrant symmetric instances.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "pattern_features": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
    ],
    produces_features=["mirror_quad"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.pattern_exits_face", "fm.features_overlap"],
    cost_hint=0.2,
    expansion=["hole"] * 4,   # exactly 4 atomic feature applications
    post_conditions=[PostCondition(kind="volume_changed")],
)
class MirrorAboutTwoPlanes(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        # Feature template
        profile_diameter_mm: float = Field(gt=0, le=100)
        operation: Literal["pocket", "hole", "boss"]
        feature_depth_mm: float | None = Field(default=None, gt=0, le=200)
        feature_height_mm: float | None = Field(default=None, gt=0, le=200)
        # Two reflection planes — must be distinct.
        mirror_plane_1: Literal["XZ", "YZ", "XY"] = "YZ"
        mirror_plane_2: Literal["XZ", "YZ", "XY"] = "XZ"
        mirror_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        # Original feature center in face-local XY.
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

        if args.mirror_plane_1 == args.mirror_plane_2:
            raise ValueError(
                f"mirror_about_two_planes: mirror_plane_1 and mirror_plane_2 "
                f"must differ (got both = {args.mirror_plane_1!r})"
            )

        _validate_feature_args(
            args.operation, args.feature_depth_mm, args.feature_height_mm,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"mirror_about_two_planes: face_selector matched 0 faces: "
                f"{args.face_selector.model_dump()}"
            )
        (fcx, fcy, fcz), normal = _face_center_and_normal(faces[0])
        nz_sign = 1 if normal[2] > 0 else -1

        # Build the 4 world-XY centers: original, mirror1, mirror2, both.
        ox_w = fcx + args.original_x_mm
        oy_w = fcy + args.original_y_mm
        mox, moy, _moz = args.mirror_origin

        p1x, p1y = _reflect(ox_w, oy_w, args.mirror_plane_1, mox, moy)
        p2x, p2y = _reflect(ox_w, oy_w, args.mirror_plane_2, mox, moy)
        # Both reflections (composition is commutative for axis flips).
        p12x, p12y = _reflect(p1x, p1y, args.mirror_plane_2, mox, moy)

        centers_world = [
            (ox_w, oy_w),
            (p1x, p1y),
            (p2x, p2y),
            (p12x, p12y),
        ]

        # De-duplicate near-coincident centers (e.g., original on a mirror
        # plane). Tolerance: 0.001 mm.
        unique: list[tuple[float, float]] = []
        for (x, y) in centers_world:
            if any(
                abs(x - ux) < 1e-3 and abs(y - uy) < 1e-3 for (ux, uy) in unique
            ):
                continue
            unique.append((x, y))

        for (wx, wy) in unique:
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
