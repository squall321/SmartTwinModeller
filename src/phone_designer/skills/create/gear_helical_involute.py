"""gear_helical_involute — atomic create skill.

Helical involute spur gear. Same kinematic involute tooth profile as
`gear_external_involute`, but the tooth flanks are twisted along the gear
axis by a constant helix angle β.

Geometry:
    module           m  (mm/tooth)
    n_teeth          z
    pressure_angle   α  (deg, transverse plane)
    helix_angle      β  (deg)
    face_width       W  (axial extent, mm)

    pitch_radius     Rp = m · z / 2
    addendum         Ra = Rp + m
    dedendum         Rd = max(Rp − 1.25 m, 1e-3)

Construction strategy:
    The classic "single closed-wire planar profile → BRepPrimAPI_MakePrism with
    a twist" approach is not stable in OCCT for arbitrary closed wires (no
    twisted-prism primitive). Instead we build the gear by lofting the same
    transverse tooth-outline wire at multiple axial slices, each rotated by
    the cumulative helix twist for that slice height.

    For a face width W and helix angle β at the pitch radius:
        twist per unit Z = tan(β) / Rp        (helix advance angle per dz)
        total twist Δφ   = W · tan(β) / Rp

    The gear body is produced by stacking N (≥ 6) thin slabs:
        slice_i at z=z_i has the transverse outline rotated by φ_i = z_i·tan(β)/Rp
    Each slab is a planar prism (no twist within a single slab — only between
    adjacent slabs). Adjacent slabs are fused with BRepAlgoAPI_Fuse so that the
    resulting solid is a single connected body whose flanks closely approximate
    the true helical surface.

    For β = 0 this degenerates to a single straight extrusion (same as
    gear_external_involute, modulo the slab count).

post: body_present
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


def _build_transverse_outline_points(
    module: float,
    n_teeth: int,
    pressure_angle_rad: float,
    samples_per_flank: int = 10,
) -> list[tuple[float, float]]:
    """Return the cleaned list of XY points describing one closed transverse
    outline of the gear (same construction as gear_external_involute, but
    returns points so we can rotate them per slice)."""
    pitch_r = module * n_teeth / 2.0
    base_r = pitch_r * math.cos(pressure_angle_rad)
    add_r = pitch_r + module
    ded_r = max(pitch_r - 1.25 * module, 1e-3)
    flank_start_r = max(base_r, ded_r)

    def t_at_radius(r: float) -> float:
        ratio = max((r / base_r) ** 2 - 1.0, 0.0)
        return math.sqrt(ratio)

    t_start = t_at_radius(flank_start_r)
    t_end = t_at_radius(add_r)
    t_p = t_at_radius(pitch_r)
    inv_angle_at_pitch = t_p - math.atan(t_p)
    half_tooth_angle_at_pitch = math.pi / (2.0 * n_teeth)
    flank_shift = half_tooth_angle_at_pitch + inv_angle_at_pitch
    angular_pitch = 2.0 * math.pi / n_teeth

    def rot(p: tuple[float, float], ang: float) -> tuple[float, float]:
        c, s = math.cos(ang), math.sin(ang)
        x, y = p
        return (c * x - s * y, s * x + c * y)

    flank_pts: list[tuple[float, float]] = []
    for i in range(samples_per_flank):
        u = i / (samples_per_flank - 1)
        t = t_start + u * (t_end - t_start)
        flank_pts.append(_involute_xy(base_r, t))

    def emit_arc(r: float, a0: float, a1: float, n: int):
        out = []
        n = max(n, 2)
        for i in range(n):
            u = i / (n - 1)
            a = a0 + u * (a1 - a0)
            out.append((r * math.cos(a), r * math.sin(a)))
        return out

    all_points: list[tuple[float, float]] = []
    for k in range(n_teeth):
        tooth_center = k * angular_pitch
        right_pts = [rot(p, tooth_center - flank_shift) for p in flank_pts]
        left_pts_raw = [(x, -y) for (x, y) in flank_pts]
        left_pts = [rot(p, tooth_center + flank_shift) for p in left_pts_raw]
        left_pts.reverse()

        if flank_start_r > ded_r + 1e-9:
            root_ang_r = tooth_center - flank_shift
            all_points.append(
                (ded_r * math.cos(root_ang_r), ded_r * math.sin(root_ang_r))
            )

        all_points.extend(right_pts)
        right_tip_ang = math.atan2(right_pts[-1][1], right_pts[-1][0])
        left_tip_ang = math.atan2(left_pts[0][1], left_pts[0][0])
        if left_tip_ang < right_tip_ang:
            left_tip_ang += 2.0 * math.pi
        all_points.extend(emit_arc(add_r, right_tip_ang, left_tip_ang, 5)[1:-1])
        all_points.extend(left_pts)

        if flank_start_r > ded_r + 1e-9:
            root_ang_l = tooth_center + flank_shift
            all_points.append(
                (ded_r * math.cos(root_ang_l), ded_r * math.sin(root_ang_l))
            )

        next_center = (k + 1) * angular_pitch
        left_root_ang = tooth_center + flank_shift
        next_right_root_ang = next_center - flank_shift
        if next_right_root_ang < left_root_ang:
            next_right_root_ang += 2.0 * math.pi
        arc_pts = emit_arc(ded_r, left_root_ang, next_right_root_ang, 4)
        all_points.extend(arc_pts[1:-1])

    cleaned: list[tuple[float, float]] = []
    for p in all_points:
        if cleaned and (
            abs(cleaned[-1][0] - p[0]) < 1e-6
            and abs(cleaned[-1][1] - p[1]) < 1e-6
        ):
            continue
        cleaned.append(p)
    return cleaned


def _slab_from_outline(
    pts: list[tuple[float, float]],
    twist_angle: float,
    z0: float,
    dz: float,
):
    """Build one planar prism from the rotated transverse outline, extruded
    from z=z0 to z=z0+dz."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    c, s = math.cos(twist_angle), math.sin(twist_angle)
    poly = BRepBuilderAPI_MakePolygon()
    for (x, y) in pts:
        xr = c * x - s * y
        yr = s * x + c * y
        poly.Add(gp_Pnt(xr, yr, z0))
    poly.Close()
    poly.Build()
    if not poly.IsDone():
        raise RuntimeError("gear_helical_involute: slab polygon failed")
    wire = poly.Wire()
    mf = BRepBuilderAPI_MakeFace(wire, True)
    mf.Build()
    if not mf.IsDone():
        raise RuntimeError("gear_helical_involute: slab face failed")
    prism = BRepPrimAPI_MakePrism(mf.Face(), gp_Vec(0.0, 0.0, dz))
    prism.Build()
    if not prism.IsDone():
        raise RuntimeError("gear_helical_involute: slab prism failed")
    return prism.Shape()


