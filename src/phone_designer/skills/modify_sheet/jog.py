"""jog — atomic. Z-shaped offset between two coplanar regions.

A jog is a pair of bends with opposite senses producing a small Z-step in
the sheet. It is used when one region of a panel must sit on a different
plane than the rest (e.g., to nest into a recess) without changing its
overall flatness.

Geometry:
    - The host sheet is divided at the jog by two parallel bend lines that
      are `jog_run_mm` apart.
    - The first bend lifts (or drops) the "tab" half by the jog angle; a
      short flat connecting strip of length `jog_run_mm` continues the
      offset; the second bend brings the tab half back parallel to the host
      plane, offset by `jog_height_mm`.
    - We approximate the result by constructing keep + connector + offset
      strips as a piecewise-linear Z-shape with simple sharp transitions
      (no toroidal sectors — at small jog heights the bends are sharp).

Args:
    bend_axis: "X" | "Y" — direction of the jog bend lines (both are parallel).
    bend_position_mm: coordinate of the first bend on the perpendicular axis.
    jog_run_mm: distance between the two bend lines.
    jog_height_mm: signed Z offset of the offset region relative to the host
        (positive = up).
    bend_side: "+X" | "-X" | "+Y" | "-Y" — the half that gets offset.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_sheet.bend_edge import (
    _fuse,
    _make_box,
    _shape_bbox,
)


@skill(
    name="jog",
    category="modify/sheet",
    level="atomic",
    summary="Z-shaped offset in a flat sheet — two parallel opposing bends "
            "that move a portion of the sheet onto a parallel plane offset "
            "by `jog_height_mm`.",
    selector_kinds=[],
    history_rules={
        "jog_step":   HistoryRule.GENERATED_NEW,
        "host_face":  HistoryRule.MODIFIED_INHERIT,
    },
    preconditions=[
        "pc.input_is_flat_sheet",
    ],
    produces_features=["jog"],
    preserves=["sheet_thickness"],
    manufacturing={
        "sheet_metal_stamp": {"min_fillet_r_mm": 0.1,
                              "extras": {"jog_run_min_mm": 0.5}},
    },
    failure_modes=[
        "fm.jog_position_outside_sheet",
        "fm.jog_run_zero",
        "fm.jog_overlap_host_thickness",
    ],
    cost_hint=0.35,
    post_conditions=[PostCondition(kind="volume_changed", min_delta_mm3=0.5)],
)
class Jog(SkillBase):
    class Args(BaseModel):
        bend_axis: Literal["X", "Y"]
        bend_position_mm: float
        jog_run_mm: float = Field(gt=0.0, le=200.0,
                                   description="distance between the two "
                                   "parallel bend lines")
        jog_height_mm: float = Field(gt=-50.0, lt=50.0,
                                      description="signed Z offset; cannot "
                                      "equal 0")
        bend_side: Literal["+X", "-X", "+Y", "-Y"]

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        if abs(args.jog_height_mm) < 1e-6:
            raise RuntimeError("jog: jog_height_mm must be non-zero")

        shape = body.wrapped if hasattr(body, "wrapped") else body
        xmin, ymin, zmin, xmax, ymax, zmax = _shape_bbox(shape)
        t = zmax - zmin
        if t <= 0:
            raise RuntimeError("jog: input body has zero z-extent")

        # Validate bend_side perpendicular to bend_axis.
        perp_axis = "X" if args.bend_axis == "Y" else "Y"
        if args.bend_side[1] != perp_axis:
            raise RuntimeError(
                f"jog: bend_side {args.bend_side} not perpendicular to "
                f"bend axis {args.bend_axis} (expected ±{perp_axis})"
            )
        side_sign = +1 if args.bend_side[0] == "+" else -1

        p_start = args.bend_position_mm           # first bend line
        p_end = p_start + side_sign * args.jog_run_mm  # second bend line

        # Validate both bend positions lie inside the bbox along perp_axis.
        if perp_axis == "X":
            lo, hi = xmin, xmax
        else:
            lo, hi = ymin, ymax
        for p in (p_start, p_end):
            if not (lo + 1e-6 < p < hi - 1e-6):
                raise RuntimeError(
                    f"jog: bend at {p:.3f} is outside body bounds "
                    f"[{lo:.3f}, {hi:.3f}] along {perp_axis} axis"
                )

        # Piecewise construction:
        #   - keep region from perp_min..p_start at z=zmin..zmin+t
        #   - connector ramp (a tilted strip) from p_start..p_end at jog_height
        #     We model the connector as an axis-aligned slab at the *target*
        #     offset plane joining the two halves vertically — i.e. simplified
        #     into: a vertical wall (transition wall) at p_start, a flat
        #     connector at the offset plane from p_start..p_end, and the
        #     offset region from p_end..perp_max at z = zmin + jog_height ..
        #     zmin + jog_height + t.
        #   - For the bbox/volume checks we just need keep_region + transition
        #     wall + offset region. The volume change is approximately:
        #       transition_wall_volume = |jog_height_mm| * (perp_range) * t
        #     plus a slight overlap subtracted.

        h = args.jog_height_mm

        if perp_axis == "X":
            edge_extent_min, edge_extent_max = ymin, ymax
            edge_len = edge_extent_max - edge_extent_min

            if side_sign > 0:
                # Bend side is +X. keep = [xmin..p_start], offset = [p_end..xmax].
                # When side_sign > 0, p_end > p_start.
                keep_dx = p_start - xmin
                offset_x0 = p_end
                offset_dx = xmax - p_end
            else:
                # Bend side is -X. p_end < p_start. keep = [p_start..xmax].
                keep_dx = xmax - p_start
                offset_x0 = xmin
                offset_dx = p_end - xmin

            if keep_dx <= 0 or offset_dx <= 0:
                raise RuntimeError(
                    "jog: degenerate keep or offset region after applying "
                    "jog_run."
                )

            # keep region: original sheet half (unchanged).
            if side_sign > 0:
                keep_box = _make_box(
                    (xmin, ymin, zmin), keep_dx, edge_len, t,
                )
            else:
                keep_box = _make_box(
                    (p_start, ymin, zmin), keep_dx, edge_len, t,
                )

            # offset region: same half shifted by +h in z.
            offset_box = _make_box(
                (offset_x0, ymin, zmin + h), offset_dx, edge_len, t,
            )

            # connector: a flat box of length jog_run at the *offset* plane,
            # spanning between the two bend lines.
            connector_x0 = min(p_start, p_end)
            connector_dx = abs(p_end - p_start)
            connector_box = _make_box(
                (connector_x0, ymin, zmin + h), connector_dx, edge_len, t,
            )

            # transition wall: vertical slab between z = zmin and z = zmin + h
            # at the perp_axis position closest to keep (i.e. p_start).
            wall_z0 = zmin + min(0.0, h)
            wall_dz = abs(h)
            # The wall is one thickness wide along perp_axis (or thinner — t
            # of the sheet), placed touching the keep region.
            if side_sign > 0:
                wall_x0 = p_start
            else:
                wall_x0 = p_start - t
            wall_box = _make_box(
                (wall_x0, ymin, wall_z0), t, edge_len, wall_dz + t,
            )
        else:
            # bend_axis == "X", perp_axis == "Y"
            edge_extent_min, edge_extent_max = xmin, xmax
            edge_len = edge_extent_max - edge_extent_min

            if side_sign > 0:
                keep_dy = p_start - ymin
                offset_y0 = p_end
                offset_dy = ymax - p_end
            else:
                keep_dy = ymax - p_start
                offset_y0 = ymin
                offset_dy = p_end - ymin

            if keep_dy <= 0 or offset_dy <= 0:
                raise RuntimeError(
                    "jog: degenerate keep or offset region after applying "
                    "jog_run."
                )

            if side_sign > 0:
                keep_box = _make_box(
                    (xmin, ymin, zmin), edge_len, keep_dy, t,
                )
            else:
                keep_box = _make_box(
                    (xmin, p_start, zmin), edge_len, keep_dy, t,
                )

            offset_box = _make_box(
                (xmin, offset_y0, zmin + h), edge_len, offset_dy, t,
            )

            connector_y0 = min(p_start, p_end)
            connector_dy = abs(p_end - p_start)
            connector_box = _make_box(
                (xmin, connector_y0, zmin + h), edge_len, connector_dy, t,
            )

            wall_z0 = zmin + min(0.0, h)
            wall_dz = abs(h)
            if side_sign > 0:
                wall_y0 = p_start
            else:
                wall_y0 = p_start - t
            wall_box = _make_box(
                (xmin, wall_y0, wall_z0), edge_len, t, wall_dz + t,
            )

        fused = _fuse(keep_box, wall_box)
        fused = _fuse(fused, connector_box)
        fused = _fuse(fused, offset_box)

        history = EntityHistoryMap(
            rules={
                "jog_step":  HistoryRule.GENERATED_NEW,
                "host_face": HistoryRule.MODIFIED_INHERIT,
            },
        )
        return SkillResult(body=Part(fused), history=history)
