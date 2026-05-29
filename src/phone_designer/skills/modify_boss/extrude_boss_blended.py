"""extrude_boss_blended — atomic.

Like extrude_plateau / mounting_pad but with TOP and BASE fillet radii baked
into the same operation. Saves the user from running final_fillet on the
boss edges afterwards.

Args:
    face_selector: SelectorRef
    sketch:        SketchSpec
    height_mm:     float
    top_blend_r_mm:  float = 0.0     — fillet between boss walls and top face
    base_blend_r_mm: float = 0.0     — fillet between boss base and host face
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec
from phone_designer.skills.modify_pocket.extrude_pocket_blended import (
    _fillet_edges_near_z,
)


@skill(
    name="extrude_boss_blended",
    category="modify/boss",
    level="atomic",
    summary="Extrude a 2D sketch outward from a face, with top and/or base "
            "blend (fillet) radii baked into the same operation. Replaces "
            "extrude_plateau + final_fillet for cosmetic bosses.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":   HistoryRule.MODIFIED_INHERIT,
        "boss_walls":    HistoryRule.GENERATED_NEW,
        "boss_top":      HistoryRule.GENERATED_NEW,
        "boss_blends":   HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.height_positive",
    ],
    produces_features=["plateau", "fillet_faces_array"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5, "min_fillet_r_mm": 0.3},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "min_fillet_r_mm": 0.5},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "min_fillet_r_mm": 0.3},
    },
    failure_modes=["fm.sketch_outside_face", "fm.fillet_radius_too_large"],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class ExtrudeBossBlended(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        sketch: SketchSpec
        height_mm: float = Field(gt=0, le=50)
        top_blend_r_mm: float = Field(default=0.0, ge=0.0, le=10.0)
        base_blend_r_mm: float = Field(default=0.0, ge=0.0, le=10.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _face_plane_normal,
            build_pocket_tool,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"extrude_boss_blended: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        center, normal = _face_plane_normal(target_face)
        face_z = center[2]
        nz = normal[2]

        tool = build_pocket_tool(target_face, args.sketch, args.height_mm, direction="out")

        fuse = BRepAlgoAPI_Fuse(shape, tool)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("extrude_boss_blended fuse 실패")
        new_shape = fuse.Shape()

        # Compute boss top & base Z planes.
        # boss grows along face normal:
        #   top face   (nz > 0) → base at face_z, top at face_z + height
        #   bottom face(nz < 0) → base at face_z, top at face_z - height
        base_z = face_z
        top_z = face_z + args.height_mm if nz > 0 else face_z - args.height_mm

        if args.top_blend_r_mm > 0:
            new_shape, _ = _fillet_edges_near_z(
                new_shape, top_z, args.top_blend_r_mm,
            )
        if args.base_blend_r_mm > 0:
            new_shape, _ = _fillet_edges_near_z(
                new_shape, base_z, args.base_blend_r_mm,
            )

        history = EntityHistoryMap(
            rules={
                "target_face": HistoryRule.MODIFIED_INHERIT,
                "boss_walls":  HistoryRule.GENERATED_NEW,
                "boss_top":    HistoryRule.GENERATED_NEW,
                "boss_blends": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
