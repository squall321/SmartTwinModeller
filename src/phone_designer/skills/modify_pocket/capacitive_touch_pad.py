"""capacitive_touch_pad — atomic.

Recess for a capacitive-touch sensor PCB on a host face. Carves a shallow
rectangular pocket sized to (region_w_mm, region_h_mm) and the PCB stack
thickness (here mapped to `copper_clearance_mm` as the thin air-gap behind
the copper electrodes). Additionally subtracts a sparse grid of tiny round
clearance grooves under each electrode position so that the electrode
copper pads do not touch the bottom of the pocket, preserving capacitive
isolation distance.

The electrode grid is laid out on a regular pitch (electrode_pitch_mm) and
constrained to fit fully inside (region_w_mm × region_h_mm). Each electrode
clearance groove is a cylinder of diameter `electrode_d_mm` whose depth is
`copper_clearance_mm`, sunk an additional `copper_clearance_mm` below the
pocket floor.

v1 supports planar +Z / -Z target faces.
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
    name="capacitive_touch_pad",
    category="modify/pocket",
    level="atomic",
    summary="Recess for a capacitive-touch sensor PCB. Shallow rectangular "
            "pocket sized to (region_w_mm × region_h_mm) at depth "
            "copper_clearance_mm, plus a grid of small circular clearance "
            "grooves at every electrode position (electrode_d_mm on "
            "electrode_pitch_mm). v1 supports planar +Z/-Z faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "pad_walls":       HistoryRule.GENERATED_NEW,
        "pad_floor":       HistoryRule.GENERATED_NEW,
        "electrode_holes": HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
        "pc.region_inside_face",
    ],
    produces_features=["capacitive_touch_pad"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"flat_floor_required": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.electrode_pitch_smaller_than_diameter",
        "fm.region_smaller_than_electrode",
        "fm.pocket_exits_body",
    ],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class CapacitiveTouchPad(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        region_w_mm: float = Field(gt=0, le=200,
                                    description="pad region X (length)")
        region_h_mm: float = Field(gt=0, le=200,
                                    description="pad region Y (width)")
        electrode_d_mm: float = Field(default=8.0, gt=0, le=50,
                                       description="electrode pad diameter")
        electrode_pitch_mm: float = Field(default=10.0, gt=0, le=100,
                                           description="grid pitch (X and Y)")
        copper_clearance_mm: float = Field(default=0.3, gt=0, le=5.0,
                                            description="clearance/recess depth")
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of pad center, offset from face centroid.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        if args.electrode_pitch_mm < args.electrode_d_mm:
            raise ValueError(
                f"capacitive_touch_pad: electrode_pitch_mm "
                f"({args.electrode_pitch_mm}) < electrode_d_mm "
                f"({args.electrode_d_mm}) — pads would overlap"
            )
        if args.region_w_mm < args.electrode_d_mm or args.region_h_mm < args.electrode_d_mm:
            raise ValueError(
                f"capacitive_touch_pad: region "
                f"({args.region_w_mm}x{args.region_h_mm}) smaller than one "
                f"electrode_d_mm ({args.electrode_d_mm})"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"capacitive_touch_pad: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "capacitive_touch_pad v1: planar +Z/-Z target faces only"
            )
        nz_sign = 1.0 if normal[2] > 0 else -1.0
        axis_dir = gp_Dir(0.0, 0.0, -nz_sign)

        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        cz = center[2]

        # Stage 1 — outer rectangular pocket (region_w × region_h × clearance)
        pocket_origin = gp_Pnt(
            cx - args.region_w_mm / 2.0,
            cy - args.region_h_mm / 2.0,
            cz - (args.copper_clearance_mm if nz_sign > 0 else 0.0),
        )
        pocket_mk = BRepPrimAPI_MakeBox(
            pocket_origin,
            args.region_w_mm,
            args.region_h_mm,
            args.copper_clearance_mm,
        )
        pocket_mk.Build()
        if not pocket_mk.IsDone():
            raise RuntimeError("capacitive_touch_pad: pocket box build failed")
        cut_a = BRepAlgoAPI_Cut(shape, pocket_mk.Shape())
        cut_a.Build()
        if not cut_a.IsDone():
            raise RuntimeError("capacitive_touch_pad: pocket boolean cut failed")
        new_shape = cut_a.Shape()

        # Stage 2 — electrode clearance grooves on a centered grid.
        usable_w = args.region_w_mm - args.electrode_d_mm
        usable_h = args.region_h_mm - args.electrode_d_mm
        nx = int(usable_w // args.electrode_pitch_mm) + 1
        ny = int(usable_h // args.electrode_pitch_mm) + 1
        grid_span_x = (nx - 1) * args.electrode_pitch_mm
        grid_span_y = (ny - 1) * args.electrode_pitch_mm
        x0 = cx - grid_span_x / 2.0
        y0 = cy - grid_span_y / 2.0
        # Each groove sinks an extra copper_clearance below the pocket floor.
        groove_top_z = cz - nz_sign * args.copper_clearance_mm
        for ix in range(nx):
            for iy in range(ny):
                px = x0 + ix * args.electrode_pitch_mm
                py = y0 + iy * args.electrode_pitch_mm
                base = gp_Pnt(px, py, groove_top_z)
                ax2 = gp_Ax2(base, axis_dir)
                groove_mk = BRepPrimAPI_MakeCylinder(
                    ax2, args.electrode_d_mm / 2.0, args.copper_clearance_mm,
                )
                groove_mk.Build()
                if not groove_mk.IsDone():
                    raise RuntimeError(
                        f"capacitive_touch_pad: groove build failed at ({ix},{iy})"
                    )
                cut_b = BRepAlgoAPI_Cut(new_shape, groove_mk.Shape())
                cut_b.Build()
                if not cut_b.IsDone():
                    raise RuntimeError(
                        f"capacitive_touch_pad: groove cut failed at ({ix},{iy})"
                    )
                new_shape = cut_b.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "pad_walls":       HistoryRule.GENERATED_NEW,
                "pad_floor":       HistoryRule.GENERATED_NEW,
                "electrode_holes": HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
