"""tab / slot — atomic. Small protrusion / matching pocket on a sheet edge.

Two skills in one module (sheet-metal companion features):

    Tab  — extrude a small rectangular protrusion outward from a boundary
           edge of a sheet. Used for assembly registration features that
           insert into a matching slot in another sheet.
    Slot — cut a small rectangular pocket through (or partially through)
           the sheet at a specified position. Sized to receive a Tab.

Both accept the same dimensional args (tab_length / tab_width / tab_thickness).
The slot's thickness defaults to a through-cut.
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
    _fuse,
    _make_box,
    _resolve_bend_edge,
    _shape_bbox,
)


def _build_tab_box(
    *,
    shape,
    axis_kind: Literal["X", "Y"],
    edge_position: float,
    edge_perp_min: float,
    edge_perp_max: float,
    tab_side: Literal["+X", "-X", "+Y", "-Y"],
    tab_length_mm: float,
    tab_width_mm: float,
    tab_thickness_mm: float | None,
    tab_position_along_edge: float | None,
):
    """Build a single tab box positioned outward from the chosen edge.

    The tab is a rectangular solid of size
        (tab_length_mm in perpendicular direction)
      x (tab_width_mm in edge direction)
      x (tab_thickness_mm in z direction; defaults to sheet thickness).

    `tab_position_along_edge` is the coordinate along the edge axis at the
    *center* of the tab. Defaults to the edge midpoint.
    """
    xmin, ymin, zmin, xmax, ymax, zmax = _shape_bbox(shape)
    t = tab_thickness_mm if tab_thickness_mm is not None else (zmax - zmin)
    if t <= 0:
        raise RuntimeError("tab_slot: degenerate thickness")

    if axis_kind == "Y":
        if tab_side[1] != "X":
            raise RuntimeError(
                f"tab_slot: tab_side {tab_side} not perpendicular to Y-edge"
            )
    else:
        if tab_side[1] != "Y":
            raise RuntimeError(
                f"tab_slot: tab_side {tab_side} not perpendicular to X-edge"
            )

    side_sign = +1 if tab_side[0] == "+" else -1
    # Center along the edge (default: edge midpoint).
    center = (
        tab_position_along_edge
        if tab_position_along_edge is not None
        else (edge_perp_min + edge_perp_max) / 2.0
    )

    if axis_kind == "Y":
        # perpendicular axis = X.
        if side_sign > 0:
            corner_x = edge_position
        else:
            corner_x = edge_position - tab_length_mm
        corner_y = center - tab_width_mm / 2.0
        corner_z = zmin  # bottom of sheet
        return _make_box(
            (corner_x, corner_y, corner_z),
            tab_length_mm, tab_width_mm, t,
        )
    else:  # axis_kind == "X"
        if side_sign > 0:
            corner_y = edge_position
        else:
            corner_y = edge_position - tab_length_mm
        corner_x = center - tab_width_mm / 2.0
        corner_z = zmin
        return _make_box(
            (corner_x, corner_y, corner_z),
            tab_width_mm, tab_length_mm, t,
        )


@skill(
    name="tab",
    category="modify/sheet",
    level="atomic",
    summary="Add a small rectangular tab protrusion outward from a boundary "
            "edge of a sheet. The tab is a box of length x width x thickness "
            "abutting the edge, fused onto the host sheet.",
    selector_kinds=["edges"],
    history_rules={
        "host_edge":     HistoryRule.MODIFIED_INHERIT,
        "tab_faces":     HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.input_is_flat_sheet",
        "pc.edge_axis_aligned",
    ],
    produces_features=["tab"],
    preserves=["sheet_thickness"],
    manufacturing={
        "sheet_metal_stamp": {"min_wall_mm": 0.3},
    },
    failure_modes=[
        "fm.tab_width_exceeds_edge_length",
        "fm.tab_side_mismatch",
    ],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_increased", min_delta_mm3=0.5)],
)
class Tab(SkillBase):
    class Args(BaseModel):
        edge_selector: SelectorRef
        tab_side: Literal["+X", "-X", "+Y", "-Y"]
        tab_length_mm: float = Field(gt=0.0, le=200.0,
                                      description="extent outward from the edge")
        tab_width_mm: float = Field(gt=0.0, le=500.0,
                                      description="extent along the edge")
        tab_thickness_mm: float | None = Field(default=None, gt=0.0, le=50.0,
                                                description="z extent; "
                                                "defaults to sheet thickness")
        tab_position_along_edge_mm: float | None = Field(default=None,
                                                          description="center "
                                                          "of tab along edge "
                                                          "axis; default = "
                                                          "edge midpoint")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        shape = body.wrapped if hasattr(body, "wrapped") else body
        edge = _resolve_bend_edge(shape, args.edge_selector)
        axis_kind, mid, length, endpoints = _edge_geom(edge)
        if axis_kind not in ("X", "Y"):
            raise RuntimeError(
                f"tab: edge must be parallel to X or Y, got {axis_kind}"
            )

        if axis_kind == "Y":
            edge_position = mid[0]
            (p1, p2) = endpoints
            perp_min = min(p1[1], p2[1])
            perp_max = max(p1[1], p2[1])
        else:
            edge_position = mid[1]
            (p1, p2) = endpoints
            perp_min = min(p1[0], p2[0])
            perp_max = max(p1[0], p2[0])

        tab_solid = _build_tab_box(
            shape=shape,
            axis_kind=axis_kind,
            edge_position=edge_position,
            edge_perp_min=perp_min,
            edge_perp_max=perp_max,
            tab_side=args.tab_side,
            tab_length_mm=args.tab_length_mm,
            tab_width_mm=args.tab_width_mm,
            tab_thickness_mm=args.tab_thickness_mm,
            tab_position_along_edge=args.tab_position_along_edge_mm,
        )

        fused = _fuse(shape, tab_solid)

        history = EntityHistoryMap(
            rules={
                "host_edge": HistoryRule.MODIFIED_INHERIT,
                "tab_faces": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(fused), history=history)


@skill(
    name="slot",
    category="modify/sheet",
    level="atomic",
    summary="Cut a small rectangular slot pocket into a sheet. Matches a "
            "Tab made with the same length/width. Defaults to through-cut.",
    selector_kinds=["edges"],
    history_rules={
        "slot_walls":  HistoryRule.GENERATED_NEW,
        "consumed":    HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.input_is_flat_sheet",
        "pc.edge_axis_aligned",
    ],
    produces_features=["slot"],
    preserves=["outer_envelope"],
    manufacturing={
        "sheet_metal_stamp": {"min_wall_mm": 0.3},
    },
    failure_modes=[
        "fm.slot_outside_sheet",
    ],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_decreased", min_delta_mm3=0.5)],
)
class Slot(SkillBase):
    class Args(BaseModel):
        edge_selector: SelectorRef
        slot_inset_side: Literal["+X", "-X", "+Y", "-Y"] = Field(
            description="direction *into* the sheet from the chosen edge "
            "where the slot will be cut",
        )
        slot_length_mm: float = Field(gt=0.0, le=200.0,
                                       description="inset depth from the edge")
        slot_width_mm: float = Field(gt=0.0, le=500.0,
                                       description="extent along the edge")
        slot_thickness_mm: float | None = Field(default=None, gt=0.0, le=200.0,
                                                  description="z depth; "
                                                  "default = through-cut")
        slot_position_along_edge_mm: float | None = Field(default=None)
        offset_from_edge_mm: float = Field(default=0.0, ge=0.0,
                                            description="lateral offset of "
                                            "slot from the edge")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        shape = body.wrapped if hasattr(body, "wrapped") else body
        edge = _resolve_bend_edge(shape, args.edge_selector)
        axis_kind, mid, length, endpoints = _edge_geom(edge)
        if axis_kind not in ("X", "Y"):
            raise RuntimeError(
                f"slot: edge must be parallel to X or Y, got {axis_kind}"
            )

        xmin, ymin, zmin, xmax, ymax, zmax = _shape_bbox(shape)
        sheet_t = zmax - zmin
        # Through cut: cut a little extra in z to ensure clean Boolean result.
        if args.slot_thickness_mm is None:
            cut_z_start = zmin - 0.01
            cut_z_height = sheet_t + 0.02
        else:
            cut_z_start = zmax - args.slot_thickness_mm
            cut_z_height = args.slot_thickness_mm + 0.01

        if axis_kind == "Y":
            edge_position = mid[0]
            (p1, p2) = endpoints
            perp_min = min(p1[1], p2[1])
            perp_max = max(p1[1], p2[1])
            center = (
                args.slot_position_along_edge_mm
                if args.slot_position_along_edge_mm is not None
                else (perp_min + perp_max) / 2.0
            )

            if args.slot_inset_side[1] != "X":
                raise RuntimeError(
                    f"slot: slot_inset_side {args.slot_inset_side} not "
                    f"perpendicular to Y-edge"
                )
            side_sign = +1 if args.slot_inset_side[0] == "+" else -1
            if side_sign > 0:
                # cut extends from edge_position + offset into +X
                corner_x = edge_position + args.offset_from_edge_mm
            else:
                corner_x = (edge_position - args.offset_from_edge_mm
                            - args.slot_length_mm)
            corner_y = center - args.slot_width_mm / 2.0
            tool = _make_box(
                (corner_x, corner_y, cut_z_start),
                args.slot_length_mm, args.slot_width_mm, cut_z_height,
            )
        else:
            edge_position = mid[1]
            (p1, p2) = endpoints
            perp_min = min(p1[0], p2[0])
            perp_max = max(p1[0], p2[0])
            center = (
                args.slot_position_along_edge_mm
                if args.slot_position_along_edge_mm is not None
                else (perp_min + perp_max) / 2.0
            )
            if args.slot_inset_side[1] != "Y":
                raise RuntimeError(
                    f"slot: slot_inset_side {args.slot_inset_side} not "
                    f"perpendicular to X-edge"
                )
            side_sign = +1 if args.slot_inset_side[0] == "+" else -1
            if side_sign > 0:
                corner_y = edge_position + args.offset_from_edge_mm
            else:
                corner_y = (edge_position - args.offset_from_edge_mm
                            - args.slot_length_mm)
            corner_x = center - args.slot_width_mm / 2.0
            tool = _make_box(
                (corner_x, corner_y, cut_z_start),
                args.slot_width_mm, args.slot_length_mm, cut_z_height,
            )

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("slot: BRepAlgoAPI_Cut failed")

        history = EntityHistoryMap(
            rules={
                "slot_walls": HistoryRule.GENERATED_NEW,
                "consumed":   HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(cut.Shape()), history=history)
