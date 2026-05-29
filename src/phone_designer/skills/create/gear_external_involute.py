"""gear_external_involute — atomic create skill.

External spur gear (standard involute tooth profile).

Geometry:
    pitch_radius     Rp = module * n_teeth / 2
    base_radius      Rb = Rp * cos(pressure_angle)
    addendum_radius  Ra = Rp + module
    dedendum_radius  Rd = max(Rp - 1.25 * module, 1e-3)   (clamped > 0)

Profile construction:
    For one tooth centered at angle 0 we build a closed sub-wire consisting of:
        1. an involute curve from the base circle outward to the addendum,
        2. the mirror of that involute across the tooth-center line,
        3. an arc along the addendum circle between the two involute tips,
        4. arcs along the dedendum circle between adjacent teeth (root land).

    The single tooth + the half-pitch root gap on each side are concatenated
    n_teeth times around the gear to form one closed outer wire.

Postprocessing:
    closed outer wire → planar face → extrude `width_mm` along +Z → optional
    central bore cut (BRepAlgoAPI_Cut with a cylinder).

This is a kinematically correct involute (the curve a tooth actually has on a
real gear) — not a sinusoidal approximation.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _involute_xy(base_r: float, t: float) -> tuple[float, float]:
    """Standard involute of a circle of radius `base_r` parameter t."""
    return (
        base_r * (math.cos(t) + t * math.sin(t)),
        base_r * (math.sin(t) - t * math.cos(t)),
    )


def _build_gear_outline_wire(
    module: float,
    n_teeth: int,
    pressure_angle_rad: float,
    samples_per_flank: int = 12,
):
    """Build the closed outer wire of an external involute spur gear in the XY
    plane (axis = +Z, centered at origin).

    Returns TopoDS_Wire.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakePolygon,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.gp import gp_Pnt
    from OCP.TColgp import TColgp_HArray1OfPnt

    pitch_r = module * n_teeth / 2.0
    base_r = pitch_r * math.cos(pressure_angle_rad)
    add_r = pitch_r + module
    ded_r = max(pitch_r - 1.25 * module, 1e-3)

    # If the dedendum radius is smaller than the base radius, the flank below
    # the base circle is a radial line down to the dedendum (a "radial extension"
    # commonly used for small gears). We always start the involute at the
    # larger of base_r and ded_r — and add a radial connecting edge if needed.
    flank_start_r = max(base_r, ded_r)

    # Parameter values
    # At involute parameter t, the radial distance from gear center is
    # r(t) = base_r * sqrt(1 + t²).
    def t_at_radius(r: float) -> float:
        ratio = max((r / base_r) ** 2 - 1.0, 0.0)
        return math.sqrt(ratio)

    t_start = t_at_radius(flank_start_r)
    t_end = t_at_radius(add_r)

    # Pitch-point involute angle (used to center the tooth thickness).
    # The involute at parameter t is at angle theta(t) = t - atan(t) above the
    # +X axis (since the involute starts at (base_r, 0) when t = 0).
    t_p = t_at_radius(pitch_r)
    inv_angle_at_pitch = t_p - math.atan(t_p)

    # Standard tooth thickness at the pitch circle = π·m / 2 → corresponds to
    # half-angle at pitch = (π·m/2) / (2·Rp) = π / (2·z).
    half_tooth_angle_at_pitch = math.pi / (2.0 * n_teeth)

    # Shift the right flank so that the tooth is symmetric about angle 0.
    # Right flank: rotated by +flank_shift; left flank: rotated by -flank_shift
    # then mirrored across the X-axis.
    flank_shift = half_tooth_angle_at_pitch + inv_angle_at_pitch

    angular_pitch = 2.0 * math.pi / n_teeth

    def rot(p: tuple[float, float], ang: float) -> tuple[float, float]:
        c, s = math.cos(ang), math.sin(ang)
        x, y = p
        return (c * x - s * y, s * x + c * y)

    # Sample the involute flank (right side, before tooth-centering rotation).
    flank_pts: list[tuple[float, float]] = []
    for i in range(samples_per_flank):
        u = i / (samples_per_flank - 1)
        t = t_start + u * (t_end - t_start)
        flank_pts.append(_involute_xy(base_r, t))
    # If t_start == t_end == 0 (degenerate), just collapse to single point;
    # handled by guard below.

    # Build a polygonal closed wire for all teeth (involute approximated by
    # high-density polyline). Going CCW.
    poly = BRepBuilderAPI_MakePolygon()

    # Helper: emit a curved arc along a circle of radius r from angle a0 to a1
    # as a polyline (positive direction = CCW). Used for addendum top and
    # dedendum root.
    def emit_arc(r: float, a0: float, a1: float, n: int) -> list[tuple[float, float]]:
        if n < 2:
            n = 2
        out = []
        for i in range(n):
            u = i / (n - 1)
            a = a0 + u * (a1 - a0)
            out.append((r * math.cos(a), r * math.sin(a)))
        return out

    all_points: list[tuple[float, float]] = []

    for k in range(n_teeth):
        tooth_center = k * angular_pitch
        # Right flank (going from root to addendum tip), rotated by
        # (tooth_center - flank_shift). The raw involute starts at angle 0 on
        # base circle and curls CCW.
        right_pts = [rot(p, tooth_center - flank_shift) for p in flank_pts]
        # Left flank: mirror raw involute about X axis (y → -y) then rotate by
        # (tooth_center + flank_shift). Reverse direction so it goes from
        # addendum tip back down to the root.
        left_pts_raw = [(x, -y) for (x, y) in flank_pts]
        left_pts = [rot(p, tooth_center + flank_shift) for p in left_pts_raw]
        left_pts.reverse()

        # Optional radial extension from dedendum up to base circle if
        # base_r > ded_r (a small radial segment, only needed when flank
        # doesn't already reach the root).
        if flank_start_r > ded_r + 1e-9:
            # Right flank root radial extension (from dedendum at the tooth's
            # right-flank-root angle, radially out to flank_start_r).
            root_ang_r = tooth_center - flank_shift  # angle of flank base point
            all_points.append(
                (ded_r * math.cos(root_ang_r), ded_r * math.sin(root_ang_r))
            )

        # Right flank
        all_points.extend(right_pts)
        # Addendum arc from right tip to left tip
        right_tip_ang = math.atan2(right_pts[-1][1], right_pts[-1][0])
        left_tip_ang = math.atan2(left_pts[0][1], left_pts[0][0])
        # Ensure CCW (left_tip_ang > right_tip_ang)
        if left_tip_ang < right_tip_ang:
            left_tip_ang += 2.0 * math.pi
        all_points.extend(emit_arc(add_r, right_tip_ang, left_tip_ang, 6)[1:-1])
        # Left flank (already reversed)
        all_points.extend(left_pts)

        # Optional radial extension back down to dedendum at the left-flank-root.
        if flank_start_r > ded_r + 1e-9:
            root_ang_l = tooth_center + flank_shift
            all_points.append(
                (ded_r * math.cos(root_ang_l), ded_r * math.sin(root_ang_l))
            )

        # Dedendum arc from this tooth's left-root to next tooth's right-root.
        next_center = (k + 1) * angular_pitch
        left_root_ang = tooth_center + flank_shift
        next_right_root_ang = next_center - flank_shift
        if next_right_root_ang < left_root_ang:
            next_right_root_ang += 2.0 * math.pi
        arc_pts = emit_arc(ded_r, left_root_ang, next_right_root_ang, 5)
        # skip first (== last point already in all_points or close to it) and
        # last (== next tooth's first radial root)
        all_points.extend(arc_pts[1:-1])

    # Deduplicate consecutive near-identical points (numeric guard).
    cleaned: list[tuple[float, float]] = []
    for p in all_points:
        if cleaned and (
            abs(cleaned[-1][0] - p[0]) < 1e-6 and abs(cleaned[-1][1] - p[1]) < 1e-6
        ):
            continue
        cleaned.append(p)

    for (x, y) in cleaned:
        poly.Add(gp_Pnt(x, y, 0.0))
    poly.Close()
    poly.Build()
    if not poly.IsDone():
        raise RuntimeError("gear_external_involute: outline polygon failed")
    return poly.Wire()


