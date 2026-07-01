"""intersect — atomic. body ∩ other_body (the third boolean).

The keep-common half of the boolean set, alongside union (∪) and subtract (−).
Returns ONLY the material shared by both bodies — used for keep-common-material,
trimming a body to a tool envelope, and lens / overlap-volume construction. 'other'
is the same OtherBodySpec union union/subtract accept (STEP file, XDE assembly part,
or an inline box/cylinder primitive).

HONEST: two non-overlapping bodies have an EMPTY intersection — OCCT returns a valid
but volume-0 shape at IsDone=True, which is a lie if handed back as a "solid". So a
near-zero result is a structured refusal (fm.no_intersection), matching the
volume>1e-6 is_solid convention used across the create skills.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.compose._load_other import OtherBodySpec, load_other_shape

_EPS_VOL = 1e-6


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(float(g.Mass()))


@skill(
    name="intersect",
    category="compose",
    level="atomic",
    summary="Boolean intersect (common) — keep ONLY the material shared by the "
            "current body and 'other' (the third boolean, alongside union and "
            "subtract). 'other' can be a STEP file, an XDE assembly part, or an "
            "inline box/cylinder primitive. Refuses a non-overlapping pair "
            "(fm.no_intersection — empty common volume). is_solid gated on volume>0.",
    selector_kinds=[],
    history_rules={
        "body_faces":         HistoryRule.MODIFIED_INHERIT,
        "other_faces":        HistoryRule.MODIFIED_INHERIT,
        "intersection_faces": HistoryRule.GENERATED_NEW,
    },
    produces_features=["boolean_common"],
    preserves=[],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.no_intersection", "fm.boolean_failed"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class Intersect(SkillBase):
    class Args(BaseModel):
        other: OtherBodySpec

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

        shape = body.wrapped if hasattr(body, "wrapped") else body
        other_shape = load_other_shape(args.other)

        common = BRepAlgoAPI_Common(shape, other_shape)
        common.Build()
        if not common.IsDone():
            raise RuntimeError(
                "fm.boolean_failed: BRepAlgoAPI_Common failed (IsDone=False)")
        new_shape = common.Shape()

        vol = _volume(new_shape)
        if new_shape is None or new_shape.IsNull() or vol <= _EPS_VOL:
            raise ValueError(
                f"fm.no_intersection: common volume {vol:.6g}mm³ ≈ 0 — the two "
                "bodies do not overlap, so their intersection is empty.")

        history = EntityHistoryMap(
            rules={
                "body_faces":         HistoryRule.MODIFIED_INHERIT,
                "other_faces":        HistoryRule.MODIFIED_INHERIT,
                "intersection_faces": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
