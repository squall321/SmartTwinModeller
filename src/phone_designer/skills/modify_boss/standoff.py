"""standoff — atomic. PCB-mount boss with central through-hole.

A hollow cylindrical boss with a concentric inner hole, typically used to
mount a PCB above an interior face. Optional `taper_deg` adds an outward
draft at the top (cone-like) for injection-mold ejection.

Geometry (raw OCCT):
    outer = BRepPrimAPI_MakeCylinder | MakeCone (if taper_deg > 0)
    inner = BRepPrimAPI_MakeCylinder (slightly longer for clean cut)
    tool  = BRepAlgoAPI_Cut(outer, inner)
    result= BRepAlgoAPI_Fuse(body, tool)
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
    name="standoff",
    category="modify/boss",
    level="atomic",
    summary="PCB-mount standoff = hollow cylindrical boss (outer + concentric inner hole) "
            "with optional top taper (draft) for injection-mold ejection. v1 supports "
            "planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "standoff_solid":  HistoryRule.GENERATED_NEW,
    },
    produces_features=["standoff", "through_hole"],
    preserves=["outer_envelope_outside_standoff"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"wall_recommended_mm": "(outer-inner)/2 >= 0.8"}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "cnc_3axis":         {"min_wall_mm": 0.5},
    },
    failure_modes=["fm.inner_d_geq_outer_d", "fm.taper_consumes_wall"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class Standoff(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        center_x_mm: float = Field(default=0.0,
                                    description="face-local X offset from face center")
        center_y_mm: float = Field(default=0.0,
                                    description="face-local Y offset from face center")
        outer_diameter_mm: float = Field(gt=0, le=50,
                                          description="outer (boss) diameter")
        inner_diameter_mm: float = Field(gt=0, le=50,
                                          description="inner through-hole diameter")
        height_mm: float = Field(gt=0, le=50,
                                  description="standoff height (axial)")
        taper_deg: float = Field(default=0.0, ge=0.0, le=10.0,
                                  description="outward top taper for ejection draft; "
                                              "0 = straight cylinder, >0 = upper radius shrinks")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        if args.inner_diameter_mm >= args.outer_diameter_mm:
            raise ValueError(
                f"standoff: inner_d ({args.inner_diameter_mm}) >= outer_d "
                f"({args.outer_diameter_mm}) — no wall remains"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"standoff: face_selector matched 0 — {args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "standoff v1: planar +Z/-Z target faces only"
            )

        # axis direction: grows outward along face normal
        z_sign = 1.0 if normal[2] > 0 else -1.0
        direction = gp_Dir(0.0, 0.0, z_sign)

        base = gp_Pnt(
            center[0] + args.center_x_mm,
            center[1] + args.center_y_mm,
            center[2],
        )
        ax2 = gp_Ax2(base, direction)

        outer_r = args.outer_diameter_mm / 2.0
        if args.taper_deg > 0.0:
            # upper radius = outer_r - height * tan(taper)
            upper_r = outer_r - args.height_mm * math.tan(math.radians(args.taper_deg))
            if upper_r <= args.inner_diameter_mm / 2.0:
                raise ValueError(
                    f"standoff: taper_deg ({args.taper_deg}) consumes wall — "
                    f"upper_r {upper_r:.3f} <= inner_r {args.inner_diameter_mm / 2.0:.3f}"
                )
            outer_mk = BRepPrimAPI_MakeCone(ax2, outer_r, upper_r, args.height_mm)
        else:
            outer_mk = BRepPrimAPI_MakeCylinder(ax2, outer_r, args.height_mm)
        outer_mk.Build()
        if not outer_mk.IsDone():
            raise RuntimeError("standoff: outer cylinder/cone build failed")
        outer_shape = outer_mk.Shape()

        # inner hole — slightly longer for clean boolean
        # extend +overshoot in axial direction, also shift base down a hair so it
        # punches through the bottom too.
        overshoot = 1.0
        inner_base = gp_Pnt(
            center[0] + args.center_x_mm,
            center[1] + args.center_y_mm,
            center[2] - z_sign * (overshoot / 2.0),
        )
        inner_ax2 = gp_Ax2(inner_base, direction)
        inner_mk = BRepPrimAPI_MakeCylinder(
            inner_ax2, args.inner_diameter_mm / 2.0, args.height_mm + overshoot,
        )
        inner_mk.Build()
        if not inner_mk.IsDone():
            raise RuntimeError("standoff: inner cylinder build failed")
        inner_shape = inner_mk.Shape()

        # tool = outer - inner
        cut = BRepAlgoAPI_Cut(outer_shape, inner_shape)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("standoff: outer - inner cut failed")
        tool = cut.Shape()

        fuse = BRepAlgoAPI_Fuse(shape, tool)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("standoff: body fuse failed")
        new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":    HistoryRule.MODIFIED_INHERIT,
                "standoff_solid": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
