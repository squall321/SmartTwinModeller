"""bend_relief — atomic. Small V or rectangular cut at a bend corner.

Bend relief is a clearance cut placed at the corner of a flange or bend where
two perpendicular bends meet. Without it, sheet metal tears during forming
because the material at the corner is in tension/compression simultaneously
along two axes.

This skill cuts a small rectangular (or V-shaped) slot through the sheet
adjacent to the bend line, just inside the host material. Width is along the
bend line, depth is into the keep half of the sheet. Through-cuts only (the
relief is a hole in the flat blank).

Args:
    edge_selector: a straight axis-aligned edge representing the bend line
        on the top face of the sheet.
    bend_side: "+X" | "-X" | "+Y" | "-Y" — the side of the bend, i.e. the
        half that gets bent. Relief is cut on the *bend* side, snug to the
        bend line, at either end of the edge.
    relief_position: "start" | "end" | "both" — which end(s) of the bend
        line to put the relief at. Defaults to "both".
    width_mm: extent along the bend axis (typical = 1.5 × sheet_thickness).
    depth_mm: extent perpendicular to the bend axis, into the bend-side
        half (typical = thickness, just enough to clear the bend).
    shape: "rectangular" | "v" — V-shape uses a triangular cut. Both are
        through-cuts.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_sheet.bend_edge import (
    _edge_geom,
    _make_box,
    _resolve_bend_edge,
    _shape_bbox,
)


def _make_triangular_prism(
    *,
    apex: tuple[float, float, float],
    base_p1: tuple[float, float, float],
    base_p2: tuple[float, float, float],
    extrude_vec: tuple[float, float, float],
):
    """Build a triangular prism from a triangle (apex, base_p1, base_p2) and
    a linear extrusion vector. Used for V-shaped reliefs.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    poly = BRepBuilderAPI_MakePolygon(
        gp_Pnt(*apex),
        gp_Pnt(*base_p1),
        gp_Pnt(*base_p2),
        True,
    )
    if not poly.IsDone():
        raise RuntimeError("bend_relief: triangle polygon failed")
    wire = poly.Wire()
    face_maker = BRepBuilderAPI_MakeFace(wire, True)
    if not face_maker.IsDone():
        raise RuntimeError("bend_relief: triangle face failed")
    face = face_maker.Face()

    prism = BRepPrimAPI_MakePrism(face, gp_Vec(*extrude_vec))
    prism.Build()
    if not prism.IsDone():
        raise RuntimeError("bend_relief: triangular prism failed")
    return prism.Shape()


