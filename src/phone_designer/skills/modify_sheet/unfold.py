"""unfold — atomic. Flatten all bends and produce the unfolded (developed) blank.

Computes the developed (flat-pattern) layout for a bent sheet-metal body
using the standard neutral-axis bend-allowance formula:

    L_developed = θ × (R + K × T)

where θ is the bend angle (radians), R is the inner bend radius, T is the
sheet thickness, and K is the K-factor (neutral-axis offset ratio). The
default K = 0.4 corresponds to typical aluminum/mild-steel forming.

This implementation analyses the body's cylindrical bend regions (the
toroidal sectors produced by `bend_edge` / `flange` / `hem`) and replaces
each curved region of arc length L_curved (= θ × (R + T/2), measured on the
*centerline*) with the equivalent developed straight length L_developed
(= θ × (R + K × T)).

For the canonical case (a single bent sheet built by `sheet_base` + one
`bend_edge`), the unfolded result is a flat rectangle whose perpendicular
dimension equals:

    (flat keep length) + L_developed + (flat bend length)

The output body is constructed as a flat rectangular sheet at the original
bottom plane z = zmin..zmin+thickness, with width matching the original
edge length and length matching the developed perpendicular extent.

This is a coarse but useful approximation: it correctly handles the
geometric consequence of unfolding (the flat pattern is shorter than the
sum of arc-length + chord because the neutral-axis develops to less than
the outer-fiber arc length). Per-face topology is not preserved.

Args:
    k_factor: neutral-axis K-factor. Default 0.4 (typical aluminum).
    fallback_thickness_mm: thickness override if the body's z-extent cannot
        be determined (e.g. fully vertical panel). Defaults to inferring
        from the shortest body bbox dimension.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_sheet.bend_edge import _shape_bbox


def _collect_cylindrical_faces(shape) -> list[tuple[float, float, tuple[float, float, float]]]:
    """Scan shape for cylindrical faces (bend arc surfaces). Returns a list of
    tuples (radius, angle_span_rad, axis_dir) per cylinder.

    Heuristic — only returns *finite* bend-like cylinders (angle_span < 2π).
    Used to compute bend-allowance corrections.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_MapOfShape

    out: list[tuple[float, float, tuple[float, float, float]]] = []
    seen = TopTools_MapOfShape()
    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        face_shape = it.Current()
        if seen.Contains(face_shape):
            it.Next()
            continue
        seen.Add(face_shape)
        try:
            face = TopoDS.Face_s(face_shape)
            adaptor = BRepAdaptor_Surface(face, True)
            if adaptor.GetType() == GeomAbs_Cylinder:
                cyl = adaptor.Cylinder()
                radius = float(cyl.Radius())
                # angle span in U direction
                umin = float(adaptor.FirstUParameter())
                umax = float(adaptor.LastUParameter())
                angle_span = abs(umax - umin)
                if 1e-3 < angle_span < (2.0 * math.pi - 1e-3):
                    axis_dir = cyl.Axis().Direction()
                    out.append((
                        radius, angle_span,
                        (axis_dir.X(), axis_dir.Y(), axis_dir.Z()),
                    ))
        except Exception:
            pass
        it.Next()
    return out


def _measure_volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


