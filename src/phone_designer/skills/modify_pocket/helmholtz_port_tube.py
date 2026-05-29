"""helmholtz_port_tube — atomic. Helmholtz tuning port for acoustic chamber.

A cylindrical through-tube cut into a planar face that connects an internal
acoustic cabinet volume to the outside, forming the inductive arm of a
Helmholtz resonator together with the cabinet volume:

    fb = (c / 2π) * sqrt( A / ((L + ΔL) * V) )

where A = π (d/2)², c = speed of sound in air (343 m/s = 343000 mm/s),
ΔL ≈ 1.7 * (d/2) is the end correction for an unflanged tube.

If `target_fb_hz` and `cabinet_volume_mm3` are both supplied, the caller's
`port_length_mm` is overridden by the analytic length needed to tune the
resonator to fb:

    L = (c² · π · (d/2)²) / (4π² · fb² · V)  −  1.7 · (d/2)

v1 supports planar +Z/-Z target faces.
"""
from __future__ import annotations

import math
from typing import Any, Optional

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


# Speed of sound in air (mm/s).
_C_SOUND_MM_S = 343_000.0


@skill(
    name="helmholtz_port_tube",
    category="modify/pocket",
    level="atomic",
    summary="Cylindrical Helmholtz tuning port cut into a planar face. "
            "Optionally retunes its own length from a target box-tuning "
            "frequency fb and a known cabinet back-volume. v1 supports "
            "planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "port_wall":       HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.diameter_positive",
    ],
    produces_features=["helmholtz_port", "acoustic_tube"],
    preserves=["outer_envelope_outside_port"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.6,
                              "extras": {"max_aspect_ratio": 8.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.port_length_nonpositive_after_tuning",
        "fm.port_exits_body",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class HelmholtzPortTube(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of port axis",
        )
        port_diameter_mm: float = Field(gt=0, le=50)
        port_length_mm: float = Field(gt=0, le=200)
        target_fb_hz: Optional[float] = Field(
            default=None, gt=0, le=20000,
            description="If set with cabinet_volume_mm3, override port_length",
        )
        cabinet_volume_mm3: Optional[float] = Field(
            default=None, gt=0,
            description="Back-volume coupled to the port",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        d = args.port_diameter_mm
        r = d / 2.0
        L = args.port_length_mm

        # Retune length if both Helmholtz inputs are provided.
        if args.target_fb_hz is not None and args.cabinet_volume_mm3 is not None:
            fb = args.target_fb_hz
            V = args.cabinet_volume_mm3
            A = math.pi * r * r
            # L_eff = c² · A / (4 π² · fb² · V); subtract end correction (one
            # side: unflanged ≈ 1.7 r; v1 keeps this simple, single-sided).
            L_eff = (_C_SOUND_MM_S * _C_SOUND_MM_S * A) / (
                4.0 * math.pi * math.pi * fb * fb * V
            )
            L_tuned = L_eff - 1.7 * r
            if L_tuned <= 0.0:
                raise ValueError(
                    f"helmholtz_port_tube: tuned port length non-positive "
                    f"(L={L_tuned:.3f} mm) for fb={fb} Hz, V={V} mm³, d={d} mm — "
                    f"reduce diameter, lower fb, or shrink cabinet volume."
                )
            L = L_tuned

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"helmholtz_port_tube: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "helmholtz_port_tube v1: planar +Z/-Z target faces only"
            )

        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        # Push the cylinder base a hair outside the face so the boolean cut
        # cleanly forms the entry edge, then extend by L + overshoot.
        overshoot = 0.5
        base_pt = gp_Pnt(cx, cy, center[2] + nz_sign * overshoot)
        axis_dir = gp_Dir(0.0, 0.0, -nz_sign)
        ax = gp_Ax2(base_pt, axis_dir)
        mk = BRepPrimAPI_MakeCylinder(ax, r, L + overshoot)
        mk.Build()
        if not mk.IsDone():
            raise RuntimeError(
                "helmholtz_port_tube: port cylinder build failed"
            )

        cut = BRepAlgoAPI_Cut(shape, mk.Shape())
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError(
                "helmholtz_port_tube: boolean cut failed"
            )
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "port_wall":       HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
