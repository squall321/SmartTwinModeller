"""louver — atomic. Formed louver (3-sided cut + push out at angle).

A louver is a vent feature stamped into sheet metal: three sides of a small
rectangle are cut through and the fourth side is bent so the cut strip
rotates outward at an angle, creating an air opening.

Geometry:
    - Cut a U-shaped slit through the sheet (three sides of a rectangle
      open). The fourth side remains attached and acts as the hinge axis.
    - The portion between the slit becomes the louver flap; it is rotated
      about the hinge axis by `open_angle_deg`.
    - We model the result as: (host sheet minus a small rectangular hole
      for the cut) plus a rotated flap solid. The 3-sided cut produces an
      opening on three sides of the flap footprint.

Args:
    center_x_mm, center_y_mm: center of the louver footprint.
    length_mm: length of the louver along its hinge axis (X for hinge=X).
    width_mm: width perpendicular to the hinge axis.
    hinge_axis: "X" | "Y" — direction of the hinge edge.
    hinge_side: "+X" | "-X" | "+Y" | "-Y" — direction the flap opens
        (i.e. which side of the slit becomes air). For hinge_axis=X, must
        be ±Y; for hinge_axis=Y, must be ±X.
    open_angle_deg: rotation of the flap about the hinge axis (positive
        opens the flap upward).
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_sheet.bend_edge import (
    _fuse,
    _make_box,
    _rotate_shape,
    _shape_bbox,
)


@skill(
    name="louver",
    category="modify/sheet",
    level="atomic",
    summary="Formed louver vent in a flat sheet — 3-sided cut creating a flap "
            "that pivots about the fourth side by `open_angle_deg`.",
    selector_kinds=[],
    history_rules={
        "louver_flap":   HistoryRule.GENERATED_NEW,
        "louver_window": HistoryRule.CONSUMED,
        "host_face":     HistoryRule.MODIFIED_INHERIT,
    },
    preconditions=[
        "pc.input_is_flat_sheet",
    ],
    produces_features=["louver"],
    preserves=["sheet_thickness"],
    manufacturing={
        "sheet_metal_stamp": {"min_wall_mm": 0.3,
                              "extras": {"open_angle_max_deg": 60.0}},
    },
    failure_modes=[
        "fm.louver_outside_sheet",
        "fm.hinge_side_mismatch",
        "fm.louver_angle_zero",
    ],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="volume_changed", min_delta_mm3=0.05,
                                    allow_no_change=True)],
)
class Louver(SkillBase):
    class Args(BaseModel):
        center_x_mm: float
        center_y_mm: float
        length_mm: float = Field(gt=0.0, le=200.0,
                                  description="extent along hinge axis")
        width_mm: float = Field(gt=0.0, le=200.0,
                                 description="extent perpendicular to hinge "
                                 "axis (footprint of the flap)")
        hinge_axis: Literal["X", "Y"]
        hinge_side: Literal["+X", "-X", "+Y", "-Y"]
        open_angle_deg: float = Field(default=30.0, gt=0.0, le=89.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        shape = body.wrapped if hasattr(body, "wrapped") else body
        xmin, ymin, zmin, xmax, ymax, zmax = _shape_bbox(shape)
        t = zmax - zmin
        if t <= 0:
            raise RuntimeError("louver: input body has zero z-extent")

        # Validate hinge_side perpendicular to hinge_axis.
        perp_axis = "X" if args.hinge_axis == "Y" else "Y"
        if args.hinge_side[1] != perp_axis:
            raise RuntimeError(
                f"louver: hinge_side {args.hinge_side} not perpendicular to "
                f"hinge_axis {args.hinge_axis} (expected ±{perp_axis})"
            )
        side_sign = +1 if args.hinge_side[0] == "+" else -1

        # The louver footprint is a rectangle of (length x width). It is
        # placed centered at (center_x, center_y). The hinge edge runs along
        # `hinge_axis` and is located on the *opposite* side of the footprint
        # from `hinge_side`. (Flap opens *toward* hinge_side; the hinge is
        # opposite that, so the flap rotates about the far edge.)
        if args.hinge_axis == "X":
            # length is along X; width is along Y.
            x0 = args.center_x_mm - args.length_mm / 2.0
            y0 = args.center_y_mm - args.width_mm / 2.0
            # Hinge runs along X; located at y = y0 + width (if side_sign>0
            # means flap opens toward +Y so flap is on the +Y side; hinge is
            # on the -Y side of the footprint, i.e. y = y0).
            if side_sign > 0:
                hinge_y = y0
            else:
                hinge_y = y0 + args.width_mm
        else:
            # length along Y, width along X.
            x0 = args.center_x_mm - args.width_mm / 2.0
            y0 = args.center_y_mm - args.length_mm / 2.0
            if side_sign > 0:
                hinge_x = x0
            else:
                hinge_x = x0 + args.width_mm

        # Footprint bbox extents.
        if args.hinge_axis == "X":
            foot_x0, foot_y0 = x0, y0
            foot_dx, foot_dy = args.length_mm, args.width_mm
        else:
            foot_x0, foot_y0 = x0, y0
            foot_dx, foot_dy = args.width_mm, args.length_mm

        # Validate footprint sits inside the sheet bbox.
        EPS = 1e-6
        if (foot_x0 < xmin - EPS or foot_x0 + foot_dx > xmax + EPS or
                foot_y0 < ymin - EPS or foot_y0 + foot_dy > ymax + EPS):
            raise RuntimeError(
                f"louver: footprint exceeds sheet bbox "
                f"(footprint x∈[{foot_x0:.2f}, {foot_x0+foot_dx:.2f}], "
                f"y∈[{foot_y0:.2f}, {foot_y0+foot_dy:.2f}], "
                f"sheet x∈[{xmin:.2f}, {xmax:.2f}], "
                f"y∈[{ymin:.2f}, {ymax:.2f}])"
            )

        # Carve the flap volume out of the sheet (the louver opening).
        cutter = _make_box(
            (foot_x0, foot_y0, zmin - 0.01),
            foot_dx, foot_dy, t + 0.02,
        )
        cut = BRepAlgoAPI_Cut(shape, cutter)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("louver: cut failed")
        host_after_cut = cut.Shape()

        # Build the flap body (same footprint), then rotate about the hinge.
        flap_box = _make_box(
            (foot_x0, foot_y0, zmin), foot_dx, foot_dy, t,
        )

        # Rotation axis: along hinge_axis, passing through the hinge line at
        # z = zmax (top surface — the flap pivots about its top edge so that
        # opening lifts the free side upward).
        # The hinge sits at the "far" side of the flap (opposite to
        # hinge_side). The flap's free end is on the hinge_side. We want
        # the free end to RISE (+Z), pivoting about the hinge edge at zmax.
        if args.hinge_axis == "X":
            axis_origin = (0.0, hinge_y, zmax)
            axis_dir = (1.0, 0.0, 0.0)
            # Right-hand rule about +X: positive rotation sends +Y → +Z.
            # For side_sign > 0 (free end on +Y of hinge), positive rotation
            # lifts it up. For side_sign < 0 (free end on -Y of hinge),
            # negative rotation lifts the -Y end to +Z.
            signed_angle = +side_sign * math.radians(args.open_angle_deg)
        else:
            axis_origin = (hinge_x, 0.0, zmax)
            axis_dir = (0.0, 1.0, 0.0)
            # Right-hand rule about +Y: positive rotation sends +X → -Z.
            # For side_sign > 0 (free end on +X), we need negative rotation
            # to lift the +X end to +Z.
            signed_angle = -side_sign * math.radians(args.open_angle_deg)

        flap_rotated = _rotate_shape(
            flap_box, axis_origin, axis_dir, signed_angle,
        )

        # Fuse the rotated flap back onto the host. The hinge edge is still
        # shared with the sheet, so the result is a single connected solid.
        fused = _fuse(host_after_cut, flap_rotated)

        history = EntityHistoryMap(
            rules={
                "louver_flap":   HistoryRule.GENERATED_NEW,
                "louver_window": HistoryRule.CONSUMED,
                "host_face":     HistoryRule.MODIFIED_INHERIT,
            },
        )
        return SkillResult(body=Part(fused), history=history)