@skill(
    name="gear_helical_involute",
    category="create",
    level="atomic",
    summary="Helical external spur gear with kinematically correct involute "
            "tooth profile and constant helix-angle twist. Standard module / "
            "pressure-angle / helix-angle parameterization (ISO).",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["helical_gear_solid", "involute_teeth", "helix_twist"],
    preserves=[],
    manufacturing={
        "cnc_5axis":         {"min_wall_mm": 0.5,
                              "extras": {"helical_hob_required": True}},
        "hobbing":           {"min_wall_mm": 0.4},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"side_action_required": True}},
    },
    failure_modes=[
        "fm.gear_n_teeth_too_low",
        "fm.gear_module_invalid",
        "fm.helix_angle_too_large",
    ],
    cost_hint=0.35,
    post_conditions=[PostCondition(kind="body_present")],
)
class GearHelicalInvolute(SkillBase):
    class Args(BaseModel):
        module_mm: float = Field(default=1.0, gt=0.0)
        teeth: int = Field(default=20, ge=8)
        helix_angle_deg: float = Field(default=15.0, ge=0.0, lt=60.0)
        face_width_mm: float = Field(default=10.0, gt=0.0)
        pressure_angle_deg: float = Field(default=20.0, gt=0.0, lt=45.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from build123d import Part

        m = args.module_mm
        z = args.teeth
        alpha = math.radians(args.pressure_angle_deg)
        beta = math.radians(args.helix_angle_deg)
        W = args.face_width_mm

        pitch_r = m * z / 2.0
        if pitch_r < 1e-6:
            raise ValueError("gear_helical_involute: degenerate pitch radius")

        # Total axial twist over the face width.
        twist_total = (W * math.tan(beta)) / pitch_r if beta > 0.0 else 0.0

        # Number of slabs. For zero helix, a single slab is fine.
        if abs(twist_total) < 1e-6:
            n_slabs = 1
        else:
            # Cap angular increment per slab to about 3° for a smooth flank.
            max_step_rad = math.radians(3.0)
            n_slabs = max(int(math.ceil(abs(twist_total) / max_step_rad)), 6)
            # Hard upper cap to keep test runtime bounded.
            n_slabs = min(n_slabs, 24)

        outline = _build_transverse_outline_points(m, z, alpha)

        dz = W / n_slabs
        accum = None
        for i in range(n_slabs):
            z0 = i * dz
            twist = (z0 + dz * 0.5) * (math.tan(beta) / pitch_r) if beta > 0 else 0.0
            slab = _slab_from_outline(outline, twist, z0, dz)
            if accum is None:
                accum = slab
            else:
                fuse = BRepAlgoAPI_Fuse(accum, slab)
                fuse.Build()
                if not fuse.IsDone():
                    raise RuntimeError("gear_helical_involute: slab fuse failed")
                accum = fuse.Shape()

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(accum), history=history)
