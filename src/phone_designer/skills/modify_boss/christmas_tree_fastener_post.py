"""christmas_tree_fastener_post — atomic. Multi-fin retention post (Christmas tree clip).

A Christmas-tree fastener is a one-shot plastic post used widely in automotive
trim and interior assemblies to retain a panel against a substrate. Geometry:
a central cylindrical shaft of diameter ``hole_d_mm × 0.95`` rises from the
host face. Stacked along it are ``fin_count`` thin conical "fir" fins
(barbed sections); each fin is a cone whose base flares outward by ~20 %% beyond
the panel hole diameter and whose top tapers back to the shaft, giving the
characteristic christmas-tree silhouette. Fins are spaced ``fin_pitch_mm``
apart along the shaft; the bottom-most fin sits just above the panel-bearing
ledge at ``panel_t_mm`` above the face so the post grips a sheet of that
thickness.

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
    name="christmas_tree_fastener_post",
    category="modify/boss",
    level="atomic",
    summary="Multi-fin retention post (christmas-tree clip): central shaft topped "
            "by a stack of `fin_count` barbed conical fins, each flared beyond "
            "`hole_d_mm` to grip a panel of `panel_t_mm` thickness. v1 supports "
            "planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":  HistoryRule.MODIFIED_INHERIT,
        "post_solid":   HistoryRule.GENERATED_NEW,
        "fin_solids":   HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.fin_count_at_least_one",
        "pc.hole_diameter_positive",
    ],
    produces_features=["christmas_tree_fastener_post", "barbed_fin_stack"],
    preserves=["outer_envelope_outside_post"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"fin_min_thickness_mm": 0.4}},
    },
    failure_modes=[
        "fm.fin_overlaps_panel_ledge",
        "fm.fin_pitch_too_tight",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class ChristmasTreeFastenerPost(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) offset from face center for the post axis",
        )
        panel_t_mm: float = Field(default=2.0, gt=0, le=20,
                                    description="thickness of panel to be retained")
        hole_d_mm: float = Field(default=6.0, gt=0, le=30,
                                   description="nominal panel hole diameter — fins flare beyond this")
        fin_count: int = Field(default=3, ge=1, le=10,
                                 description="number of barbed fins stacked on the shaft")
        fin_pitch_mm: float = Field(default=2.0, gt=0, le=10,
                                      description="vertical spacing between adjacent fin bases")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"christmas_tree_fastener_post: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "christmas_tree_fastener_post v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        direction = gp_Dir(0.0, 0.0, z_sign)

        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        base_z = center[2]

        # Shaft radius is slightly smaller than the panel hole so the panel
        # slides over the shaft and engages on the fins.
        shaft_r = args.hole_d_mm * 0.95 / 2.0
        # Fin base flares ~20% beyond panel hole (the barb).
        fin_base_r = args.hole_d_mm * 1.20 / 2.0
        # Fin top tapers back to the shaft.
        fin_top_r = shaft_r
        # Fin axial height: pitch minus a small gap so adjacent fins do not merge.
        fin_height = args.fin_pitch_mm * 0.85

        # Total shaft height: panel ledge + fin stack + a small top cap.
        stack_height = args.fin_count * args.fin_pitch_mm
        shaft_height = args.panel_t_mm + stack_height + fin_height * 0.5

        # ── Shaft cylinder
        shaft_base_pt = gp_Pnt(cx, cy, base_z)
        shaft_ax = gp_Ax2(shaft_base_pt, direction)
        shaft_mk = BRepPrimAPI_MakeCylinder(shaft_ax, shaft_r, shaft_height)
        shaft_mk.Build()
        if not shaft_mk.IsDone():
            raise RuntimeError("christmas_tree_fastener_post: shaft build failed")
        post_solid = shaft_mk.Shape()

        # ── Fin stack. First fin sits at z = base_z + z_sign * panel_t_mm.
        first_fin_z = base_z + z_sign * args.panel_t_mm

        for k in range(args.fin_count):
            fin_base_z = first_fin_z + z_sign * (k * args.fin_pitch_mm)
            fin_base_pt = gp_Pnt(cx, cy, fin_base_z)
            fin_ax = gp_Ax2(fin_base_pt, direction)
            fin_mk = BRepPrimAPI_MakeCone(
                fin_ax, fin_base_r, fin_top_r, fin_height,
            )
            fin_mk.Build()
            if not fin_mk.IsDone():
                raise RuntimeError(
                    f"christmas_tree_fastener_post: fin {k} build failed"
                )
            fin_shape = fin_mk.Shape()

            fu = BRepAlgoAPI_Fuse(post_solid, fin_shape)
            fu.Build()
            if not fu.IsDone():
                raise RuntimeError(
                    f"christmas_tree_fastener_post: fin {k} fuse failed"
                )
            post_solid = fu.Shape()

        # ── Fuse the whole post onto the body.
        f_body = BRepAlgoAPI_Fuse(shape, post_solid)
        f_body.Build()
        if not f_body.IsDone():
            raise RuntimeError("christmas_tree_fastener_post: body fuse failed")
        new_shape = f_body.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face": HistoryRule.MODIFIED_INHERIT,
                "post_solid":  HistoryRule.GENERATED_NEW,
                "fin_solids":  HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
