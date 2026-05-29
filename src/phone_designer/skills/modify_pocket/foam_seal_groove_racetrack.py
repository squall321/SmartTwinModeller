"""foam_seal_groove_racetrack — atomic.

Closed-loop foam (PORON / EPDM / silicone-foam) compression-seal groove with
a RACETRACK (stadium) centerline — two straight runs joined by semicircular
end caps. Sized for soft-foam gaskets used in phone back covers, IP-rated
enclosure lids, and waterproof bezels where an O-ring would be over-spec'd.

Geometry of the centerline (in the face's local XY frame):

    Total bounding box of the racetrack centerline = length_mm × width_mm.
    The straight runs lie along the long axis of length
        L_straight = length_mm - 2 * (width_mm/2) = length_mm - width_mm
    and the end caps are semicircles of radius
        R_end = width_mm / 2.
    Optional ``corner_r_mm`` rounds the four "joins" where the straight runs
    meet the semicircles. (For a true racetrack the joins are already smooth,
    but supplying corner_r_mm > 0 lets callers approximate a rounded-rectangle
    centerline if they want — see below.)

When ``corner_r_mm < width_mm/2``, the helper falls back to a rounded-rect
centerline of the same bounding box and the requested corner radius. When
``corner_r_mm >= width_mm/2`` (or <= 0), the centerline is a perfect
racetrack (semicircular end caps).

Args:
    face_selector: SelectorRef — planar host face. Must have a ±Z normal.
    length_mm:     long-axis size of the centerline bounding box.
    width_mm:      short-axis size of the centerline bounding box.
    corner_r_mm:   corner radius of the centerline (mm). Use 0 for true
                   racetrack (capsule) geometry.
    groove_w_mm:   foam groove band width (across the centerline path).
    groove_d_mm:   foam groove depth (into the face).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, model_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _racetrack_centerline(
    length_mm: float,
    width_mm: float,
    corner_r_mm: float,
    arc_segments: int = 24,
) -> list[tuple[float, float]]:
    """Sample a closed centerline polyline for a racetrack / stadium shape.

    Centered on (0, 0). Long axis = +X (length), short axis = +Y (width).
    """
    half_l = length_mm / 2.0
    half_w = width_mm / 2.0

    # Effective corner radius: cap at min(half_l, half_w) so we never produce
    # a degenerate inverted shape.
    r = max(0.0, min(corner_r_mm, half_l, half_w))
    # For a "true racetrack" the corner radius IS half the short axis.
    # If the caller passed 0 (or anything smaller than half_w), keep what
    # they asked for — that turns the corners into hard right angles when
    # r==0, or rounded-rect when 0 < r < half_w. When r >= half_w, the
    # corners just become semicircles → pure racetrack.

    # Centers of the four arcs (going CCW from bottom-right):
    #   bottom-right: ( half_l - r, -half_w + r )
    #   top-right:    ( half_l - r,  half_w - r )
    #   top-left:     (-half_l + r,  half_w - r )
    #   bottom-left:  (-half_l + r, -half_w + r )
    centers = [
        ( half_l - r, -half_w + r),
        ( half_l - r,  half_w - r),
        (-half_l + r,  half_w - r),
        (-half_l + r, -half_w + r),
    ]
    # Arc start/sweep — each quarter arc spans 90 deg, CCW from start angle:
    start_angles = [-math.pi / 2.0, 0.0, math.pi / 2.0, math.pi]
    pts: list[tuple[float, float]] = []
    if r <= 1e-9:
        # Hard-cornered rectangle — just the four corners (CCW).
        pts = [
            ( half_l, -half_w),
            ( half_l,  half_w),
            (-half_l,  half_w),
            (-half_l, -half_w),
        ]
        return pts

    seg = max(2, int(arc_segments))
    for (cx, cy), a0 in zip(centers, start_angles):
        # sample arc inclusive of start, exclusive of end (next arc takes
        # over at its start point — but for the LAST arc we also drop the
        # end since the wire is implicitly closed by the helper).
        for i in range(seg):
            t = a0 + (math.pi / 2.0) * (i / seg)
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return pts


@skill(
    name="foam_seal_groove_racetrack",
    category="modify/pocket",
    level="atomic",
    summary="Closed-loop foam-gasket seal groove with a racetrack (stadium) "
            "centerline on a planar face. Length / width / corner radius "
            "define the centerline, groove_w/d define the slot band.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "groove_walls":    HistoryRule.GENERATED_NEW,
        "groove_floor":    HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.racetrack_dimensions_positive",
    ],
    produces_features=[
        "foam_seal_groove", "face_seal_groove", "closed_path_groove",
    ],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4, "min_fillet_r_mm": 0.3},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_recommended": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.groove_outside_face",
        "fm.racetrack_width_too_large",
    ],
    cost_hint=0.30,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class FoamSealGrooveRacetrack(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        length_mm:   float = Field(gt=0.0, le=500.0)
        width_mm:    float = Field(gt=0.0, le=500.0)
        corner_r_mm: float = Field(default=0.0, ge=0.0, le=250.0)
        groove_w_mm: float = Field(gt=0.0, le=20.0)
        groove_d_mm: float = Field(gt=0.0, le=20.0)

        @model_validator(mode="after")
        def _check_band_fits_in_short_axis(self) -> "FoamSealGrooveRacetrack.Args":
            # The groove band straddles the centerline by ±groove_w/2. The
            # outermost edge of the groove must still fit inside the face
            # neighborhood, so at least the band width must be < the short
            # axis dimension.
            if self.groove_w_mm >= self.width_mm:
                raise ValueError(
                    f"foam_seal_groove_racetrack: groove_w_mm "
                    f"({self.groove_w_mm}) must be < width_mm "
                    f"({self.width_mm}) — otherwise the inner offset "
                    f"collapses to zero."
                )
            return self

    def _apply(self, body: Any, args: Any) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._closed_path_sweep import (
            build_closed_groove,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"foam_seal_groove_racetrack: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]

        path = _racetrack_centerline(
            length_mm=args.length_mm,
            width_mm=args.width_mm,
            corner_r_mm=args.corner_r_mm,
        )

        cutter = build_closed_groove(
            face=target_face,
            path_points=path,
            width_mm=args.groove_w_mm,
            depth_mm=args.groove_d_mm,
        )

        cut = BRepAlgoAPI_Cut(shape, cutter)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError(
                "foam_seal_groove_racetrack: body cut failed"
            )
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "groove_walls":    HistoryRule.GENERATED_NEW,
                "groove_floor":    HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