@skill(
    name="gear_external_involute",
    category="create",
    level="atomic",
    summary="External spur gear with kinematically correct involute tooth profile. "
            "Standard module/pressure-angle parameterization (ISO). Optional central bore.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["gear_solid", "involute_teeth"],
    preserves=[],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "wire_edm":          {"min_wall_mm": 0.3},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"side_action_required": True}},
    },
    failure_modes=[
        "fm.gear_n_teeth_too_low",
        "fm.gear_module_invalid",
        "fm.gear_bore_exceeds_dedendum",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="body_present")],
)
class GearExternalInvolute(SkillBase):
    class Args(BaseModel):
        module_mm: float = Field(gt=0)
        n_teeth: int = Field(ge=10)
        pressure_angle_deg: float = Field(default=20.0, gt=0.0, lt=45.0)
        width_mm: float = Field(gt=0)
        center_x_mm: float = 0.0
        center_y_mm: float = 0.0
        base_z_mm: float = 0.0
        bore_diameter_mm: float = Field(default=0.0, ge=0.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_Transform,
        )
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
        from build123d import Part

        m = args.module_mm
        z = args.n_teeth
        alpha = math.radians(args.pressure_angle_deg)

        pitch_r = m * z / 2.0
        ded_r = max(pitch_r - 1.25 * m, 1e-3)
        if args.bore_diameter_mm > 0.0 and args.bore_diameter_mm / 2.0 >= ded_r:
            raise ValueError(
                f"gear_external_involute: bore radius "
                f"({args.bore_diameter_mm/2.0}) >= dedendum radius ({ded_r})"
            )

        wire = _build_gear_outline_wire(m, z, alpha)

        mf = BRepBuilderAPI_MakeFace(wire, True)
        mf.Build()
        if not mf.IsDone():
            raise RuntimeError("gear_external_involute: face from outline failed")
        face = mf.Face()

        prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, args.width_mm))
        prism.Build()
        if not prism.IsDone():
            raise RuntimeError("gear_external_involute: extrude failed")
        shape = prism.Shape()

        # Optional central bore (cut with a cylinder).
        if args.bore_diameter_mm > 0.0:
            bore_r = args.bore_diameter_mm / 2.0
            ax2 = gp_Ax2(gp_Pnt(0.0, 0.0, -1.0), gp_Dir(0.0, 0.0, 1.0))
            cyl = BRepPrimAPI_MakeCylinder(ax2, bore_r, args.width_mm + 2.0)
            cyl.Build()
            if not cyl.IsDone():
                raise RuntimeError("gear_external_involute: bore cylinder failed")
            cut = BRepAlgoAPI_Cut(shape, cyl.Shape())
            cut.Build()
            if not cut.IsDone():
                raise RuntimeError("gear_external_involute: bore cut failed")
            shape = cut.Shape()

        # Translate to (center_x, center_y, base_z).
        if (args.center_x_mm, args.center_y_mm, args.base_z_mm) != (0.0, 0.0, 0.0):
            tr = gp_Trsf()
            tr.SetTranslation(
                gp_Vec(args.center_x_mm, args.center_y_mm, args.base_z_mm)
            )
            xf = BRepBuilderAPI_Transform(shape, tr, True)
            xf.Build()
            if not xf.IsDone():
                raise RuntimeError("gear_external_involute: translate failed")
            shape = xf.Shape()

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shape), history=history)
