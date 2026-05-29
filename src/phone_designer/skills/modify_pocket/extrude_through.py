"""extrude_through — atomic. face → sketch → 관통 cutout."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec


@skill(
    name="extrude_through",
    category="modify/pocket",
    level="atomic",
    summary="Extrude a 2D sketch through the body — cuts a through-hole shaped like the sketch. "
            "Used for non-circular cutouts (USB-C, speaker grille window, etc.).",
    selector_kinds=["faces"],
    history_rules={
        "target_face":  HistoryRule.MODIFIED_INHERIT,
        "cutout_walls": HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    produces_features=["through_cutout"],
    preserves=["outer_envelope_outside_cutout"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"max_aspect_ratio": 6.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "undercut_allowed": False},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.sketch_outside_face", "fm.cutout_too_close_to_edge"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class ExtrudeThrough(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        sketch: SketchSpec

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._sketch_to_solid import build_pocket_tool

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"face_selector matched 0 faces: {args.face_selector.model_dump()}"
            )
        target_face = faces[0]

        # body 의 bbox 를 충분히 넘는 깊이로 — through 보장
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        bb = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, bb)
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        diagonal = ((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2) ** 0.5
        through_depth = diagonal * 1.5    # 충분히 깊게

        tool = build_pocket_tool(target_face, args.sketch, through_depth, direction="into")

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("extrude_through cut 실패")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":  HistoryRule.MODIFIED_INHERIT,
                "cutout_walls": HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
