"""button_cap_rounded_actuation — atomic. Cylindrical button post with dome cap.

A user-facing button cap consisting of a short cylindrical post that grows out
of the host face, topped with a spherical dome of the same base diameter. The
dome gives a comfortable rounded actuation surface for the finger pad. Common
on power / volume / shutter buttons.

Geometry (raw OCCT, normal-aware):
    post  = BRepPrimAPI_MakeCylinder(ax, r=base_d/2, h=post_h)
    dome  = spherical cap of base radius base_d/2 and height dome_h,
            sitting on top of the post
    tool  = fuse(post, dome)
    result= fuse(body, tool)

post_h is fixed to base_d/2 if not specified — a stubby "pill" form. For this
v1 the post height is implicit (matches dome base diameter / 2) so callers only
need to pick base_d and dome_h.
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
    name="button_cap_rounded_actuation",
    category="modify/boss",
    level="atomic",
    summary="User-facing button cap = short cylindrical post + spherical dome "
            "cap of matching base diameter. Provides a rounded finger-pad "
            "actuation surface in a single skill (no manual post+sphere compose).",
    selector_kinds=["faces"],
    history_rules={
        "target_face":  HistoryRule.MODIFIED_INHERIT,
        "post_wall":    HistoryRule.GENERATED_NEW,
        "dome_face":    HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.diameter_positive",
    ],
    produces_features=["button_cap", "dome", "actuation_surface"],
    preserves=["outer_envelope_outside_button"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "min_fillet_r_mm": 0.3},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "min_fillet_r_mm": 0.5},
        "cnc_3axis":         {"min_wall_mm": 0.5, "min_fillet_r_mm": 0.3},
    },
    failure_modes=[
        "fm.dome_h_geq_radius",
        "fm.button_outside_face",
    ],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class ButtonCapRoundedActuation(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of button axis (anchored at face center)",
        )
        base_d_mm: float = Field(default=6.0, gt=0, le=50,
                                  description="post + dome base diameter")
        dome_h_mm: float = Field(default=1.0, gt=0, le=20,
                                  description="dome cap rise above the post top")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import (
            BRepAlgoAPI_Common,
            BRepAlgoAPI_Fuse,
        )
        from OCP.BRepPrimAPI import (
            BRepPrimAPI_MakeBox,
            BRepPrimAPI_MakeCylinder,
            BRepPrimAPI_MakeSphere,
        )
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        base_r = args.base_d_mm / 2.0
        if args.dome_h_mm > base_r * 2.0:
            raise ValueError(
                f"button_cap_rounded_actuation: dome_h ({args.dome_h_mm}) "
                f"exceeds full sphere height 2*r={2 * base_r:.3f}"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"button_cap_rounded_actuation: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "button_cap_rounded_actuation v1: planar +Z/-Z target faces only"
            )

        # axis grows OUTWARD along face normal
        z_sign = 1.0 if normal[2] > 0 else -1.0
        wx = center[0] + args.position_xy[0]
        wy = center[1] + args.position_xy[1]
        face_z = center[2]
        direction = gp_Dir(0.0, 0.0, z_sign)

        # Post height = half the base diameter (stubby pill form). Keeps the
        # API small — caller only picks base_d + dome_h.
        post_h = base_r

        # 1) post cylinder — from face outward post_h
        post_ax = gp_Ax2(gp_Pnt(wx, wy, face_z), direction)
        post_mk = BRepPrimAPI_MakeCylinder(post_ax, base_r, post_h)
        post_mk.Build()
        if not post_mk.IsDone():
            raise RuntimeError(
                "button_cap_rounded_actuation: post cylinder build failed"
            )
        post_shape = post_mk.Shape()

        # 2) spherical cap sitting on top of the post.
        #    Cap of base radius r and height h ⇒ sphere of radius
        #        R = (r² + h²) / (2h)
        #    centered so the cap protrudes by exactly h above the post top.
        post_top_z = face_z + z_sign * post_h
        h = args.dome_h_mm
        R = (base_r * base_r + h * h) / (2.0 * h)
        sphere_center_z = post_top_z + z_sign * (h - R)
        sph_mk = BRepPrimAPI_MakeSphere(
            gp_Pnt(wx, wy, sphere_center_z), R,
        )
        sph_mk.Build()
        if not sph_mk.IsDone():
            raise RuntimeError(
                "button_cap_rounded_actuation: sphere build failed"
            )
        sphere = sph_mk.Shape()

        # Clip the sphere to the half-space outside the post top: bounding box
        # from post_top_z extending outward by h + 1mm.
        margin = base_r + h + 1.0
        if z_sign > 0:
            box_corner = gp_Pnt(wx - margin, wy - margin, post_top_z)
            box_dx, box_dy, box_dz = 2.0 * margin, 2.0 * margin, h + margin
        else:
            box_corner = gp_Pnt(wx - margin, wy - margin, post_top_z - (h + margin))
            box_dx, box_dy, box_dz = 2.0 * margin, 2.0 * margin, h + margin
        box_mk = BRepPrimAPI_MakeBox(box_corner, box_dx, box_dy, box_dz)
        box_mk.Build()
        if not box_mk.IsDone():
            raise RuntimeError(
                "button_cap_rounded_actuation: cap clipping box failed"
            )
        clip_box = box_mk.Shape()

        common = BRepAlgoAPI_Common(sphere, clip_box)
        common.Build()
        if not common.IsDone():
            raise RuntimeError(
                "button_cap_rounded_actuation: spherical cap intersection failed"
            )
        cap_shape = common.Shape()

        # 3) tool = post ∪ cap
        f1 = BRepAlgoAPI_Fuse(post_shape, cap_shape)
        f1.Build()
        if not f1.IsDone():
            raise RuntimeError(
                "button_cap_rounded_actuation: post+cap fuse failed"
            )
        tool = f1.Shape()

        # 4) body ∪ tool
        f2 = BRepAlgoAPI_Fuse(shape, tool)
        f2.Build()
        if not f2.IsDone():
            raise RuntimeError(
                "button_cap_rounded_actuation: body fuse failed"
            )
        new_shape = f2.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face": HistoryRule.MODIFIED_INHERIT,
                "post_wall":   HistoryRule.GENERATED_NEW,
                "dome_face":   HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
