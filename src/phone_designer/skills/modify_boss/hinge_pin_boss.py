"""hinge_pin_boss — atomic. Cylindrical boss with through-hole + N alignment notches.

A hinge_pin boss is a thick cylindrical post pierced by an axial through-hole
(for the pin) with `notch_count` rectangular notches cut around the outer
surface (acting as anti-rotation / detent features for the mating part).

Geometry (raw OCCT):
    outer    = MakeCylinder(r=pin_d/2 + wall, h=height)
    through  = MakeCylinder(r=pin_d/2, h=height + overshoot)  # axial through-hole
    notch_i  = MakeBox at angle 2π·i/notch_count, radial slot of width=notch_w
               x depth=notch_depth into outer ring
    boss     = (outer - through) - union(notches)
    result   = body ∪ boss

v1 supports planar +Z/-Z target faces.
"""
from __future__ import annotations

import math
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
    name="hinge_pin_boss",
    category="modify/boss",
    level="atomic",
    summary="Cylindrical hinge-pin boss with axial through-hole and N radial "
            "alignment notches around the perimeter (anti-rotation features). "
            "v1 supports planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":        HistoryRule.MODIFIED_INHERIT,
        "hinge_pin_solid":    HistoryRule.GENERATED_NEW,
        "hinge_pin_through":  HistoryRule.GENERATED_NEW,
        "alignment_notches":  HistoryRule.GENERATED_NEW,
    },
    produces_features=["hinge_pin_boss", "through_hole", "alignment_notches"],
    preserves=["outer_envelope_outside_boss"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"notch_min_width_mm": 0.5}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "cnc_3axis":         {"min_wall_mm": 0.5},
    },
    failure_modes=[
        "fm.notch_consumes_wall",
        "fm.pin_diameter_geq_outer",
        "fm.notch_count_zero_allowed",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class HingePinBoss(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of boss center",
        )
        pin_diameter_mm: float = Field(gt=0, le=20,
                                        description="axial through-hole diameter")
        height_mm: float = Field(gt=0, le=30,
                                  description="boss height along face normal")
        notch_count: int = Field(default=4, ge=0, le=24,
                                  description="number of radial alignment notches "
                                              "(0 disables notch cut)")
        wall_thickness_mm: float = Field(default=1.5, gt=0, le=10,
                                          description="ring wall thickness "
                                                      "(outer_r = pin_r + wall)")
        notch_width_mm: float = Field(default=1.0, gt=0, le=5,
                                       description="circumferential width of each notch")
        notch_depth_mm: float = Field(default=0.5, gt=0, le=5,
                                       description="radial depth of each notch into the wall")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf

        if args.notch_depth_mm >= args.wall_thickness_mm:
            raise ValueError(
                f"hinge_pin_boss: notch_depth_mm ({args.notch_depth_mm}) >= "
                f"wall_thickness_mm ({args.wall_thickness_mm}) — notch breaks "
                f"through to pin hole"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"hinge_pin_boss: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "hinge_pin_boss v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        axis_dir = gp_Dir(0.0, 0.0, z_sign)
        wx = center[0] + args.position[0]
        wy = center[1] + args.position[1]
        base_z = center[2]

        pin_r = args.pin_diameter_mm / 2.0
        outer_r = pin_r + args.wall_thickness_mm

        # outer cylinder
        outer_ax = gp_Ax2(gp_Pnt(wx, wy, base_z), axis_dir)
        outer_mk = BRepPrimAPI_MakeCylinder(outer_ax, outer_r, args.height_mm)
        outer_mk.Build()
        if not outer_mk.IsDone():
            raise RuntimeError("hinge_pin_boss: outer cylinder build failed")
        outer_shape = outer_mk.Shape()

        # through-hole cylinder (slightly longer)
        overshoot = 1.0
        through_base = gp_Pnt(wx, wy, base_z - z_sign * (overshoot / 2.0))
        through_ax = gp_Ax2(through_base, axis_dir)
        through_mk = BRepPrimAPI_MakeCylinder(through_ax, pin_r, args.height_mm + overshoot)
        through_mk.Build()
        if not through_mk.IsDone():
            raise RuntimeError("hinge_pin_boss: through-hole cylinder build failed")
        through_shape = through_mk.Shape()

        # outer - through
        cut1 = BRepAlgoAPI_Cut(outer_shape, through_shape)
        cut1.Build()
        if not cut1.IsDone():
            raise RuntimeError("hinge_pin_boss: outer - through cut failed")
        ring = cut1.Shape()

        # notches: build at +X side as a small box that intersects the ring at
        # radius (outer_r - notch_depth_mm/2 + notch_depth_mm/2) = outer_r, then
        # rotate around boss axis. The notch is a box of (notch_depth × notch_width
        # × height) sitting tangent to the outer cylinder, oriented so that
        # "depth" runs radially inward.
        if args.notch_count > 0:
            half_h = args.height_mm  # full height, ends are clamped by ring
            nd = args.notch_depth_mm
            nw = args.notch_width_mm

            # Build a single notch template at angle 0 (along +X axis), centered
            # at boss center in world:
            # box spans X = [outer_r - nd, outer_r + 0.1]  (slight outside overshoot
            #               so the cut starts cleanly at the outer surface),
            # Y = [-nw/2, +nw/2], Z = [base_z, base_z + z_sign * height].
            x_in = wx + (outer_r - nd)
            x_out = wx + (outer_r + 0.1)
            y_lo = wy - nw / 2.0
            y_hi = wy + nw / 2.0
            z_lo = min(base_z, base_z + z_sign * half_h)
            z_hi = max(base_z, base_z + z_sign * half_h)

            notch_union = None
            for i in range(args.notch_count):
                theta = 2.0 * math.pi * i / args.notch_count
                box_mk = BRepPrimAPI_MakeBox(
                    gp_Pnt(x_in, y_lo, z_lo),
                    gp_Pnt(x_out, y_hi, z_hi),
                )
                box_mk.Build()
                if not box_mk.IsDone():
                    raise RuntimeError(
                        f"hinge_pin_boss: notch {i} box build failed"
                    )
                notch_shape = box_mk.Shape()
                if abs(theta) > 1e-9:
                    # Rotate around boss-axis (z axis through wx, wy).
                    rot_axis = gp_Ax1(gp_Pnt(wx, wy, base_z), gp_Dir(0, 0, 1))
                    tr = gp_Trsf()
                    tr.SetRotation(rot_axis, theta)
                    xf = BRepBuilderAPI_Transform(notch_shape, tr, True)
                    xf.Build()
                    if not xf.IsDone():
                        raise RuntimeError(
                            f"hinge_pin_boss: notch {i} rotation failed"
                        )
                    notch_shape = xf.Shape()

                if notch_union is None:
                    notch_union = notch_shape
                else:
                    fu = BRepAlgoAPI_Fuse(notch_union, notch_shape)
                    fu.Build()
                    if not fu.IsDone():
                        raise RuntimeError(
                            f"hinge_pin_boss: notch union {i} failed"
                        )
                    notch_union = fu.Shape()

            # ring - notches
            cut2 = BRepAlgoAPI_Cut(ring, notch_union)
            cut2.Build()
            if not cut2.IsDone():
                raise RuntimeError("hinge_pin_boss: ring - notches cut failed")
            boss_shape = cut2.Shape()
        else:
            boss_shape = ring

        # body + boss
        fuse = BRepAlgoAPI_Fuse(shape, boss_shape)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("hinge_pin_boss: body fuse failed")
        new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":       HistoryRule.MODIFIED_INHERIT,
                "hinge_pin_solid":   HistoryRule.GENERATED_NEW,
                "hinge_pin_through": HistoryRule.GENERATED_NEW,
                "alignment_notches": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
