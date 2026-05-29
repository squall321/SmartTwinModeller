"""spring_bar_lug_pair — macro. Two opposing lug ears with a coaxial through-hole
for a watch / wearable strap spring bar.

Geometry per side:
  - A small rectangular lug ear extruded from the host face.
    Footprint: (along Y) = lug_thickness_mm   (slim, in the bar-axis direction)
               (along X) = footprint_x_mm     (derived from bar_d + 2 * inset)
    Height (above face)  = lug_height_mm
  - A coaxial through-hole of diameter bar_d_mm at z = face_z + lug_height/2,
    centered hole_inset_mm from the lug's outer face.

Lug pair separation along Y (the bar axis):
  - lug centers placed at ±lug_separation_mm/2 from the host face center.
  - holes are coaxial along the +Y / -Y direction, so a single straight
    spring bar of length ≈ lug_separation_mm would seat through both holes.

v1: planar +Z / -Z host faces only (typical case face of a watch / earbud
case top or bottom).

expansion: [union(lug+y), union(lug-y), hole(through both)]
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

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
    name="spring_bar_lug_pair",
    category="modify/boss",
    level="macro",
    summary="Pair of opposing lug ears with a coaxial through-hole for a "
            "watch / wearable strap spring bar. Lugs grow out of the target "
            "face along its outward normal; bar axis is +Y in the face frame. "
            "v1 supports planar ±Z host faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":   HistoryRule.MODIFIED_INHERIT,
        "lug_solids":    HistoryRule.GENERATED_NEW,
        "spring_bar_id": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.lug_separation_positive",
    ],
    produces_features=["spring_bar_lugs", "through_hole_pair"],
    preserves=["outer_envelope_outside_lugs"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"min_lug_thickness_mm": 1.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.lug_too_close_to_housing_edge",
        "fm.bar_hole_breaks_lug_wall",
    ],
    cost_hint=0.3,
    expansion=["union", "union", "hole", "hole"],
    post_conditions=[PostCondition(kind="volume_increased")],
)
class SpringBarLugPair(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        lug_separation_mm: float = Field(default=20.0, gt=0, le=80,
                                          description="centre-to-centre lug spacing along +Y")
        lug_h_mm: float = Field(default=3.0, gt=0, le=15,
                                 description="lug projection above the host face")
        lug_t_mm: float = Field(default=2.0, gt=0, le=10,
                                 description="lug wall thickness along Y (bar axis)")
        bar_d_mm: float = Field(default=1.5, gt=0, le=5,
                                 description="spring bar / pin diameter")
        hole_inset_mm: float = Field(default=0.6, gt=0, le=5,
                                      description="bar axis offset from the lug's outer (Y-far) face")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"spring_bar_lug_pair: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "spring_bar_lug_pair v1: planar +Z/-Z host faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        face_z = center[2]

        # Footprint along X — wide enough to comfortably hold a bar of bar_d
        # plus material on both sides (inset on outer side + matching wall on
        # inner side).
        footprint_x = max(args.bar_d_mm + 2.0 * args.hole_inset_mm, 2.0)

        half_sep = args.lug_separation_mm / 2.0
        half_th = args.lug_t_mm / 2.0
        half_fx = footprint_x / 2.0

        # Build each lug ear as a box.
        # Each lug centre (Y) is at ±half_sep relative to the host face centre.
        lug_centres_y = [center[1] + half_sep, center[1] - half_sep]

        lug_solids = []
        for cy in lug_centres_y:
            # Box spans:
            #   x: [cx - half_fx, cx + half_fx]
            #   y: [cy - half_th, cy + half_th]
            #   z: face_z .. face_z + z_sign * lug_height
            cx = center[0]
            xmin, xmax = cx - half_fx, cx + half_fx
            ymin, ymax = cy - half_th, cy + half_th
            if z_sign > 0:
                zmin, zmax = face_z, face_z + args.lug_h_mm
            else:
                zmin, zmax = face_z - args.lug_h_mm, face_z
            box_mk = BRepPrimAPI_MakeBox(
                gp_Pnt(xmin, ymin, zmin), gp_Pnt(xmax, ymax, zmax),
            )
            box_mk.Build()
            if not box_mk.IsDone():
                raise RuntimeError("spring_bar_lug_pair: lug box build failed")
            lug_solids.append(box_mk.Shape())

        # Fuse both lugs into the host body.
        cur = shape
        for lug in lug_solids:
            f = BRepAlgoAPI_Fuse(cur, lug)
            f.Build()
            if not f.IsDone():
                raise RuntimeError("spring_bar_lug_pair: lug fuse failed")
            cur = f.Shape()

        # Through-hole: single cylinder along +Y, through both lugs.
        # Bar centre Z = face_z + z_sign * lug_height/2 (mid-height of lugs).
        bar_z = face_z + z_sign * (args.lug_h_mm / 2.0)
        # Bar X offset: hole_inset_mm from the outer (X-far) edge of the
        # lug — outer edge is x = cx + half_fx, so hole at:
        bar_x = center[0] + half_fx - args.hole_inset_mm

        # Hole start: y just outside +Y lug, end just outside -Y lug.
        y_start = center[1] + half_sep + half_th + 0.5
        bar_length = 2.0 * (half_sep + half_th + 0.5)
        # Cylinder axis points -Y (gp_Dir(0,-1,0)) anchored at y_start.
        ax = gp_Ax2(gp_Pnt(bar_x, y_start, bar_z), gp_Dir(0.0, -1.0, 0.0))
        cyl_mk = BRepPrimAPI_MakeCylinder(ax, args.bar_d_mm / 2.0, bar_length)
        cyl_mk.Build()
        if not cyl_mk.IsDone():
            raise RuntimeError("spring_bar_lug_pair: bar hole cylinder failed")
        bar_tool = cyl_mk.Shape()

        cut = BRepAlgoAPI_Cut(cur, bar_tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("spring_bar_lug_pair: bar hole cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":   HistoryRule.MODIFIED_INHERIT,
                "lug_solids":    HistoryRule.GENERATED_NEW,
                "spring_bar_id": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
