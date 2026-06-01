"""speaker_baffle_window — atomic. Stepped speaker baffle cutout.

Cuts a rounded-rectangle window through the host wall for a loudspeaker, then
adds a shallow recess (step) around the window inset by
`grille_pattern_inset_mm` to seat a perforated grille / mesh plate flush with
the outer surface.

Geometry:
  1. Outer step  — rounded-rectangle pocket at (baffle_w + 2*inset)
                   × (baffle_h + 2*inset), depth = inset_depth (small).
  2. Inner cutout — rounded-rectangle THROUGH-cut at baffle_w × baffle_h.

Both stages share the same outer corner radius `corner_r_mm`. The window cuts
all the way through the wall (depth set from the body bounding box). v1
supports planar +Z/-Z target faces.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._resolvers import (
    _face_center,
    _face_normal_at_center,
    resolve_faces,
)
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="speaker_baffle_window",
    category="modify/pocket",
    level="atomic",
    summary="Stepped speaker baffle cutout: rounded-rectangle through-window "
            "for the driver plus a shallow surrounding recess to seat a "
            "perforated grille flush with the outer wall. v1 supports "
            "planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "grille_recess":   HistoryRule.GENERATED_NEW,
        "baffle_window":   HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.corner_r_le_min_half_side",
    ],
    produces_features=["speaker_baffle_window", "grille_seat_recess"],
    preserves=["outer_envelope_outside_baffle"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.6, "min_fillet_r_mm": 0.3,
                              "extras": {"max_aspect_ratio": 6.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "min_fillet_r_mm": 0.5},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "min_fillet_r_mm": 0.3},
    },
    failure_modes=[
        "fm.corner_r_too_large",
        "fm.window_exits_body",
    ],
    cost_hint=0.24,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class SpeakerBaffleWindow(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        baffle_w_mm: float = Field(gt=0, le=500)
        baffle_h_mm: float = Field(gt=0, le=500)
        corner_r_mm: float = Field(default=2.0, ge=0, le=100)
        grille_pattern_inset_mm: float = Field(default=2.0, ge=0, le=50)
        grille_recess_depth_mm: float = Field(default=1.2, gt=0, le=20)
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of window centre",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        # Geometry sanity: corner_r must fit inside the smallest half-side of
        # both rectangles, and the recess must be larger than the through cut.
        min_half = 0.5 * min(args.baffle_w_mm, args.baffle_h_mm)
        if args.corner_r_mm >= min_half:
            raise ValueError(
                f"speaker_baffle_window: corner_r ({args.corner_r_mm}) must be "
                f"< half min-side ({min_half:.3f})"
            )

        outer_w = args.baffle_w_mm + 2.0 * args.grille_pattern_inset_mm
        outer_h = args.baffle_h_mm + 2.0 * args.grille_pattern_inset_mm

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"speaker_baffle_window: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "speaker_baffle_window v1: planar +Z/-Z target faces only",
            )
        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]

        # Through-depth: probe the body bounding box along Z and use total
        # Z extent + a small overshoot so the inner cutout fully clears the
        # wall regardless of thickness.
        bb = Bnd_Box()
        BRepBndLib.Add_s(shape, bb)
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        through_depth = (zmax - zmin) + 4.0

        # Stage 1: grille recess (outer rounded-rect) — shallow.
        recess_tool = _make_rounded_rect_prism(
            cx, cy, center[2], nz_sign,
            outer_w, outer_h, args.corner_r_mm,
            args.grille_recess_depth_mm,
        )
        cut_a = BRepAlgoAPI_Cut(shape, recess_tool)
        cut_a.Build()
        if not cut_a.IsDone():
            raise RuntimeError(
                "speaker_baffle_window: grille recess cut failed",
            )
        new_shape = cut_a.Shape()

        # Stage 2: inner through cutout (baffle window) — full through-cut.
        # Push the start point slightly out along +normal so the prism crosses
        # the face cleanly.
        overshoot = 1.0
        start_z = center[2] + nz_sign * overshoot
        win_tool = _make_rounded_rect_prism(
            cx, cy, start_z, nz_sign,
            args.baffle_w_mm, args.baffle_h_mm,
            max(args.corner_r_mm - args.grille_pattern_inset_mm, 0.05),
            through_depth + overshoot,
        )
        cut_b = BRepAlgoAPI_Cut(new_shape, win_tool)
        cut_b.Build()
        if not cut_b.IsDone():
            raise RuntimeError(
                "speaker_baffle_window: window through-cut failed",
            )
        new_shape = cut_b.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "grille_recess":   HistoryRule.GENERATED_NEW,
                "baffle_window":   HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_rounded_rect_prism(
    cx: float, cy: float, z_top: float, nz_sign: float,
    w: float, h: float, r: float, depth: float,
):
    """Build a rounded-rectangle prism (TopoDS_Solid) anchored at (cx, cy, z_top),
    extruding by `depth` in -nz_sign direction.

    The rounded-rectangle wire is built explicitly in the XY plane (z = z_top),
    then prismed along Z.
    """
    import math

    from OCP.BRep import BRep_Tool  # noqa: F401
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.GC import GC_MakeArcOfCircle, GC_MakeSegment
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt, gp_Vec

    half_w = w / 2.0
    half_h = h / 2.0
    r = max(min(r, half_w - 1e-4, half_h - 1e-4), 0.0)

    # Corner centres (clockwise from +X / +Y):
    # P_TR = (cx+half_w-r, cy+half_h-r)
    # P_TL = (cx-half_w+r, cy+half_h-r)
    # P_BL = (cx-half_w+r, cy-half_h+r)
    # P_BR = (cx+half_w-r, cy-half_h+r)
    z = z_top

    if r > 1e-4:
        # Eight points around the rounded rectangle.
        p_t_r = gp_Pnt(cx + half_w - r, cy + half_h, z)  # top edge - right end
        p_t_l = gp_Pnt(cx - half_w + r, cy + half_h, z)  # top edge - left end
        p_l_t = gp_Pnt(cx - half_w, cy + half_h - r, z)  # left edge - top
        p_l_b = gp_Pnt(cx - half_w, cy - half_h + r, z)  # left edge - bot
        p_b_l = gp_Pnt(cx - half_w + r, cy - half_h, z)  # bot edge - left
        p_b_r = gp_Pnt(cx + half_w - r, cy - half_h, z)  # bot edge - right
        p_r_b = gp_Pnt(cx + half_w, cy - half_h + r, z)  # right edge - bot
        p_r_t = gp_Pnt(cx + half_w, cy + half_h - r, z)  # right edge - top

        # Arc midpoints (45° from each corner centre) for GC_MakeArcOfCircle's
        # 3-point form.
        m = r * math.sqrt(0.5)
        a_tr_mid = gp_Pnt(cx + half_w - r + m, cy + half_h - r + m, z)
        a_tl_mid = gp_Pnt(cx - half_w + r - m, cy + half_h - r + m, z)
        a_bl_mid = gp_Pnt(cx - half_w + r - m, cy - half_h + r - m, z)
        a_br_mid = gp_Pnt(cx + half_w - r + m, cy - half_h + r - m, z)

        # Edges (counter-clockwise around the boundary).
        e_top = BRepBuilderAPI_MakeEdge(
            GC_MakeSegment(p_t_l, p_t_r).Value()
        ).Edge()
        e_arc_tr = BRepBuilderAPI_MakeEdge(
            GC_MakeArcOfCircle(p_t_r, a_tr_mid, p_r_t).Value()
        ).Edge()
        e_right = BRepBuilderAPI_MakeEdge(
            GC_MakeSegment(p_r_t, p_r_b).Value()
        ).Edge()
        e_arc_br = BRepBuilderAPI_MakeEdge(
            GC_MakeArcOfCircle(p_r_b, a_br_mid, p_b_r).Value()
        ).Edge()
        e_bot = BRepBuilderAPI_MakeEdge(
            GC_MakeSegment(p_b_r, p_b_l).Value()
        ).Edge()
        e_arc_bl = BRepBuilderAPI_MakeEdge(
            GC_MakeArcOfCircle(p_b_l, a_bl_mid, p_l_b).Value()
        ).Edge()
        e_left = BRepBuilderAPI_MakeEdge(
            GC_MakeSegment(p_l_b, p_l_t).Value()
        ).Edge()
        e_arc_tl = BRepBuilderAPI_MakeEdge(
            GC_MakeArcOfCircle(p_l_t, a_tl_mid, p_t_l).Value()
        ).Edge()

        # Wire — note: order must match endpoint chain for MakeWire.
        wb = BRepBuilderAPI_MakeWire()
        for e in (e_top, e_arc_tr, e_right, e_arc_br,
                  e_bot, e_arc_bl, e_left, e_arc_tl):
            wb.Add(e)
        wire = wb.Wire()
    else:
        # Sharp-corner rectangle.
        p1 = gp_Pnt(cx - half_w, cy - half_h, z)
        p2 = gp_Pnt(cx + half_w, cy - half_h, z)
        p3 = gp_Pnt(cx + half_w, cy + half_h, z)
        p4 = gp_Pnt(cx - half_w, cy + half_h, z)
        e1 = BRepBuilderAPI_MakeEdge(GC_MakeSegment(p1, p2).Value()).Edge()
        e2 = BRepBuilderAPI_MakeEdge(GC_MakeSegment(p2, p3).Value()).Edge()
        e3 = BRepBuilderAPI_MakeEdge(GC_MakeSegment(p3, p4).Value()).Edge()
        e4 = BRepBuilderAPI_MakeEdge(GC_MakeSegment(p4, p1).Value()).Edge()
        wb = BRepBuilderAPI_MakeWire()
        wb.Add(e1); wb.Add(e2); wb.Add(e3); wb.Add(e4)  # noqa: E702
        wire = wb.Wire()

    face = BRepBuilderAPI_MakeFace(wire, True).Face()

    extrude_vec = gp_Vec(0.0, 0.0, -nz_sign * depth)
    prism = BRepPrimAPI_MakePrism(face, extrude_vec, True, True)
    if not prism.IsDone():
        raise RuntimeError(
            "speaker_baffle_window: rounded-rect prism build failed",
        )
    return prism.Shape()
