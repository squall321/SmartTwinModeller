"""gear_internal_involute — atomic create skill.

Internal (ring) gear: external outline is a plain circle (`outer_diameter_mm`),
inner boundary carries involute teeth pointing INWARD (toward the gear axis).

Geometry conventions (ISO / standard internal gear):
    pitch_radius     Rp = module * n_teeth / 2
    base_radius      Rb = Rp * cos(pressure_angle)
    addendum_radius  Ra = Rp - module          (tooth tip — closer to center)
    dedendum_radius  Rd = Rp + 1.25 * module   (root — further from center)
    outer_radius     must satisfy  Ro > Rd  (some wall thickness)

For an internal gear the involute is on the "inside" of the base circle, so we
use the same involute parameterization as the external gear but evaluate it
between t at Ra (smallest) and t at Rd (largest). For each tooth we still
sweep the same involute curve outward from base_r — the difference is that
the resulting tooth profile forms the BOUNDARY OF A HOLE rather than the
outer outline.

Final body = circular outer cylinder MINUS a toothed-prism cutter; equivalently,
a face built from an outer circle wire (CCW) + inner toothed hole wire (CW),
then extruded.

Constraints checked:
    - outer_diameter_mm / 2 > Rd     (must enclose the dedendum)
    - n_teeth ≥ 10
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
    return (
        base_r * (math.cos(t) + t * math.sin(t)),
        base_r * (math.sin(t) - t * math.cos(t)),
    )


def _build_internal_tooth_outline_wire(
    module: float,
    n_teeth: int,
    pressure_angle_rad: float,
    samples_per_flank: int = 12,
):
    """Build a closed wire (CCW) approximating the toothed inner boundary of
    an internal gear, in the XY plane centered at origin.

    The "tooth tip" (addendum) is the point CLOSEST to the center; the
    "root" (dedendum) is the point FARTHEST from center. Otherwise the
    involute construction is identical to the external case (curve emanating
    from the base circle outward in the radial direction).
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    pitch_r = module * n_teeth / 2.0
    base_r = pitch_r * math.cos(pressure_angle_rad)
    add_r = pitch_r - module          # tip (closer to center)
    ded_r = pitch_r + 1.25 * module   # root (farther out)

    # The involute can only exist outside the base circle (r >= base_r).
    # For an internal gear with shallow teeth, add_r < base_r is fine — we
    # then start the flank at base_r and travel outward to ded_r, and connect
    # base_r down to add_r with a small radial segment (similar to external
    # gear's dedendum extension).
    flank_inner_r = max(add_r, base_r)
    flank_outer_r = ded_r

    if flank_outer_r <= flank_inner_r + 1e-9:
        raise ValueError(
            "gear_internal_involute: degenerate teeth — dedendum radius "
            f"({flank_outer_r}) must exceed flank-start radius ({flank_inner_r})"
        )

    def t_at_radius(r: float) -> float:
        ratio = max((r / base_r) ** 2 - 1.0, 0.0)
        return math.sqrt(ratio)

    t_inner = t_at_radius(flank_inner_r)
    t_outer = t_at_radius(flank_outer_r)

    # Pitch-point involute angle for centering tooth.
    t_p = t_at_radius(pitch_r)
    inv_angle_at_pitch = t_p - math.atan(t_p)

    # Internal gear: at the pitch circle the SPACE between teeth equals π·m/2
    # (i.e., the tooth THICKNESS of an internal gear is "inverted"). For the
    # hole-boundary wire we are tracing the SPACE between the gear's material,
    # so the "tooth-of-the-hole" half-angle is π / (2·z) — same expression.
    half_tooth_angle_at_pitch = math.pi / (2.0 * n_teeth)
    flank_shift = half_tooth_angle_at_pitch + inv_angle_at_pitch

    angular_pitch = 2.0 * math.pi / n_teeth

    def rot(p: tuple[float, float], ang: float) -> tuple[float, float]:
        c, s = math.cos(ang), math.sin(ang)
        x, y = p
        return (c * x - s * y, s * x + c * y)

    # Sample flank from inner (near center) to outer (near root, larger r).
    flank_pts: list[tuple[float, float]] = []
    for i in range(samples_per_flank):
        u = i / (samples_per_flank - 1)
        t = t_inner + u * (t_outer - t_inner)
        flank_pts.append(_involute_xy(base_r, t))

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

        # Radial extension from add_r up to flank_inner_r (when base_r > add_r).
        if flank_inner_r > add_r + 1e-9:
            tip_ang = tooth_center
            all_points.append((add_r * math.cos(tip_ang), add_r * math.sin(tip_ang)))

        # Right flank (from inner to outer), rotated by tooth_center - flank_shift.
        right_pts = [rot(p, tooth_center - flank_shift) for p in flank_pts]
        all_points.extend(right_pts)

        # Dedendum arc between this tooth's right-outer and left-outer (across root).
        # Move to the next tooth's left flank.
        # Compute dedendum arc from (tooth_center - flank_shift) at outer r to
        # ((k+1)*pitch - flank_shift') etc. — actually root-arc is shorter than
        # that; the simpler boundary is: after right flank ends at root, sweep
        # along dedendum circle to the next tooth's left flank start at root,
        # then descend the left flank back inward.
        # For internal gear, between teeth at the root (outer side) we have
        # a SHORT arc. Going CCW around the hole means: right flank goes
        # outward, then short arc along ded_r CCW to next-tooth's mirror, then
        # left flank of NEXT tooth comes back inward.
        # Actually it's simpler: per tooth, emit right flank (out), arc along
        # dedendum to left flank (out point), left flank (inward back to tip),
        # arc along addendum to next tooth's tip.
        # — let me redo this cleanly:
        pass

    # Restart with a cleaner per-tooth assembly:
    all_points = []
    for k in range(n_teeth):
        tooth_center = k * angular_pitch
        # Tip point (addendum, closest to center). Centered on tooth_center.
        tip_pt = (add_r * math.cos(tooth_center), add_r * math.sin(tooth_center))

        # Right flank (going from tip outward to root).
        # If flank_inner_r > add_r, prepend a radial segment from tip to
        # flank_inner_r along the tooth-center direction.
        right_pts: list[tuple[float, float]] = []
        if flank_inner_r > add_r + 1e-9:
            right_pts.append(tip_pt)
        right_raw = [rot(p, tooth_center - flank_shift) for p in flank_pts]
        right_pts.extend(right_raw)

        # Left flank (mirror of involute about X-axis then rotated by
        # tooth_center + flank_shift), going from root back to tip.
        left_raw = [(x, -y) for (x, y) in flank_pts]
        left_pts = [rot(p, tooth_center + flank_shift) for p in left_raw]
        left_pts.reverse()
        if flank_inner_r > add_r + 1e-9:
            left_pts.append(tip_pt)

        # Append right flank.
        all_points.extend(right_pts)
        # Dedendum arc between right flank root end (angle ≈ tooth_center-flank_shift
        # at r=flank_outer_r) and left flank root start (angle ≈ tooth_center+flank_shift
        # at r=flank_outer_r) going CCW (the SHORT way around the root).
        r_end_ang = math.atan2(right_pts[-1][1], right_pts[-1][0])
        l_start_ang = math.atan2(left_pts[0][1], left_pts[0][0])
        # Force CCW direction on the root arc (l > r).
        if l_start_ang < r_end_ang:
            l_start_ang += 2.0 * math.pi
        all_points.extend(emit_arc(flank_outer_r, r_end_ang, l_start_ang, 4)[1:-1])
        # Append left flank.
        all_points.extend(left_pts)

        # Addendum arc from this tooth's tip to the next tooth's tip, going CCW
        # (the LONG way along addendum between teeth) — actually short, since
        # tooth tip→next tooth tip is angular_pitch.
        next_tip_ang = (k + 1) * angular_pitch
        cur_tip_ang = tooth_center
        if next_tip_ang < cur_tip_ang:
            next_tip_ang += 2.0 * math.pi
        all_points.extend(emit_arc(add_r, cur_tip_ang, next_tip_ang, 4)[1:-1])

    # Deduplicate consecutive near-identical points.
    cleaned: list[tuple[float, float]] = []
    for p in all_points:
        if cleaned and (
            abs(cleaned[-1][0] - p[0]) < 1e-6 and abs(cleaned[-1][1] - p[1]) < 1e-6
        ):
            continue
        cleaned.append(p)

    poly = BRepBuilderAPI_MakePolygon()
    for (x, y) in cleaned:
        poly.Add(gp_Pnt(x, y, 0.0))
    poly.Close()
    poly.Build()
    if not poly.IsDone():
        raise RuntimeError("gear_internal_involute: inner toothed wire failed")
    return poly.Wire()


