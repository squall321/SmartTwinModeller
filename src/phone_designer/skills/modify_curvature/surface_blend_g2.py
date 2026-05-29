"""surface_blend_g2 — atomic. G2 curvature-continuous surface blend.

Same machinery as `surface_blend_g1` but the BRepFill_Filling constraint
order is GeomAbs_G2 — the blend surface matches not just the tangent plane
of each anchor face but also the principal curvatures along the boundary
edge. Visually this gives a "Class-A" style highlight-continuous blend
suitable for consumer-product surfaces.

Caveat: G2 is materially harder for the filler to solve. If convergence
fails, the skill raises — caller can retry with `surface_blend_g1` as a
gentler fallback.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_curvature.surface_blend_g1 import (
    _build_blend_face,
)


@skill(
    name="surface_blend_g2",
    category="modify/curvature",
    level="atomic",
    summary="G2 curvature-continuous surface blend between two faces. Builds a "
            "new spline surface (via BRepFill_Filling) that matches the tangent "
            "plane AND principal curvatures of A and B along their boundary "
            "edges. Stricter than surface_blend_g1; fails if filler can't "
            "converge — caller may fall back to G1.",
    selector_kinds=["faces"],
    history_rules={
        "face_a":       HistoryRule.MODIFIED_INHERIT,
        "face_b":       HistoryRule.MODIFIED_INHERIT,
        "blend_face":   HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_a_matches_one",
        "pc.face_selector_b_matches_one",
    ],
    produces_features=["surface_blend_g2"],
    preserves=["face_a_curvature", "face_b_curvature"],
    manufacturing={
        "cnc_5axis":         {"min_fillet_r_mm": 0.3},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.g2_filler_did_not_converge",
        "fm.face_has_no_edges",
    ],
    cost_hint=0.45,
    post_conditions=[PostCondition(kind="body_present")],
)
class SurfaceBlendG2(SkillBase):
    class Args(BaseModel):
        face_selector_a: SelectorRef
        face_selector_b: SelectorRef
        blend_radius_mm: float = Field(gt=0.0, le=50.0,
                                        description="Target blend radius hint (mm)")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.GeomAbs import GeomAbs_G2

        from phone_designer.skills._resolvers import resolve_faces

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces_a = resolve_faces(shape, args.face_selector_a, body=body)
        if not faces_a:
            raise RuntimeError(
                "surface_blend_g2: face_selector_a matched 0 faces"
            )
        faces_b = resolve_faces(shape, args.face_selector_b, body=body)
        if not faces_b:
            raise RuntimeError(
                "surface_blend_g2: face_selector_b matched 0 faces"
            )

        blend_face = _build_blend_face(
            faces_a[0], faces_b[0], GeomAbs_G2, args.blend_radius_mm,
        )

        history = EntityHistoryMap(
            rules={
                "face_a":     HistoryRule.MODIFIED_INHERIT,
                "face_b":     HistoryRule.MODIFIED_INHERIT,
                "blend_face": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(blend_face), history=history)