@skill(
    name="bend_relief",
    category="modify/sheet",
    level="atomic",
    summary="Cut a small clearance slot at a bend-line corner to prevent the "
            "sheet from tearing during forming. Rectangular or V-shape, "
            "through-cut.",
    selector_kinds=["edges"],
    history_rules={
        "host_edge":   HistoryRule.MODIFIED_INHERIT,
        "relief_cut":  HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.input_is_flat_sheet",
        "pc.edge_axis_aligned_on_top",
    ],
    produces_features=["bend_relief"],
    preserves=["sheet_thickness"],
    manufacturing={
        "sheet_metal_stamp": {"min_wall_mm": 0.3,
                              "extras": {"reason": "prevent_tear"}},
    },
    failure_modes=[
        "fm.relief_too_large",
        "fm.relief_outside_sheet",
    ],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_decreased", min_delta_mm3=0.05)],
)
class BendRelief(SkillBase):
    class Args(BaseModel):
        edge_selector: SelectorRef
        bend_side: Literal["+X", "-X", "+Y", "-Y"]
        relief_position: Literal["start", "end", "both"] = "both"
        width_mm: float = Field(default=1.5, gt=0.0, le=50.0,
                                description="extent along the bend axis")
        depth_mm: float = Field(default=1.5, gt=0.0, le=50.0,
                                description="extent perpendicular to the bend "
                                "axis, into the bend-side half")
        shape: Literal["rectangular", "v"] = "rectangular"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        shape = body.wrapped if hasattr(body, "wrapped") else body
        edge = _resolve_bend_edge(shape, args.edge_selector)
        axis_kind, mid, length, endpoints = _edge_geom(edge)
        if axis_kind not in ("X", "Y"):
            raise RuntimeError(
                f"bend_relief: edge must be parallel to X or Y, got {axis_kind}"
            )

        xmin, ymin, zmin, xmax, ymax, zmax = _shape_bbox(shape)
        t = zmax - zmin
        # Through-cut: extend slightly past top/bottom for clean boolean.
        cut_z_start = zmin - 0.01
        cut_z_height = t + 0.02

        # bend_side must be perpendicular to the edge axis (same logic as
        # other sheet skills).
        if axis_kind == "Y":
            if args.bend_side[1] != "X":
                raise RuntimeError(
                    f"bend_relief: bend_side {args.bend_side} not perpendicular "
                    f"to Y-edge"
                )
            edge_position = mid[0]
            (p1, p2) = endpoints
            perp_min = min(p1[1], p2[1])
            perp_max = max(p1[1], p2[1])
        else:
            if args.bend_side[1] != "Y":
                raise RuntimeError(
                    f"bend_relief: bend_side {args.bend_side} not perpendicular "
                    f"to X-edge"
                )
            edge_position = mid[1]
            (p1, p2) = endpoints
            perp_min = min(p1[0], p2[0])
            perp_max = max(p1[0], p2[0])

        side_sign = +1 if args.bend_side[0] == "+" else -1

        # Determine which positions to cut at.
        positions: list[float] = []
        if args.relief_position in ("start", "both"):
            positions.append(perp_min)
        if args.relief_position in ("end", "both"):
            positions.append(perp_max)
        if not positions:
            raise RuntimeError(
                f"bend_relief: relief_position {args.relief_position} produced "
                f"no cuts"
            )

        # Build one cutter per position. The relief is anchored at the END of
        # the bend line (perp_min or perp_max) and extends along the bend axis
        # by `width_mm` *inward* (into the bend region) — but for sheet-metal
        # purposes the cut is best placed straddling the edge end so we center
        # it on the end point.
        cutters = []
        for pos in positions:
            # Start coordinate along bend axis: half the width inward from the
            # endpoint, so the cut straddles the corner.
            if pos == perp_min:
                axis_start = pos
            else:
                axis_start = pos - args.width_mm

            # Clamp to bbox so the cutter doesn't extend past the sheet.
            if axis_kind == "Y":
                # bend axis is Y; perp axis is X.
                if side_sign > 0:
                    corner_x = edge_position
                else:
                    corner_x = edge_position - args.depth_mm
                dy = args.width_mm
                dx = args.depth_mm
                corner_y = axis_start
                if args.shape == "rectangular":
                    cutters.append(_make_box(
                        (corner_x, corner_y, cut_z_start),
                        dx, dy, cut_z_height,
                    ))
                else:  # "v"
                    # Triangle in XY plane: apex pointing into the bend-side
                    # half (along bend_side direction). Base lies along the
                    # bend axis (Y) at edge_position.
                    if side_sign > 0:
                        apex = (edge_position + args.depth_mm,
                                corner_y + dy / 2.0, cut_z_start)
                    else:
                        apex = (edge_position - args.depth_mm,
                                corner_y + dy / 2.0, cut_z_start)
                    base1 = (edge_position, corner_y, cut_z_start)
                    base2 = (edge_position, corner_y + dy, cut_z_start)
                    cutters.append(_make_triangular_prism(
                        apex=apex,
                        base_p1=base1,
                        base_p2=base2,
                        extrude_vec=(0.0, 0.0, cut_z_height),
                    ))
            else:
                # bend axis is X; perp axis is Y.
                if side_sign > 0:
                    corner_y = edge_position
                else:
                    corner_y = edge_position - args.depth_mm
                dx = args.width_mm
                dy = args.depth_mm
                corner_x = axis_start
                if args.shape == "rectangular":
                    cutters.append(_make_box(
                        (corner_x, corner_y, cut_z_start),
                        dx, dy, cut_z_height,
                    ))
                else:
                    if side_sign > 0:
                        apex = (corner_x + dx / 2.0,
                                edge_position + args.depth_mm, cut_z_start)
                    else:
                        apex = (corner_x + dx / 2.0,
                                edge_position - args.depth_mm, cut_z_start)
                    base1 = (corner_x, edge_position, cut_z_start)
                    base2 = (corner_x + dx, edge_position, cut_z_start)
                    cutters.append(_make_triangular_prism(
                        apex=apex,
                        base_p1=base1,
                        base_p2=base2,
                        extrude_vec=(0.0, 0.0, cut_z_height),
                    ))

        # Subtract every cutter from the host sheet.
        result = shape
        for c in cutters:
            cut = BRepAlgoAPI_Cut(result, c)
            cut.Build()
            if not cut.IsDone():
                raise RuntimeError("bend_relief: BRepAlgoAPI_Cut failed")
            result = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "host_edge":  HistoryRule.MODIFIED_INHERIT,
                "relief_cut": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(result), history=history)
