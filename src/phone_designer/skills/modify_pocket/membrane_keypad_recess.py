"""membrane_keypad_recess — atomic.

A shallow stepped rectangular recess on a planar +Z/-Z host face that seats a
medical-device membrane keypad (PET / polyester overlay) flush with the body
surface. The recess is purposely SHALLOW (default 0.25 mm) — enough for the
overlay thickness so the keypad sits flush and can be wiped down without
catching debris (cleanability).

Geometry:
  - rectangular pocket of (length_mm × width_mm) on the target face,
  - depth = recess_d_mm (typ. 0.20–0.50 mm),
  - corners filleted with corner_r_mm to ease cleaning (no sharp internal
    90° corners — IEC 60601-1 cleanability hint).

v1: planar ±Z target faces only.
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
    name="membrane_keypad_recess",
    category="modify/pocket",
    level="atomic",
    summary="Shallow stepped rectangular recess on a planar host face that seats "
            "a medical-device membrane keypad flush with the body. Corners are "
            "filleted for cleanability. v1 supports planar ±Z faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "recess_walls":    HistoryRule.GENERATED_NEW,
        "recess_floor":    HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["membrane_keypad_recess", "shallow_pocket"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3, "min_fillet_r_mm": 0.5,
                              "extras": {"flatness_mm": 0.05,
                                         "cleanability": "wipe_down"}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_flatness": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"medical_grade": True}},
    },
    failure_modes=[
        "fm.recess_outside_face",
        "fm.corner_radius_too_large",
    ],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class MembraneKeypadRecess(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        length_mm: float = Field(gt=0, le=400,
                                  description="recess outer length (X)")
        width_mm: float = Field(gt=0, le=400,
                                 description="recess outer width (Y)")
        recess_d_mm: float = Field(default=0.25, gt=0, le=5.0,
                                    description="recess depth — typ. overlay thickness")
        corner_r_mm: float = Field(default=2.0, ge=0.0, le=20.0,
                                    description="corner fillet radius — cleanability aid")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeEdge,
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakeWire,
        )
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakePrism
        from OCP.GC import GC_MakeArcOfCircle, GC_MakeSegment
        from OCP.gp import gp_Dir, gp_Pnt, gp_Vec

        # Corner radius must fit inside the rectangle.
        max_r = min(args.length_mm, args.width_mm) / 2.0
        if args.corner_r_mm >= max_r:
            raise ValueError(
                f"membrane_keypad_recess: corner_r_mm ({args.corner_r_mm}) too "
                f"large for {args.length_mm} x {args.width_mm} (max < {max_r:.3f})"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"membrane_keypad_recess: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "membrane_keypad_recess v1: planar ±Z faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        cx, cy = center[0], center[1]
        face_z = center[2]

        hl = args.length_mm / 2.0
        hw = args.width_mm / 2.0
        r = args.corner_r_mm
        overshoot = 0.05

        # Build the tool. When corner_r > 0 we build a rounded rectangle as a
        # wire then prism; when r == 0 we just use a box (faster, robust).
        if r <= 1e-6:
            zmin = min(face_z, face_z - z_sign * args.recess_d_mm)
            zmax = max(face_z, face_z - z_sign * args.recess_d_mm)
            zmin_o = zmin - overshoot if z_sign < 0 else zmin
            zmax_o = zmax + overshoot if z_sign > 0 else zmax
            box_lo = gp_Pnt(cx - hl, cy - hw, zmin_o)
            box_hi = gp_Pnt(cx + hl, cy + hw, zmax_o)
            box_mk = BRepPrimAPI_MakeBox(box_lo, box_hi)
            box_mk.Build()
            if not box_mk.IsDone():
                raise RuntimeError(
                    "membrane_keypad_recess: tool box build failed"
                )
            tool = box_mk.Shape()
        else:
            # Rounded-rect wire at z = face_z + z_sign*overshoot (slightly
            # above the face) so the prism opens cleanly through the face.
            wz = face_z + z_sign * overshoot
            # Eight key points: four arc-tangency points (start/end) per corner.
            #   straight segment along +X bottom: from (-hl+r, -hw) to (hl-r, -hw)
            #   bottom-right arc: from (hl-r, -hw) to (hl, -hw+r) centered (hl-r, -hw+r)
            #   right segment: (hl, -hw+r) to (hl, hw-r)
            #   top-right arc: (hl, hw-r) → (hl-r, hw) centered (hl-r, hw-r)
            #   top segment: (hl-r, hw) → (-hl+r, hw)
            #   top-left arc: (-hl+r, hw) → (-hl, hw-r) centered (-hl+r, hw-r)
            #   left segment: (-hl, hw-r) → (-hl, -hw+r)
            #   bottom-left arc: (-hl, -hw+r) → (-hl+r, -hw) centered (-hl+r, -hw+r)
            def P(x, y):
                return gp_Pnt(cx + x, cy + y, wz)

            seg_specs = [
                ("seg", P(-hl + r, -hw), P(hl - r, -hw)),
                ("arc", P(hl - r, -hw), P(hl, -hw + r), P(hl - r, -hw + r)),
                ("seg", P(hl, -hw + r), P(hl, hw - r)),
                ("arc", P(hl, hw - r), P(hl - r, hw), P(hl - r, hw - r)),
                ("seg", P(hl - r, hw), P(-hl + r, hw)),
                ("arc", P(-hl + r, hw), P(-hl, hw - r), P(-hl + r, hw - r)),
                ("seg", P(-hl, hw - r), P(-hl, -hw + r)),
                ("arc", P(-hl, -hw + r), P(-hl + r, -hw), P(-hl + r, -hw + r)),
            ]

            wire_mk = BRepBuilderAPI_MakeWire()
            for spec in seg_specs:
                if spec[0] == "seg":
                    _, p1, p2 = spec
                    seg = GC_MakeSegment(p1, p2).Value()
                    em = BRepBuilderAPI_MakeEdge(seg)
                    em.Build()
                    if not em.IsDone():
                        raise RuntimeError(
                            "membrane_keypad_recess: segment edge build failed"
                        )
                    wire_mk.Add(em.Edge())
                else:
                    _, p1, p2, cpt = spec
                    # 3-pt arc using the mid-arc point at 45°
                    mx = cpt.X() + (p1.X() - cpt.X()) * 0.7071067811865 \
                        + (p2.X() - cpt.X()) * 0.7071067811865
                    my = cpt.Y() + (p1.Y() - cpt.Y()) * 0.7071067811865 \
                        + (p2.Y() - cpt.Y()) * 0.7071067811865
                    # Linear-blend the mid is wrong — we need the arc midpoint
                    # at the circle. Use vector form: center + r * unit(p1-c
                    # rotated 45° toward p2). Simpler: param midpoint of arc.
                    # Compute via half-angle: midpoint = center + r * normalize(
                    #     (p1-c) + (p2-c)).
                    vx = (p1.X() - cpt.X()) + (p2.X() - cpt.X())
                    vy = (p1.Y() - cpt.Y()) + (p2.Y() - cpt.Y())
                    vlen = (vx * vx + vy * vy) ** 0.5
                    if vlen < 1e-9:
                        # 180°: pick perpendicular
                        vx, vy = -(p1.Y() - cpt.Y()), (p1.X() - cpt.X())
                        vlen = (vx * vx + vy * vy) ** 0.5
                    ux, uy = vx / vlen, vy / vlen
                    mx = cpt.X() + r * ux
                    my = cpt.Y() + r * uy
                    mid = gp_Pnt(mx, my, wz)
                    arc = GC_MakeArcOfCircle(p1, mid, p2).Value()
                    em = BRepBuilderAPI_MakeEdge(arc)
                    em.Build()
                    if not em.IsDone():
                        raise RuntimeError(
                            "membrane_keypad_recess: arc edge build failed"
                        )
                    wire_mk.Add(em.Edge())

            wire_mk.Build()
            if not wire_mk.IsDone():
                raise RuntimeError(
                    "membrane_keypad_recess: rounded-rect wire build failed"
                )
            wire = wire_mk.Wire()

            fmk = BRepBuilderAPI_MakeFace(wire, True)
            fmk.Build()
            if not fmk.IsDone():
                raise RuntimeError(
                    "membrane_keypad_recess: face build failed"
                )
            sketch_face = fmk.Face()

            # Prism into the body. Depth = recess_d_mm + overshoot so the cut
            # opens through the host face cleanly.
            prism_h = args.recess_d_mm + overshoot
            prism_vec = gp_Vec(0.0, 0.0, -z_sign * prism_h)
            pm = BRepPrimAPI_MakePrism(sketch_face, prism_vec)
            pm.Build()
            if not pm.IsDone():
                raise RuntimeError(
                    "membrane_keypad_recess: prism build failed"
                )
            tool = pm.Shape()
            # silence unused-import lint for gp_Dir which we may not use
            _ = gp_Dir

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError(
                "membrane_keypad_recess: boolean cut failed"
            )
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "recess_walls":    HistoryRule.GENERATED_NEW,
                "recess_floor":    HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