@skill(
    name="gear_internal_involute",
    category="create",
    level="atomic",
    summary="Internal (ring) gear with involute tooth profile pointing inward. "
            "Outer boundary is a circle; inner boundary carries n_teeth involute teeth.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["ring_gear_solid", "internal_involute_teeth"],
    preserves=[],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "wire_edm":          {"min_wall_mm": 0.3},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"side_action_required": True}},
    },
    failure_modes=[
        "fm.gear_n_teeth_too_low",
        "fm.gear_outer_diameter_too_small",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class GearInternalInvolute(SkillBase):
    class Args(BaseModel):
        module_mm: float = Field(gt=0)
        n_teeth: int = Field(ge=10)
        pressure_angle_deg: float = Field(default=20.0, gt=0.0, lt=45.0)
        width_mm: float = Field(gt=0)
        outer_diameter_mm: float = Field(gt=0)
        center_x_mm: float = 0.0
        center_y_mm: float = 0.0
        base_z_mm: float = 0.0

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeEdge,
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakeWire,
            BRepBuilderAPI_Transform,
        )
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
        from OCP.TopoDS import TopoDS
        from build123d import Part

        m = args.module_mm
        z = args.n_teeth
        alpha = math.radians(args.pressure_angle_deg)

        pitch_r = m * z / 2.0
        ded_r = pitch_r + 1.25 * m
        outer_r = args.outer_diameter_mm / 2.0
        if outer_r <= ded_r:
            raise ValueError(
                f"gear_internal_involute: outer_diameter/2 ({outer_r}) must "
                f"exceed dedendum radius ({ded_r}) for a ring-gear wall"
            )

        # Inner toothed wire.
        inner_wire = _build_internal_tooth_outline_wire(m, z, alpha)

        # Outer circle wire.
        outer_circ = gp_Circ(
            gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)),
            outer_r,
        )
        me = BRepBuilderAPI_MakeEdge(outer_circ)
        me.Build()
        if not me.IsDone():
            raise RuntimeError("gear_internal_involute: outer circle edge failed")
        mw = BRepBuilderAPI_MakeWire()
        mw.Add(me.Edge())
        mw.Build()
        if not mw.IsDone():
            raise RuntimeError("gear_internal_involute: outer circle wire failed")
        outer_wire = mw.Wire()

        # Build annular face: outer wire (CCW = +) then inner wire reversed
        # (CW = hole). BRepBuilderAPI_MakeFace requires the hole boundary to
        # have the OPPOSITE orientation of the outer boundary; without the
        # explicit reverse, OCCT computes the face area as outer + inner
        # rather than outer − inner (verified empirically).
        mf = BRepBuilderAPI_MakeFace(outer_wire, True)
        inner_wire_reversed = TopoDS.Wire_s(inner_wire.Reversed())
        mf.Add(inner_wire_reversed)
        mf.Build()
        if not mf.IsDone():
            raise RuntimeError("gear_internal_involute: annular face failed")
        face = mf.Face()

        prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, args.width_mm))
        prism.Build()
        if not prism.IsDone():
            raise RuntimeError("gear_internal_involute: extrude failed")
        shape = prism.Shape()

        if (args.center_x_mm, args.center_y_mm, args.base_z_mm) != (0.0, 0.0, 0.0):
            tr = gp_Trsf()
            tr.SetTranslation(
                gp_Vec(args.center_x_mm, args.center_y_mm, args.base_z_mm)
            )
            xf = BRepBuilderAPI_Transform(shape, tr, True)
            xf.Build()
            if not xf.IsDone():
                raise RuntimeError("gear_internal_involute: translate failed")
            shape = xf.Shape()

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shape), history=history)
