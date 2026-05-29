"""barrel_pivot_socket_pair — atomic. Two coaxial socket bosses, separated by
``separation_mm`` along Y, each with a coaxial pivot socket bore.

Used as the trunnion pair for a rotating barrel vent or rocker mechanism:
the barrel's two pivot stubs (diameter = pivot_inner_d_mm) rotate inside the
two socket bores. Each socket is a cylindrical boss of outer diameter
``pivot_outer_d_mm`` rising from the host face, with an axial blind bore of
diameter ``pivot_inner_d_mm`` and depth ``socket_depth_mm``.

v1 supports planar +Z/-Z target faces.
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
    name="barrel_pivot_socket_pair",
    category="modify/boss",
    level="atomic",
    summary="Pair of coaxial pivot-socket bosses separated by `separation_mm` along Y. "
            "Each boss has an axial blind bore sized for a barrel-vent trunnion. "
            "v1 supports planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":   HistoryRule.MODIFIED_INHERIT,
        "socket_bosses": HistoryRule.GENERATED_NEW,
        "pivot_bores":   HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.separation_positive",
    ],
    produces_features=["barrel_pivot_socket_pair", "pivot_bore_pair"],
    preserves=["outer_envelope_outside_sockets"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "cnc_3axis":         {"min_wall_mm": 0.5},
    },
    failure_modes=[
        "fm.inner_diameter_geq_outer",
        "fm.socket_depth_geq_boss_height",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class BarrelPivotSocketPair(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        separation_mm: float = Field(
            gt=0, le=300,
            description="center-to-center distance between the two socket axes",
        )
        pivot_inner_d_mm: float = Field(default=2.0, gt=0, le=20,
                                          description="bore diameter (trunnion fit)")
        pivot_outer_d_mm: float = Field(default=4.0, gt=0, le=30,
                                          description="boss outer diameter (wall)")
        socket_depth_mm: float = Field(default=3.0, gt=0, le=20,
                                         description="blind bore depth")
        boss_height_mm: float = Field(default=4.0, gt=0, le=20,
                                        description="boss height above face")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        if args.pivot_inner_d_mm >= args.pivot_outer_d_mm:
            raise ValueError(
                f"barrel_pivot_socket_pair: pivot_inner_d_mm "
                f"({args.pivot_inner_d_mm}) >= pivot_outer_d_mm "
                f"({args.pivot_outer_d_mm}) — no boss wall"
            )
        if args.socket_depth_mm > args.boss_height_mm:
            raise ValueError(
                f"barrel_pivot_socket_pair: socket_depth_mm "
                f"({args.socket_depth_mm}) > boss_height_mm "
                f"({args.boss_height_mm}) — bore would pierce through floor"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"barrel_pivot_socket_pair: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "barrel_pivot_socket_pair v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        cx, cy, base_z = center
        axis_dir = gp_Dir(0.0, 0.0, z_sign)

        outer_r = args.pivot_outer_d_mm / 2.0
        inner_r = args.pivot_inner_d_mm / 2.0

        half_sep = args.separation_mm / 2.0

        current = shape

        for sign in (+1.0, -1.0):
            y = cy + sign * half_sep

            # Outer boss cylinder.
            base_pt = gp_Pnt(cx, y, base_z)
            outer_ax = gp_Ax2(base_pt, axis_dir)
            outer_mk = BRepPrimAPI_MakeCylinder(outer_ax, outer_r,
                                                  args.boss_height_mm)
            outer_mk.Build()
            if not outer_mk.IsDone():
                raise RuntimeError(
                    "barrel_pivot_socket_pair: outer cylinder build failed"
                )
            outer_solid = outer_mk.Shape()

            # Fuse the boss to the body FIRST.
            fu = BRepAlgoAPI_Fuse(current, outer_solid)
            fu.Build()
            if not fu.IsDone():
                raise RuntimeError(
                    "barrel_pivot_socket_pair: boss fuse failed"
                )
            current = fu.Shape()

            # Now bore the socket from the top of the boss inward by
            # socket_depth_mm. Top of boss is at base_z + z_sign * boss_h.
            top_z = base_z + z_sign * args.boss_height_mm
            # Cylinder grows along -z_sign (into boss) from the top.
            # Place its base at top_z and direction = -z_sign.
            bore_base = gp_Pnt(cx, y, top_z)
            bore_ax = gp_Ax2(bore_base, gp_Dir(0.0, 0.0, -z_sign))
            bore_mk = BRepPrimAPI_MakeCylinder(bore_ax, inner_r,
                                                 args.socket_depth_mm)
            bore_mk.Build()
            if not bore_mk.IsDone():
                raise RuntimeError(
                    "barrel_pivot_socket_pair: bore cylinder build failed"
                )
            bore_solid = bore_mk.Shape()

            cut = BRepAlgoAPI_Cut(current, bore_solid)
            cut.Build()
            if not cut.IsDone():
                raise RuntimeError(
                    "barrel_pivot_socket_pair: bore cut failed"
                )
            current = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":   HistoryRule.MODIFIED_INHERIT,
                "socket_bosses": HistoryRule.GENERATED_NEW,
                "pivot_bores":   HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(current), history=history)
