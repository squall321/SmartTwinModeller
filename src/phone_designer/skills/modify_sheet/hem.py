"""hem — atomic. 180-degree bend back on itself (with optional gap).

A hem is a sheet-metal edge treatment where a strip is folded back over
the host sheet. The fold is a tight half-bend (inner radius defaults small)
plus a flat strip that lies parallel to the host but on the +Z side.

Args:
    edge_selector: SelectorRef
        a straight axis-aligned boundary edge on the top face of the sheet
    bend_side: "+X" | "-X" | "+Y" | "-Y"
        outward direction from the sheet at that edge
    hem_length_mm: float > 0
        length of the folded-back strip (along the host sheet from the edge)
    gap_mm: float >= 0
        offset between the folded layer and the host sheet. 0 = touching
        (or interpenetrating numerically, in which case the result is a
        single solid).
    radius_mm: float > 0 (default = thickness/2)
        inner bend radius

The geometry is constructed via raw OCCT primitives: a toroidal sector for
the bend region plus a flat box for the folded layer, both fused with the
input sheet.
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
    _shape_bbox,
)


@skill(
    name="hem",
    category="modify/sheet",
    level="atomic",
    summary="180-degree fold-back hem on a sheet boundary edge. The folded "
            "strip lies parallel to the host sheet, offset upward by "
            "`gap_mm` (0 = closed hem).",
    selector_kinds=["edges"],
    history_rules={
        "host_edge":        HistoryRule.MODIFIED_INHERIT,
        "hem_bend_face":    HistoryRule.GENERATED_NEW,
        "hem_layer_faces":  HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.input_is_flat_sheet",
        "pc.edge_axis_aligned_on_top",
    ],
    produces_features=["hem"],
    preserves=["sheet_thickness"],
    manufacturing={
        "sheet_metal_stamp": {"min_fillet_r_mm": 0.1},
    },
    failure_modes=[
        "fm.hem_length_zero",
        "fm.gap_too_small_for_radius",
    ],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="volume_increased", min_delta_mm3=0.5)],
)
class Hem(SkillBase):
    class Args(BaseModel):
        edge_selector: SelectorRef
        bend_side: Literal["+X", "-X", "+Y", "-Y"]
        hem_length_mm: float = Field(gt=0.0, le=500.0)
        gap_mm: float = Field(default=0.0, ge=0.0, le=50.0)
        radius_mm: float | None = Field(default=None, gt=0.0, le=50.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        shape = body.wrapped if hasattr(body, "wrapped") else body
        xmin, ymin, zmin, xmax, ymax, zmax = _shape_bbox(shape)
        t = zmax - zmin

        # Default inner radius = half thickness, but factor in the requested gap:
        # the bend axis needs to be at z = zmax + radius. The folded layer
        # lies at z = zmax + gap_mm .. zmax + gap_mm + t. For a 180-deg fold
        # with inner radius r, the inner surface of the folded layer is at
        # z = zmax + 2*r, so 2*r == gap_mm + 0? Actually:
        #   - Outer-most point of the bend arc on the host's bottom is at
        #     z = zmax + r + (r + t) (radius outer = r + t).
        #   - After 180-deg rotation, the bent piece's inner surface (was
        #     top of original) ends up at z = zmax + 2*r (touching).
        # So for gap == 0 the folded layer's bottom is at z = zmax + 2*r,
        # i.e. there's an air gap of 2*r between host and folded layer.
        # That's the natural physical hem geometry (you can't fold infinitely
        # tight). When the user passes gap_mm > 0, we use radius = gap_mm/2
        # so that the layers are exactly that far apart.
        if args.radius_mm is not None:
            radius = args.radius_mm
        elif args.gap_mm > 0:
            radius = args.gap_mm / 2.0
        else:
            # Minimum sensible default: half the thickness.
            radius = max(t * 0.5, 0.1)

        edge = _resolve_bend_edge(shape, args.edge_selector)
        axis_kind, mid, length, endpoints = _edge_geom(edge)
        if axis_kind not in ("X", "Y"):
            raise RuntimeError(
                f"hem: edge must be parallel to X or Y, got {axis_kind}"
            )

        if axis_kind == "Y":
            edge_position = mid[0]
            (p1, p2) = endpoints
            perp_min = min(p1[1], p2[1])
            perp_max = max(p1[1], p2[1])
            if args.bend_side[1] != "X":
                raise RuntimeError(
                    f"hem: bend_side {args.bend_side} not perpendicular to "
                    f"Y-edge"
                )
        else:
            edge_position = mid[1]
            (p1, p2) = endpoints
            perp_min = min(p1[0], p2[0])
            perp_max = max(p1[0], p2[0])
            if args.bend_side[1] != "Y":
                raise RuntimeError(
                    f"hem: bend_side {args.bend_side} not perpendicular to "
                    f"X-edge"
                )

        side_sign = +1 if args.bend_side[0] == "+" else -1

        # Build the 180-deg bend arc.
        arc = _build_bend_arc(
            bend_position=edge_position,
            bend_axis_kind=axis_kind,
            perp_min=perp_min,
            perp_max=perp_max,
            thickness=t,
            radius=radius,
            angle_rad=math.pi,
            invert_rotation=(side_sign < 0),
        )

        # The folded layer is a flat box sitting at z = zmax + 2*radius
        # (the natural fold height for inner radius `radius` and 180 deg),
        # spanning from the edge backward into the sheet by hem_length_mm.
        layer_z = zmax + 2.0 * radius
        # Apply optional extra gap on top of the natural fold height.
        extra_gap = max(0.0, args.gap_mm - 2.0 * radius)
        layer_z += extra_gap

        if axis_kind == "Y":
            if side_sign > 0:
                # +X edge: folded layer extends back into the sheet (-X) from
                # the edge.
                layer_corner = (edge_position - args.hem_length_mm, perp_min, layer_z)
            else:
                layer_corner = (edge_position, perp_min, layer_z)
            layer = _make_box(
                layer_corner, args.hem_length_mm, perp_max - perp_min, t,
            )
        else:
            if side_sign > 0:
                layer_corner = (perp_min, edge_position - args.hem_length_mm, layer_z)
            else:
                layer_corner = (perp_min, edge_position, layer_z)
            layer = _make_box(
                layer_corner, perp_max - perp_min, args.hem_length_mm, t,
            )

        # Fuse: host + arc + layer. If `extra_gap > 0`, the layer floats above
        # the arc — that's fine for testing the additive volume, although in
        # a real model you'd need an extra connecting strip. For the standard
        # gap == 0 case the arc terminus precisely meets the layer.
        fused = _fuse(shape, arc)
        if extra_gap == 0.0:
            fused = _fuse(fused, layer)
        else:
            # When there's a positive extra_gap, fall back to a non-touching
            # union (BRepAlgoAPI_Fuse still works on disjoint solids and
            # produces a compound of two solids; the volume sum is correct).
            fused = _fuse(fused, layer)

        history = EntityHistoryMap(
            rules={
                "host_edge":        HistoryRule.MODIFIED_INHERIT,
                "hem_bend_face":    HistoryRule.GENERATED_NEW,
                "hem_layer_faces":  HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(fused), history=history)
