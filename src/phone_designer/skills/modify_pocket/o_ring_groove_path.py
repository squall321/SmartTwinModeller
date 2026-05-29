"""o_ring_groove_path — atomic.

Closed-loop O-ring face-seal groove that follows an ARBITRARY 2D path
(not just a circle). Use when a rectangular bezel, a phone back-cover,
or any non-circular flange needs a continuous compression seal.

Sizing (ISO 3601-2 axial / face-seal land, with installer squeeze):

    cs       = cross_section_diameter_mm
    width    = cs * 1.36                       # groove band width
    depth    = cs * (1 - squeeze_ratio) * 0.76 # groove depth

``squeeze_ratio`` is the fraction of the cross-section that is crushed when
the flanges are tightened (typical 0.10 – 0.20 for static face seals;
default 0.15).

Args:
    face_selector:             SelectorRef — planar host face for the seal
                               land. Must have a ±Z normal in world coords
                               (matches the other axial face-seal skills).
    path_points:               list[(x, y)] — centerline polyline in the
                               face-LOCAL XY frame (origin = face centroid,
                               z=0 = face surface). The loop is implicitly
                               closed.
    cross_section_diameter_mm: float        — nominal O-ring W (cs).
    squeeze_ratio:             float = 0.15 — installer squeeze fraction.

Convention: width runs across the path, depth sinks INTO the face along its
inward normal. See ``_closed_path_sweep.build_closed_groove``.
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
    name="o_ring_groove_path",
    category="modify/pocket",
    level="atomic",
    summary="Closed-loop O-ring face-seal groove that follows an arbitrary "
            "2D path on a planar face. Width/depth sized per ISO 3601-2 "
            "from the cross-section diameter and installer squeeze ratio.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "groove_walls":    HistoryRule.GENERATED_NEW,
        "groove_floor":    HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.path_has_two_or_more_points",
    ],
    produces_features=["o_ring_groove", "face_seal_groove", "closed_path_groove"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3, "min_fillet_r_mm": 0.2},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_recommended": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.path_self_intersect",
        "fm.groove_outside_face",
        "fm.squeeze_out_of_range",
    ],
    cost_hint=0.30,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class ORingGroovePath(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        path_points: list[tuple[float, float]] = Field(min_length=3)
        cross_section_diameter_mm: float = Field(gt=0.0, le=20.0)
        squeeze_ratio: float = Field(default=0.15, ge=0.0, lt=0.5)

    def _apply(self, body: Any, args: Args) -> SkillResult:
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
                f"o_ring_groove_path: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]

        cs = args.cross_section_diameter_mm
        width = cs * 1.36
        depth = cs * (1.0 - args.squeeze_ratio) * 0.76

        cutter = build_closed_groove(
            face=target_face,
            path_points=list(args.path_points),
            width_mm=width,
            depth_mm=depth,
        )

        cut = BRepAlgoAPI_Cut(shape, cutter)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("o_ring_groove_path: body cut failed")
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
