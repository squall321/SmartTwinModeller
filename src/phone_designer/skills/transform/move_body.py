"""move_body — atomic. Rigid transform (translate + rotate) of THE working solid.

The base body-level transform (SolidWorks Move/Copy Body, Fusion Move, NX Move
Object), DECOUPLED from any assembly context — reposition a modeled solid, re-datum
an imported part, or stage a copy before a boolean. Distinct from assembly's
``move_component``, which hard-gates on a NAMED component and re-wraps its output as
an assembly compound; this operates on the anonymous single body directly.

Order of application: rotate about (axis through rotate_origin_mm) by rotate_deg,
THEN translate by translate_mm — the SolidWorks convention. ``copy=True`` leaves the
original in place and returns a 2-body compound (original + moved), the leave-a-copy
idiom used to seed a boolean. Reuses the assembly compound helpers
(make_translation_trsf / make_axis_rotation_trsf / apply_transform_shape).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult

_EPS_VOL = 1e-6


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(float(g.Mass()))


@skill(
    name="move_body",
    category="transform",
    level="atomic",
    summary="Rigid transform (translate + rotate) of the working solid, "
            "non-assembly. rotate about (rotate_axis through rotate_origin_mm) by "
            "rotate_deg, THEN translate_mm. copy=True leaves the original and "
            "returns a 2-body compound (leave-a-copy, e.g. to seed a boolean). "
            "Volume is invariant under a rigid motion.",
    selector_kinds=[],
    history_rules={"body_faces": HistoryRule.MODIFIED_INHERIT},
    produces_features=[],
    preserves=["volume", "outer_envelope_outer"],
    manufacturing={},
    failure_modes=["fm.zero_rotation_axis", "fm.transform_failed"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class MoveBody(SkillBase):
    class Args(BaseModel):
        translate_mm: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="World translation applied AFTER the rotation.")
        rotate_axis: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 1.0),
            description="Rotation axis direction (need not be unit). Ignored if "
                        "rotate_deg == 0.")
        rotate_deg: float = Field(
            default=0.0,
            description="Rotation angle in degrees about (rotate_axis through "
                        "rotate_origin_mm), applied BEFORE the translation.")
        rotate_origin_mm: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="A point the rotation axis passes through.")
        leave_copy: bool = Field(
            default=False,
            description="False = move the body. True = leave the original in place "
                        "and return a 2-body compound (original + moved) — the "
                        "leave-a-copy idiom, e.g. to seed a boolean.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.gp import gp_Trsf

        from phone_designer.skills.assembly._compound import (
            apply_transform_shape,
            build_compound,
            make_axis_rotation_trsf,
            make_translation_trsf,
        )

        if body is None:
            raise ValueError("fm.transform_failed: move_body needs an input body.")
        shape = body.wrapped if hasattr(body, "wrapped") else body

        # compose rotation THEN translation into a single gp_Trsf.
        trsf = gp_Trsf()
        if args.rotate_deg != 0.0:
            ax, ay, az = args.rotate_axis
            if math.sqrt(ax * ax + ay * ay + az * az) < 1e-12:
                raise ValueError(
                    "fm.zero_rotation_axis: rotate_axis is a zero vector but "
                    f"rotate_deg={args.rotate_deg} — supply a non-zero axis.")
            ox, oy, oz = args.rotate_origin_mm
            # rotate about an axis through an arbitrary origin: translate origin→0,
            # rotate about origin-axis, translate back. Compose as T(o)·R·T(-o).
            to_origin = make_translation_trsf(-ox, -oy, -oz)
            rot = make_axis_rotation_trsf(ax, ay, az, math.radians(args.rotate_deg))
            back = make_translation_trsf(ox, oy, oz)
            trsf = back.Multiplied(rot).Multiplied(to_origin)
        tx, ty, tz = args.translate_mm
        trsf = make_translation_trsf(tx, ty, tz).Multiplied(trsf)

        try:
            moved = apply_transform_shape(shape, trsf)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.transform_failed: {type(exc).__name__}: {exc}")

        if args.leave_copy:
            out = build_compound([shape, moved])
        else:
            out = moved

        vol = _volume(out)
        if out is None or out.IsNull() or vol <= _EPS_VOL:
            raise ValueError(
                f"fm.transform_failed: result volume {vol:.6g}mm³ ≈ 0.")

        return SkillResult(
            body=Part(out),
            history=EntityHistoryMap(rules={"body_faces": HistoryRule.MODIFIED_INHERIT}),
            extras={"transform": {
                "op": "move_body",
                "translate_mm": list(args.translate_mm),
                "rotate_deg": args.rotate_deg,
                "rotate_axis": list(args.rotate_axis),
                "leave_copy": args.leave_copy,
                "n_bodies": 2 if args.leave_copy else 1,
                "volume_mm3": round(vol, 3),
            }},
        )
