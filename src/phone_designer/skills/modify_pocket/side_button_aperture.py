"""side_button_aperture — atomic.

Rounded-rectangular aperture cut through the side wall of a phone housing
for a side button (power, volume up/down). The aperture is a stadium-shape
slot of length `button_l_mm`, width `button_w_mm`, with quarter-round corners
of radius `corner_r_mm`.

Geometry (raw OCCT, ±X or ±Y planar host faces v1):
    A rounded-rectangle profile (in the face plane: long axis along world Z,
    width axis along the in-plane non-normal world axis) is built as
        center box (L × (W-2r))   ∪
        side strip ((L-2r) × W)   ∪
        4× corner cylinders r
    and extruded along the face normal far enough to traverse the wall.
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
    name="side_button_aperture",
    category="modify/pocket",
    level="atomic",
    summary="Rounded-rectangular side-button aperture cut through the housing "
            "side wall. v1 supports planar ±X / ±Y host faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":          HistoryRule.MODIFIED_INHERIT,
        "side_button_aperture": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["side_button_aperture"],
    preserves=["outer_envelope_outside_aperture"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3, "min_fillet_r_mm": 0.2},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_button_fit": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.button_aperture_radius_too_large",
        "fm.button_aperture_does_not_traverse",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class SideButtonAperture(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_on_edge_mm: float = Field(
            default=0.0,
            description="face-local offset along the long axis of the stadium "
                        "(world Z) from the face center",
        )
        button_l_mm: float = Field(default=18.0, gt=0, le=80,
                                    description="aperture overall length "
                                                "(along world Z)")
        button_w_mm: float = Field(default=2.5, gt=0, le=20,
                                    description="aperture overall width "
                                                "(in-plane perpendicular)")
        corner_r_mm: float = Field(default=0.4, gt=0, le=10,
                                    description="corner fillet radius")
        depth_through: bool = Field(default=True,
                                     description="True = through-hole; "
                                                 "False reserved for blind "
                                                 "(not yet implemented)")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        if 2.0 * args.corner_r_mm > args.button_w_mm:
            raise ValueError(
                f"side_button_aperture: 2*corner_r_mm ({2*args.corner_r_mm}) "
                f"cannot exceed button_w_mm ({args.button_w_mm})"
            )
        if 2.0 * args.corner_r_mm > args.button_l_mm:
            raise ValueError(
                f"side_button_aperture: 2*corner_r_mm ({2*args.corner_r_mm}) "
                f"cannot exceed button_l_mm ({args.button_l_mm})"
            )
        if not args.depth_through:
            raise NotImplementedError(
                "side_button_aperture v1: depth_through=False (blind) not "
                "implemented; pass depth_through=True"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"side_button_aperture: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        # Determine wall axis: ±X or ±Y.
        if abs(normal[0]) > 0.9:
            wall_axis = "X"
        elif abs(normal[1]) > 0.9:
            wall_axis = "Y"
        else:
            raise NotImplementedError(
                "side_button_aperture v1: planar ±X / ±Y target faces only"
            )

        cx, cy, cz = center
        L = args.button_l_mm
        W = args.button_w_mm
        r = args.corner_r_mm
        # Through depth is the full wall traverse (anchored at face center,
        # extending generously in both directions of the wall normal so the
        # boolean cut traverses the whole body irrespective of thickness).
        through = 200.0

        # Slot long axis is world Z; offset by position_on_edge_mm.
        slot_z_center = cz + args.position_on_edge_mm
        z_lo = slot_z_center - L / 2.0
        z_hi = slot_z_center + L / 2.0

        # In-plane perpendicular axis: world Y if wall_axis=X, else world X.
        # Through extent along the wall normal axis.
        if wall_axis == "X":
            t_lo = cx - through
            t_hi = cx + through
        else:
            t_lo = cy - through
            t_hi = cy + through

        def make_box(z_a, z_b, ip_a, ip_b):
            if wall_axis == "X":
                lo = gp_Pnt(t_lo, ip_a, z_a)
                hi = gp_Pnt(t_hi, ip_b, z_b)
            else:
                lo = gp_Pnt(ip_a, t_lo, z_a)
                hi = gp_Pnt(ip_b, t_hi, z_b)
            mk = BRepPrimAPI_MakeBox(lo, hi)
            mk.Build()
            if not mk.IsDone():
                raise RuntimeError("side_button_aperture: box build failed")
            return mk.Shape()

        def make_corner_cyl(zc, ipc):
            # Cylinder of radius r, axis along ±wall_axis (the wall normal),
            # spanning the full through traverse so the boolean cut is clean.
            if wall_axis == "X":
                base = gp_Pnt(t_lo, ipc, zc)
                ax = gp_Ax2(base, gp_Dir(1.0, 0.0, 0.0))
            else:
                base = gp_Pnt(ipc, t_lo, zc)
                ax = gp_Ax2(base, gp_Dir(0.0, 1.0, 0.0))
            mk = BRepPrimAPI_MakeCylinder(ax, r, 2.0 * through)
            mk.Build()
            if not mk.IsDone():
                raise RuntimeError("side_button_aperture: corner cyl failed")
            return mk.Shape()

        # Rounded rectangle = central box (full L, width W - 2r) ∪
        #                     side strip (length L - 2r, full W) ∪
        #                     4 corner cylinders of radius r.
        if wall_axis == "X":
            box1 = make_box(z_lo, z_hi, cy - (W / 2.0 - r), cy + (W / 2.0 - r))
            box2 = make_box(slot_z_center - (L / 2.0 - r),
                             slot_z_center + (L / 2.0 - r),
                             cy - W / 2.0, cy + W / 2.0)
        else:
            box1 = make_box(z_lo, z_hi, cx - (W / 2.0 - r), cx + (W / 2.0 - r))
            box2 = make_box(slot_z_center - (L / 2.0 - r),
                             slot_z_center + (L / 2.0 - r),
                             cx - W / 2.0, cx + W / 2.0)

        f1 = BRepAlgoAPI_Fuse(box1, box2)
        f1.Build()
        if not f1.IsDone():
            raise RuntimeError("side_button_aperture: box fuse failed")
        tool_shape = f1.Shape()

        # Four corner offsets in (z, in-plane) face coordinates.
        ip_center = cy if wall_axis == "X" else cx
        corner_offsets = [
            (slot_z_center + (L / 2.0 - r), ip_center + (W / 2.0 - r)),
            (slot_z_center + (L / 2.0 - r), ip_center - (W / 2.0 - r)),
            (slot_z_center - (L / 2.0 - r), ip_center + (W / 2.0 - r)),
            (slot_z_center - (L / 2.0 - r), ip_center - (W / 2.0 - r)),
        ]
        for zc, ipc in corner_offsets:
            cyl = make_corner_cyl(zc, ipc)
            fuse = BRepAlgoAPI_Fuse(tool_shape, cyl)
            fuse.Build()
            if not fuse.IsDone():
                raise RuntimeError("side_button_aperture: corner fuse failed")
            tool_shape = fuse.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool_shape)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("side_button_aperture: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":          HistoryRule.MODIFIED_INHERIT,
                "side_button_aperture": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
