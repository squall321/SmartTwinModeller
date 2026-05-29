"""stylus_tip_clearance — atomic. Stepped clearance pocket for pencil/stylus tip.

Two-stage tapered pocket used as the tip-clearance well behind a stylus dock or
pencil-holder opening. The pocket has a wider cylindrical *throat* near the
opening that tapers down to a narrower *tip* cylinder at the bottom of the
pocket. This relieves the tip without the user having to compose two pockets
plus a cone manually.

Geometry (raw OCCT, normal-aware):
    throat_cyl = BRepPrimAPI_MakeCylinder(face → throat_depth, r=throat_d/2)
    cone       = BRepPrimAPI_MakeCone(throat_top → tip_top, r1=throat_d/2,
                                       r2=tip_d/2,    height=cone_depth)
    tip_cyl    = BRepPrimAPI_MakeCylinder(tip_top → floor, r=tip_d/2)
    tool       = fuse(throat_cyl, cone, tip_cyl)
    result     = body - tool

Throat is set to ~25% of the total ``depth_mm`` to leave the bulk of the
length to the taper + tip well. tip_d must be < throat_d (precondition).
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
    name="stylus_tip_clearance",
    category="modify/pocket",
    level="atomic",
    summary="Stepped tapered pocket for stylus/pencil tip clearance — wider "
            "cylindrical throat near the opening tapering to a narrower tip "
            "cylinder at the floor. Saves composing 2 pockets + a cone.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":      HistoryRule.MODIFIED_INHERIT,
        "throat_wall":      HistoryRule.GENERATED_NEW,
        "taper_wall":       HistoryRule.GENERATED_NEW,
        "tip_wall":         HistoryRule.GENERATED_NEW,
        "consumed_volume":  HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.diameter_positive",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["stylus_tip_clearance", "stepped_pocket", "tapered_pocket"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"max_aspect_ratio": 8.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.tip_d_geq_throat_d",
        "fm.pocket_exits_body",
        "fm.tip_too_close_to_edge",
    ],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class StylusTipClearance(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of pocket axis (anchored at face center)",
        )
        tip_d_mm: float = Field(default=2.5, gt=0, le=50,
                                 description="tip cylinder diameter (floor)")
        throat_d_mm: float = Field(default=4.0, gt=0, le=50,
                                    description="throat cylinder diameter (opening)")
        depth_mm: float = Field(default=8.0, gt=0, le=100,
                                 description="total pocket depth from face to floor")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        if args.tip_d_mm >= args.throat_d_mm:
            raise ValueError(
                f"stylus_tip_clearance: tip_d ({args.tip_d_mm}) >= throat_d "
                f"({args.throat_d_mm}) — throat must be wider than tip"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"stylus_tip_clearance: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "stylus_tip_clearance v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        wx = center[0] + args.position_xy[0]
        wy = center[1] + args.position_xy[1]
        face_z = center[2]
        # axis points INTO the body (opposite the face normal)
        direction = gp_Dir(0.0, 0.0, -z_sign)

        throat_r = args.throat_d_mm / 2.0
        tip_r = args.tip_d_mm / 2.0

        # Split depth: throat 25%, taper 25%, tip 50% — keeps the bulk of the
        # pocket as the narrow tip well.
        throat_depth = args.depth_mm * 0.25
        cone_depth = args.depth_mm * 0.25
        tip_depth = args.depth_mm - throat_depth - cone_depth
        overlap = 0.05  # tiny overlap so booleans fuse cleanly

        # 1) throat cylinder — from opening (face) down throat_depth
        throat_ax = gp_Ax2(gp_Pnt(wx, wy, face_z), direction)
        throat_mk = BRepPrimAPI_MakeCylinder(
            throat_ax, throat_r, throat_depth + overlap,
        )
        throat_mk.Build()
        if not throat_mk.IsDone():
            raise RuntimeError("stylus_tip_clearance: throat cylinder build failed")
        throat_shape = throat_mk.Shape()

        # 2) cone — from end of throat to start of tip
        cone_top_z = face_z - z_sign * (throat_depth - overlap)
        cone_ax = gp_Ax2(gp_Pnt(wx, wy, cone_top_z), direction)
        # axis points into body — cone "base" (r1) at cone_top_z, "top" (r2)
        # cone_depth deeper.
        cone_mk = BRepPrimAPI_MakeCone(
            cone_ax, throat_r, tip_r, cone_depth + overlap,
        )
        cone_mk.Build()
        if not cone_mk.IsDone():
            raise RuntimeError("stylus_tip_clearance: cone build failed")
        cone_shape = cone_mk.Shape()

        # 3) tip cylinder — from end of cone to floor
        tip_top_z = face_z - z_sign * (throat_depth + cone_depth - overlap)
        tip_ax = gp_Ax2(gp_Pnt(wx, wy, tip_top_z), direction)
        tip_mk = BRepPrimAPI_MakeCylinder(
            tip_ax, tip_r, tip_depth + overlap,
        )
        tip_mk.Build()
        if not tip_mk.IsDone():
            raise RuntimeError("stylus_tip_clearance: tip cylinder build failed")
        tip_shape = tip_mk.Shape()

        # fuse: throat + cone, then + tip
        f1 = BRepAlgoAPI_Fuse(throat_shape, cone_shape)
        f1.Build()
        if not f1.IsDone():
            raise RuntimeError("stylus_tip_clearance: throat+cone fuse failed")
        partial = f1.Shape()
        f2 = BRepAlgoAPI_Fuse(partial, tip_shape)
        f2.Build()
        if not f2.IsDone():
            raise RuntimeError("stylus_tip_clearance: +tip fuse failed")
        tool = f2.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("stylus_tip_clearance: boolean cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "throat_wall":     HistoryRule.GENERATED_NEW,
                "taper_wall":      HistoryRule.GENERATED_NEW,
                "tip_wall":        HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
