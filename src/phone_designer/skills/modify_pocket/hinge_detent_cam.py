"""hinge_detent_cam — atomic. Pivot hole + N detent notches around it.

A foldable/clamshell hinge cam plate: a central pivot bore plus a small ring
of detent notches (semicircular cuts) at specified angles. The detents
provide the tactile "click" stops at the open and closed positions of the
hinge — the mating part has a spring-loaded ball that drops into each notch
as the hinge rotates.

Geometry:
  - Central through-hole of diameter `pivot_d_mm` at `position_xy` on the
    target face.
  - `detent_count` semi-cylindrical notches placed at angles
    `detent_angles_deg` (measured CCW from +X). Each notch is a small
    cylinder of radius `detent_r_mm` whose axis is parallel to the face
    normal, centered on a circle of radius (pivot_r + detent_r) so the
    notch tangents the pivot bore.

v1 supports planar ±Z target faces.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, model_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._resolvers import (
    _face_center,
    _face_normal_at_center,
    resolve_faces,
)
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="hinge_detent_cam",
    category="modify/pocket",
    level="atomic",
    summary="Clamshell-hinge cam: central pivot bore plus N semicircular "
            "detent notches at given angles around the pivot. The detents "
            "provide tactile stops at open/closed positions. v1 supports "
            "planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":   HistoryRule.MODIFIED_INHERIT,
        "pivot_bore":    HistoryRule.GENERATED_NEW,
        "detent_walls":  HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.diameter_positive",
        "pc.detent_count_matches_angles",
    ],
    produces_features=["hinge_pivot_bore", "hinge_detents"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4,
                              "extras": {"max_aspect_ratio": 6.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.pivot_exits_body",
        "fm.detent_overlaps_pivot",
        "fm.detent_count_mismatch",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class HingeDetentCam(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of pivot center.",
        )
        pivot_d_mm: float = Field(default=2.0, gt=0, le=20,
                                   description="Pivot through-bore diameter.")
        detent_count: int = Field(default=2, ge=0, le=24,
                                   description="Number of detent notches.")
        detent_r_mm: float = Field(default=0.4, gt=0, le=5,
                                    description="Detent notch radius.")
        detent_angles_deg: list[float] = Field(
            default_factory=lambda: [30.0, 150.0],
            description="Notch angles (CCW from +X). Length must equal detent_count.",
        )

        @model_validator(mode="after")
        def _angles_match_count(self):
            if len(self.detent_angles_deg) != self.detent_count:
                raise ValueError(
                    f"hinge_detent_cam: detent_angles_deg has "
                    f"{len(self.detent_angles_deg)} entries but detent_count="
                    f"{self.detent_count}"
                )
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"hinge_detent_cam: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "hinge_detent_cam v1: planar +Z/-Z target faces only"
            )

        nz_sign = 1.0 if normal[2] > 0 else -1.0
        face_z = center[2]
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]

        # We don't know the body thickness without measuring; use a generous
        # cylinder length that will clearly traverse a typical thin housing.
        # Boolean cut clips it naturally to the body.
        bore_length = 200.0
        # cylinder grows in axis_dir from base; we want base at the face level
        # and cylinder pointing INTO the body (opposite face normal).
        axis_dir = gp_Dir(0.0, 0.0, -nz_sign)
        base = gp_Pnt(cx, cy, face_z)
        ax = gp_Ax2(base, axis_dir)

        # 1. Pivot through-bore.
        pivot_mk = BRepPrimAPI_MakeCylinder(ax, args.pivot_d_mm / 2.0, bore_length)
        pivot_mk.Build()
        if not pivot_mk.IsDone():
            raise RuntimeError("hinge_detent_cam: pivot cylinder build failed")
        cut = BRepAlgoAPI_Cut(shape, pivot_mk.Shape())
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("hinge_detent_cam: pivot boolean cut failed")
        new_shape = cut.Shape()

        # 2. Detent notches — tangent to pivot bore at each angle.
        pivot_r = args.pivot_d_mm / 2.0
        detent_r = args.detent_r_mm
        ring_r = pivot_r + detent_r   # distance from pivot center to notch axis
        # Detents only go a short way into the body so they form a notch on the
        # rim of the bore (not another through hole). Make depth a few × the
        # detent radius — clipped by the body anyway.
        detent_depth = max(2.0 * detent_r, 1.0)

        for angle_deg in args.detent_angles_deg:
            theta = math.radians(angle_deg)
            nx = cx + ring_r * math.cos(theta)
            ny = cy + ring_r * math.sin(theta)
            n_base = gp_Pnt(nx, ny, face_z)
            n_ax = gp_Ax2(n_base, axis_dir)
            n_mk = BRepPrimAPI_MakeCylinder(n_ax, detent_r, detent_depth)
            n_mk.Build()
            if not n_mk.IsDone():
                raise RuntimeError(
                    f"hinge_detent_cam: detent cylinder build failed at "
                    f"angle {angle_deg}"
                )
            cut_n = BRepAlgoAPI_Cut(new_shape, n_mk.Shape())
            cut_n.Build()
            if not cut_n.IsDone():
                raise RuntimeError(
                    f"hinge_detent_cam: detent boolean cut failed at angle "
                    f"{angle_deg}"
                )
            new_shape = cut_n.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":  HistoryRule.MODIFIED_INHERIT,
                "pivot_bore":   HistoryRule.GENERATED_NEW,
                "detent_walls": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
