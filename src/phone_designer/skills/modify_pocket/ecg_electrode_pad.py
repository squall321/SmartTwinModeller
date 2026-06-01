"""ecg_electrode_pad — atomic.

Recessed circular pad for a dry ECG electrode on a host face plus a
side-exit channel for the lead wire. A two-step circular recess sinks the
electrode into the body, with a small `body_contact_step_mm` shoulder so
that the electrode rim sits proud of the surrounding body and contacts
skin. A narrow rectangular channel of width `lead_wire_channel_w_mm` runs
from the pad rim to the +X edge of the face along the surface plane,
clearing space for the flat flex-cable lead.

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
    name="ecg_electrode_pad",
    category="modify/pocket",
    level="atomic",
    summary="Recessed circular ECG electrode pad + side-exit lead-wire channel. "
            "Two-step cylindrical recess: outer rim at body_contact_step_mm "
            "depth and inner electrode well at electrode_d_mm full depth, "
            "with a rectangular lead-wire channel of width "
            "lead_wire_channel_w_mm running radially outward.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":      HistoryRule.MODIFIED_INHERIT,
        "pad_recess_wall":  HistoryRule.GENERATED_NEW,
        "pad_recess_floor": HistoryRule.GENERATED_NEW,
        "lead_channel":     HistoryRule.GENERATED_NEW,
        "consumed_volume":  HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["ecg_electrode_pad"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"flat_floor_required": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.channel_wider_than_electrode",
        "fm.pocket_exits_body",
    ],
    cost_hint=0.16,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class EcgElectrodePad(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of pad center, offset from face centroid.",
        )
        electrode_d_mm: float = Field(default=12.0, gt=0, le=80,
                                       description="electrode disc diameter")
        body_contact_step_mm: float = Field(default=0.3, gt=0, le=5.0,
                                             description="rim recess depth (skin-side step)")
        lead_wire_channel_w_mm: float = Field(default=1.5, gt=0, le=20,
                                               description="lead-wire side channel width")
        channel_length_mm: float = Field(
            default=10.0, gt=0, le=200,
            description="length of the side-exit channel measured outward from pad center",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        if args.lead_wire_channel_w_mm >= args.electrode_d_mm:
            raise ValueError(
                f"ecg_electrode_pad: lead_wire_channel_w_mm "
                f"({args.lead_wire_channel_w_mm}) >= electrode_d_mm "
                f"({args.electrode_d_mm}) — channel wider than electrode"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"ecg_electrode_pad: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "ecg_electrode_pad v1: planar +Z/-Z target faces only"
            )
        nz_sign = 1.0 if normal[2] > 0 else -1.0
        axis_dir = gp_Dir(0.0, 0.0, -nz_sign)

        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        cz = center[2]

        # Stage 1 — circular electrode well (full electrode diameter, depth = step).
        well_base = gp_Pnt(cx, cy, cz)
        well_ax = gp_Ax2(well_base, axis_dir)
        well_mk = BRepPrimAPI_MakeCylinder(
            well_ax, args.electrode_d_mm / 2.0, args.body_contact_step_mm,
        )
        well_mk.Build()
        if not well_mk.IsDone():
            raise RuntimeError("ecg_electrode_pad: electrode well build failed")
        cut_a = BRepAlgoAPI_Cut(shape, well_mk.Shape())
        cut_a.Build()
        if not cut_a.IsDone():
            raise RuntimeError("ecg_electrode_pad: well boolean cut failed")
        new_shape = cut_a.Shape()

        # Stage 2 — radial side-exit lead-wire channel (rectangular).
        # Channel runs +X from pad edge, centered on Y, depth = step.
        ch_x0 = cx - args.lead_wire_channel_w_mm / 2.0  # not used (we cut full length)
        ch_origin = gp_Pnt(
            cx,  # start at pad center, extend +X for channel_length
            cy - args.lead_wire_channel_w_mm / 2.0,
            cz - (args.body_contact_step_mm if nz_sign > 0 else 0.0),
        )
        ch_mk = BRepPrimAPI_MakeBox(
            ch_origin,
            args.channel_length_mm,
            args.lead_wire_channel_w_mm,
            args.body_contact_step_mm,
        )
        ch_mk.Build()
        if not ch_mk.IsDone():
            raise RuntimeError("ecg_electrode_pad: channel box build failed")
        cut_b = BRepAlgoAPI_Cut(new_shape, ch_mk.Shape())
        cut_b.Build()
        if not cut_b.IsDone():
            raise RuntimeError("ecg_electrode_pad: channel boolean cut failed")
        new_shape = cut_b.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":      HistoryRule.MODIFIED_INHERIT,
                "pad_recess_wall":  HistoryRule.GENERATED_NEW,
                "pad_recess_floor": HistoryRule.GENERATED_NEW,
                "lead_channel":     HistoryRule.GENERATED_NEW,
                "consumed_volume":  HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
