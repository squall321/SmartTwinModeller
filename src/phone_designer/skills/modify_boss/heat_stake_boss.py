"""heat_stake_boss — atomic. Cylindrical shaft with chamfered top crown for heat-staking.

A heat-stake boss is a plastic post that protrudes through a hole in a mating
part; the protruding crown is then melted and flared with a hot tool. The
geometry is a cylinder (shaft) topped by a short cone (crown) whose top
radius is slightly larger than the shaft radius — the crown's outward taper
gives the heat-stake tool a defined volume of plastic to mushroom over the
mating part.

Geometry (raw OCCT):
    shaft = BRepPrimAPI_MakeCylinder(r=shaft_r, h=shaft_height)
    crown = BRepPrimAPI_MakeCone(r1=shaft_r, r2=crown_r_upper, h=crown_height)
    where crown_r_upper = shaft_r + crown_height * tan(chamfer_deg)
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._resolvers import _face_center, _face_normal_at_center, resolve_faces
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="heat_stake_boss",
    category="modify/boss",
    level="atomic",
    summary="Heat-stake boss = cylindrical shaft + outward-tapered crown at the top, "
            "ready for hot-tool flaring against a mating part. v1 supports planar "
            "+Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":         HistoryRule.MODIFIED_INHERIT,
        "heat_stake_solid":    HistoryRule.GENERATED_NEW,
    },
    produces_features=["heat_stake_boss"],
    preserves=["outer_envelope_outside_boss"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"crown_volume_for_flare_mm3": "shaft_d^2 * crown_h * π/4"}},
    },
    failure_modes=["fm.crown_chamfer_negative", "fm.shaft_zero_height"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class HeatStakeBoss(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        center_x_mm: float = Field(default=0.0,
                                    description="face-local X offset from face center")
        center_y_mm: float = Field(default=0.0,
                                    description="face-local Y offset from face center")
        shaft_diameter_mm: float = Field(gt=0, le=20,
                                          description="shaft diameter")
        shaft_height_mm: float = Field(gt=0, le=30,
                                        description="cylindrical shaft height (below crown)")
        crown_chamfer_deg: float = Field(default=45.0, ge=0.0, le=80.0,
                                          description="outward chamfer angle of the crown — "
                                                      "0 = flat top extension, larger = wider flare")
        crown_height_mm: float = Field(default=0.5, gt=0, le=5.0,
                                        description="crown (cone) height above the shaft")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"heat_stake_boss: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "heat_stake_boss v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        direction = gp_Dir(0.0, 0.0, z_sign)
        shaft_r = args.shaft_diameter_mm / 2.0

        # shaft — base at face center
        shaft_base = gp_Pnt(
            center[0] + args.center_x_mm,
            center[1] + args.center_y_mm,
            center[2],
        )
        shaft_ax = gp_Ax2(shaft_base, direction)
        shaft_mk = BRepPrimAPI_MakeCylinder(shaft_ax, shaft_r, args.shaft_height_mm)
        shaft_mk.Build()
        if not shaft_mk.IsDone():
            raise RuntimeError("heat_stake_boss: shaft build failed")
        shaft_shape = shaft_mk.Shape()

        # crown — base at shaft top, expands outward
        crown_upper_r = shaft_r + args.crown_height_mm * math.tan(
            math.radians(args.crown_chamfer_deg)
        )
        crown_base_z = center[2] + z_sign * args.shaft_height_mm
        crown_base = gp_Pnt(
            center[0] + args.center_x_mm,
            center[1] + args.center_y_mm,
            crown_base_z,
        )
        crown_ax = gp_Ax2(crown_base, direction)
        if abs(crown_upper_r - shaft_r) < 1e-6:
            # zero chamfer → straight extension cylinder
            crown_mk = BRepPrimAPI_MakeCylinder(crown_ax, shaft_r, args.crown_height_mm)
        else:
            crown_mk = BRepPrimAPI_MakeCone(
                crown_ax, shaft_r, crown_upper_r, args.crown_height_mm,
            )
        crown_mk.Build()
        if not crown_mk.IsDone():
            raise RuntimeError("heat_stake_boss: crown build failed")
        crown_shape = crown_mk.Shape()

        # shaft + crown
        f1 = BRepAlgoAPI_Fuse(shaft_shape, crown_shape)
        f1.Build()
        if not f1.IsDone():
            raise RuntimeError("heat_stake_boss: shaft + crown fuse failed")
        boss_shape = f1.Shape()

        # body + boss
        f2 = BRepAlgoAPI_Fuse(shape, boss_shape)
        f2.Build()
        if not f2.IsDone():
            raise RuntimeError("heat_stake_boss: body fuse failed")
        new_shape = f2.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":      HistoryRule.MODIFIED_INHERIT,
                "heat_stake_solid": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
