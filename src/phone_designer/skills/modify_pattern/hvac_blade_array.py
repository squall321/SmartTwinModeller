"""hvac_blade_array — atomic. Louver-blade slot array on a planar face.

Models the blade stack of an HVAC vent (dash register, barrel diffuser) as
a linear array of thin rectangular slot pockets. Each slot represents one
louver vane, sized by `blade_length_mm × blade_width_mm × blade_thickness_mm`
and pitched along Y by `blade_pitch_mm`. At each end (±X) of every slot, a
small `pivot_d_mm` cylindrical hole is bored to host the vane's pivot pin.

v1 supports planar ±Z target faces.
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


def _load(family, name):
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


@skill(
    name="hvac_blade_array",
    category="modify/pattern",
    level="atomic",
    summary="Linear array of louver-blade slot pockets along Y, each with a pivot "
            "pin hole at the +X and -X end. Models the vane stack of an HVAC "
            "register or barrel vent. v1 supports planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "blade_slots":     HistoryRule.GENERATED_NEW,
        "pivot_holes":     HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.count_at_least_two",
    ],
    produces_features=["hvac_blade_array", "pivot_hole_array"],
    preserves=["outer_envelope"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"slot_min_width_mm": 0.8}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"end_mill_min_d_mm": 1.0}},
    },
    failure_modes=[
        "fm.blade_thickness_geq_pitch",
        "fm.array_exits_face",
        "fm.pivot_too_close_to_slot_edge",
    ],
    cost_hint=0.35,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class HvacBladeArray(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        blade_count: int = Field(default=10, ge=2, le=64)
        blade_length_mm: float = Field(default=80.0, gt=0, le=300)
        blade_width_mm: float = Field(default=8.0, gt=0, le=40)
        blade_thickness_mm: float = Field(default=1.5, gt=0, le=10)
        blade_pitch_mm: float = Field(default=12.0, gt=0, le=50)
        pivot_d_mm: float = Field(default=2.0, gt=0, le=8)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import (
            BRepPrimAPI_MakeBox,
            BRepPrimAPI_MakeCylinder,
        )
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        if args.blade_thickness_mm >= args.blade_pitch_mm:
            raise ValueError(
                f"hvac_blade_array: blade_thickness_mm ({args.blade_thickness_mm}) "
                f">= blade_pitch_mm ({args.blade_pitch_mm}) — blades would touch"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"hvac_blade_array: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "hvac_blade_array v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        cx, cy, base_z = center

        # Slot depth = blade_width_mm (into the body along face normal).
        slot_depth = args.blade_width_mm
        slot_zmin = min(base_z, base_z - z_sign * slot_depth)
        slot_zmax = max(base_z, base_z - z_sign * slot_depth)

        # Y-coordinates of each blade slot center.
        n = args.blade_count
        y0 = -(n - 1) * args.blade_pitch_mm / 2.0

        cutter_union = None

        for k in range(n):
            y_center = cy + y0 + k * args.blade_pitch_mm
            # slot box: spans X = [cx - L/2, cx + L/2],
            #           Y = [yc - t/2, yc + t/2], Z = [slot_zmin, slot_zmax]
            slot_lo = gp_Pnt(
                cx - args.blade_length_mm / 2.0,
                y_center - args.blade_thickness_mm / 2.0,
                slot_zmin,
            )
            slot_hi = gp_Pnt(
                cx + args.blade_length_mm / 2.0,
                y_center + args.blade_thickness_mm / 2.0,
                slot_zmax,
            )
            slot_mk = BRepPrimAPI_MakeBox(slot_lo, slot_hi)
            slot_mk.Build()
            if not slot_mk.IsDone():
                raise RuntimeError(f"hvac_blade_array: slot {k} build failed")
            slot_shape = slot_mk.Shape()

            if cutter_union is None:
                cutter_union = slot_shape
            else:
                fu = BRepAlgoAPI_Fuse(cutter_union, slot_shape)
                fu.Build()
                if not fu.IsDone():
                    raise RuntimeError(
                        f"hvac_blade_array: slot union {k} failed"
                    )
                cutter_union = fu.Shape()

            # Pivot holes at ±X end of slot. Each is a small cylinder along
            # +X / -X (so the pin axis is parallel to the blade's long axis).
            pivot_r = args.pivot_d_mm / 2.0
            pivot_len = args.blade_thickness_mm * 4.0  # extra so it pokes
            # The slot ends are at x = cx ± L/2. The pivot pin enters from
            # OUTSIDE the slot along ±X. Build cylinder starting just outside
            # the slot end and extending OUTWARD.
            for sign in (+1, -1):
                x_end = cx + sign * args.blade_length_mm / 2.0
                # axis direction outward from slot
                axis_dir = gp_Dir(float(sign), 0.0, 0.0)
                # base of cylinder at slot end face, extends outward by pivot_len
                base_pt = gp_Pnt(
                    x_end,
                    y_center,
                    base_z - z_sign * args.blade_width_mm / 2.0,
                )
                pivot_ax = gp_Ax2(base_pt, axis_dir)
                p_mk = BRepPrimAPI_MakeCylinder(pivot_ax, pivot_r, pivot_len)
                p_mk.Build()
                if not p_mk.IsDone():
                    raise RuntimeError(
                        f"hvac_blade_array: pivot {k}/{sign} build failed"
                    )
                pivot_shape = p_mk.Shape()
                fu = BRepAlgoAPI_Fuse(cutter_union, pivot_shape)
                fu.Build()
                if not fu.IsDone():
                    raise RuntimeError(
                        f"hvac_blade_array: pivot union {k}/{sign} failed"
                    )
                cutter_union = fu.Shape()

        cut = BRepAlgoAPI_Cut(shape, cutter_union)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("hvac_blade_array: BRepAlgoAPI_Cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "blade_slots":     HistoryRule.GENERATED_NEW,
                "pivot_holes":     HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