@skill(
    name="unfold",
    category="modify/sheet",
    level="atomic",
    summary="Flatten a bent sheet-metal body into its developed (unfolded) "
            "flat pattern using the bend-allowance formula L = θ·(R + K·T). "
            "Default K = 0.4.",
    selector_kinds=[],
    history_rules={
        "unfold_body": HistoryRule.GENERATED_NEW,
        "host_body":   HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.input_is_sheet_metal",
    ],
    produces_features=["flat_pattern"],
    preserves=[],
    manufacturing={
        "sheet_metal_stamp": {"min_wall_mm": 0.3,
                              "extras": {"k_factor": 0.4}},
    },
    failure_modes=[
        "fm.unfold_no_thickness",
    ],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="volume_changed", min_delta_mm3=0.5,
                                    allow_no_change=True)],
)
class Unfold(SkillBase):
    class Args(BaseModel):
        k_factor: float = Field(default=0.4, ge=0.0, le=1.0,
                                 description="neutral-axis K-factor")
        fallback_thickness_mm: float | None = Field(default=None, gt=0.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        xmin, ymin, zmin, xmax, ymax, zmax = _shape_bbox(shape)

        dx = xmax - xmin
        dy = ymax - ymin
        dz = zmax - zmin

        # Infer thickness: typically the smallest bbox dimension for a panel
        # body, but the user can override. We also need the "in-plane" axis
        # along which the developed length grows.
        if args.fallback_thickness_mm is not None:
            thickness = args.fallback_thickness_mm
        else:
            # Inspect cylindrical bend faces first — each bend produces an
            # inner cylinder (radius R) and outer cylinder (radius R+T) on
            # the same axis. The smallest pair difference is the thickness.
            thickness = None
            radii_pre = _collect_cylindrical_faces(shape)
            radii_sorted = sorted({round(r, 4) for (r, _ang, _ax) in radii_pre})
            if len(radii_sorted) >= 2:
                diffs = [radii_sorted[i + 1] - radii_sorted[i]
                         for i in range(len(radii_sorted) - 1)]
                # Pick the smallest positive non-trivial diff as thickness.
                positive = [d for d in diffs if d > 1e-3]
                if positive:
                    thickness = min(positive)
            if thickness is None:
                # Fall back to smallest bbox axis (works for flat sheets).
                thickness = min(dx, dy, dz)
            if thickness <= 1e-6:
                raise RuntimeError(
                    "unfold: could not infer sheet thickness from bbox "
                    "(zero z-extent). Pass fallback_thickness_mm."
                )

        # Total volume of the body. The developed flat sheet should have the
        # same metal volume.
        vol_total = _measure_volume(shape)
        if vol_total <= 1e-6:
            raise RuntimeError("unfold: body has near-zero volume")

        # Compute bend-allowance corrections: for each cylindrical face,
        # determine its developed length using the centerline radius
        # (R + T/2). After unfolding, the same metal would occupy a flat
        # strip of length L_developed = θ × (R + K × T). The volume change
        # equals (flat_area - original_in_plane_projected_area) × thickness.
        cylinders = _collect_cylindrical_faces(shape)

        # Build a flat rectangular sheet of the same volume as the input. The
        # in-plane shape: keep one axis (the bend-axis, typically the longer
        # of dx/dy on the original flat region) as the "width", and let the
        # "length" be derived from total_area / width.
        # total_area = vol_total / thickness.
        total_area = vol_total / thickness

        # Choose width as the longer of dx/dy of the input bbox projected on
        # the bottom plane — bend-axis direction tends to be preserved across
        # the bend. If cylinders are detected, prefer the axis aligned with
        # the cylinder axis directions.
        width = max(dx, dy)
        # If cylindrical axes lie predominantly along one axis, prefer that
        # for `width` (since the bend axis = the cylinder axis = the width
        # direction in the flat blank).
        if cylinders:
            axis_sum_x = sum(abs(a[2][0]) for a in cylinders)
            axis_sum_y = sum(abs(a[2][1]) for a in cylinders)
            if axis_sum_x > axis_sum_y * 1.2:
                width = dx
            elif axis_sum_y > axis_sum_x * 1.2:
                width = dy

        if width <= 1e-6:
            raise RuntimeError("unfold: derived blank width is zero")

        length = total_area / width
        if length <= 1e-6:
            raise RuntimeError("unfold: derived blank length is zero")

        # Apply K-factor correction to the developed length per cylindrical
        # bend region detected. The cylinder face area in the original body
        # was approximately:
        #     A_cyl_face = angle × (R + T/2) × width
        # (centerline). After unfolding, the SAME metal occupies:
        #     A_cyl_flat = angle × (R + K × T) × width
        # The DIFFERENCE per bend is: width × angle × (T/2 - K × T)
        #                           = width × angle × T × (0.5 - K)
        # For K = 0.5 (Y-axis aligned with neutral axis), no correction.
        # For K = 0.4, the flat blank is slightly *shorter* (negative dA).
        delta_area = 0.0
        for (r_cyl, ang, _axis_dir) in cylinders:  # noqa: F841
            # Width along bend axis = `width`. Centerline arc -> flat length.
            l_arc_centerline = ang * (r_cyl + thickness / 2.0)
            l_flat_developed = ang * (r_cyl + args.k_factor * thickness)
            delta_area += width * (l_flat_developed - l_arc_centerline)

        # Apply correction.
        length += delta_area / width if width > 1e-6 else 0.0
        if length <= 1e-6:
            length = max(dx, dy)  # safety net

        # Build the flat blank centered at (mid_x, mid_y, zmin).
        mid_x = (xmin + xmax) / 2.0
        mid_y = (ymin + ymax) / 2.0

        # Determine which axis is the "width" axis based on which input dim
        # it matched.
        if width == dx:
            box_dx, box_dy = width, length
        else:
            box_dx, box_dy = length, width

        corner = gp_Pnt(mid_x - box_dx / 2.0, mid_y - box_dy / 2.0, zmin)
        maker = BRepPrimAPI_MakeBox(corner, box_dx, box_dy, thickness)
        maker.Build()
        if not maker.IsDone():
            raise RuntimeError("unfold: failed to build flat blank")
        flat_shape = maker.Shape()

        history = EntityHistoryMap(
            rules={
                "unfold_body": HistoryRule.GENERATED_NEW,
                "host_body":   HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(flat_shape), history=history)
