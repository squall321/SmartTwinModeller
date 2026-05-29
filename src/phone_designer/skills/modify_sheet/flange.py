"""flange — macro. Bend + flat extension off a sheet edge.

Adds a new strip of sheet metal joined to the original sheet via a round
bend at the selected boundary edge. The flange consists of:
    1. a toroidal sector (the bend, inner radius `radius_mm`) along the edge
    2. a flat rectangular extension of length `flange_length_mm` continuing
       the bend tangent direction.

Both pieces are fused with the input sheet to form a single solid.

The edge selector should resolve to a straight, axis-aligned boundary edge
on the top face of the sheet (e.g. the +X edge). `bend_side` indicates the
outward direction relative to the sheet body (e.g. "+X" for the +X boundary
edge). Positive `angle_deg` lifts the flange upward (+Z component).
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
from phone_designer.skills.modify_sheet.bend_edge import (
    _build_bend_arc,
    _edge_geom,
    _fuse,
    _make_box,
    _resolve_bend_edge,
    _rotate_shape,
    _shape_bbox,
)


def _build_flange_piece(
    *,
    shape,
    axis_kind: Literal["X", "Y"],
    edge_position: float,
    perp_min: float,
    perp_max: float,
    bend_side: Literal["+X", "-X", "+Y", "-Y"],
    angle_deg: float,
    radius_mm: float,
    flange_length_mm: float,
):
    """Return (arc_shape, flat_extension_shape, signed_rotation_angle) for a
    flange piece attached at `edge_position` on the perpendicular axis.

    All three are positioned in world space ready to be fused with the input
    sheet.
    """
    xmin, ymin, zmin, xmax, ymax, zmax = _shape_bbox(shape)
    t = zmax - zmin

    side_sign = +1 if bend_side[0] == "+" else -1
    angle_rad = math.radians(abs(angle_deg))

    # The arc is identical to bend_edge's arc: a toroidal sector at the edge
    # line, revolving from the existing sheet plane (z=0..t) about the bend
    # axis at (edge_position on perp axis, *, t + radius).
    invert = (side_sign < 0)
    if angle_deg < 0:
        invert = not invert

    arc = _build_bend_arc(
        bend_position=edge_position,
        bend_axis_kind=axis_kind,
        perp_min=perp_min,
        perp_max=perp_max,
        thickness=t,
        radius=radius_mm,
        angle_rad=angle_rad,
        invert_rotation=invert,
    )

    # Flat extension: a box of size (flange_length_mm x edge_length x t)
    # placed immediately outward of the edge in the perpendicular direction,
    # then rotated about the bend axis by the bend angle so it continues the
    # arc tangent.
    if axis_kind == "Y":
        # perpendicular axis is X. Extension extends in +X (or -X) from the
        # edge. Build it abutting the edge at x = edge_position so that the
        # rotation about the bend axis at (edge_position, *, t+r) sweeps it
        # into the tangent direction.
        if side_sign > 0:
            corner = (edge_position, perp_min, zmin)
            dx = flange_length_mm
        else:
            corner = (edge_position - flange_length_mm, perp_min, zmin)
            dx = flange_length_mm
        dy = perp_max - perp_min
        dz = t
        flat = _make_box(corner, dx, dy, dz)
        axis_origin = (edge_position, 0.0, zmin + t + radius_mm)
        axis_dir = (0.0, 1.0, 0.0)
    else:  # axis_kind == "X"
        if side_sign > 0:
            corner = (perp_min, edge_position, zmin)
            dy = flange_length_mm
        else:
            corner = (perp_min, edge_position - flange_length_mm, zmin)
            dy = flange_length_mm
        dx = perp_max - perp_min
        dz = t
        flat = _make_box(corner, dx, dy, dz)
        axis_origin = (0.0, edge_position, zmin + t + radius_mm)
        axis_dir = (1.0, 0.0, 0.0)

    # Signed rotation — same convention as bend_edge._do_bend: rotating the
    # outward-facing flat piece up by angle_deg means a negative rotation
    # about +Y for bend_side = "+X", positive about +X for bend_side = "+Y".
    signed_angle = -side_sign * math.radians(angle_deg)
    if axis_kind == "X":
        signed_angle = -signed_angle

    flat_rotated = _rotate_shape(flat, axis_origin, axis_dir, signed_angle)
    return arc, flat_rotated


@skill(
    name="flange",
    category="modify/sheet",
    level="macro",
    summary="Add a bent flat extension (flange) at a boundary edge of a sheet. "
            "Composed of a round bend (inner radius `radius_mm`) plus a flat "
            "extension of length `flange_length_mm`.",
    selector_kinds=["edges"],
    history_rules={
        "host_edge":         HistoryRule.MODIFIED_INHERIT,
        "flange_bend_face":  HistoryRule.GENERATED_NEW,
        "flange_flat_faces": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.input_is_flat_sheet",
        "pc.edge_axis_aligned_on_top",
    ],
    produces_features=["flange"],
    preserves=["sheet_thickness"],
    manufacturing={
        "sheet_metal_stamp": {"min_fillet_r_mm": "args.radius_mm"},
    },
    failure_modes=[
        "fm.flange_length_zero",
        "fm.edge_not_axis_aligned",
    ],
    cost_hint=0.4,
    expansion=["bend_edge", "extrude_through"],
    post_conditions=[PostCondition(kind="volume_increased", min_delta_mm3=0.5)],
)
class Flange(SkillBase):
    class Args(BaseModel):
        edge_selector: SelectorRef
        angle_deg: float = Field(ge=-180.0, le=180.0)
        radius_mm: float = Field(gt=0.0, le=200.0)
        bend_side: Literal["+X", "-X", "+Y", "-Y"]
        flange_length_mm: float = Field(gt=0.0, le=500.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        shape = body.wrapped if hasattr(body, "wrapped") else body
        edge = _resolve_bend_edge(shape, args.edge_selector)
        axis_kind, mid, length, endpoints = _edge_geom(edge)
        if axis_kind not in ("X", "Y"):
            raise RuntimeError(
                f"flange: edge must be parallel to X or Y, got {axis_kind}"
            )

        if axis_kind == "Y":
            edge_position = mid[0]
            (p1, p2) = endpoints
            perp_min = min(p1[1], p2[1])
            perp_max = max(p1[1], p2[1])
            if args.bend_side[1] != "X":
                raise RuntimeError(
                    f"flange: bend_side {args.bend_side} not perpendicular to "
                    f"Y-edge (expected ±X)"
                )
        else:
            edge_position = mid[1]
            (p1, p2) = endpoints
            perp_min = min(p1[0], p2[0])
            perp_max = max(p1[0], p2[0])
            if args.bend_side[1] != "Y":
                raise RuntimeError(
                    f"flange: bend_side {args.bend_side} not perpendicular to "
                    f"X-edge (expected ±Y)"
                )

        arc, flat = _build_flange_piece(
            shape=shape,
            axis_kind=axis_kind,
            edge_position=edge_position,
            perp_min=perp_min,
            perp_max=perp_max,
            bend_side=args.bend_side,
            angle_deg=args.angle_deg,
            radius_mm=args.radius_mm,
            flange_length_mm=args.flange_length_mm,
        )

        fused = _fuse(shape, arc)
        fused = _fuse(fused, flat)

        history = EntityHistoryMap(
            rules={
                "host_edge":         HistoryRule.MODIFIED_INHERIT,
                "flange_bend_face":  HistoryRule.GENERATED_NEW,
                "flange_flat_faces": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(fused), history=history)
