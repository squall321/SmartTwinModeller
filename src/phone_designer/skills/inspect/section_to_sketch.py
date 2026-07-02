"""section_to_sketch — atomic, read-only. Cross-section a body → an executable sketch.

The "Convert Entities from a section" op: cut the body with a plane and recover the
section as ONE ordered closed loop, emitted as an EXECUTABLE SketchSpec dict that
sketch_extrude / sketch_revolve / sketch_loft / the modify_pocket skills consume
directly (section a solid → sketch → re-extrude). Derives a profile from existing
geometry instead of hand-authoring it.

Thin exposure of the existing ``inspect/_section_curve.recover_section_loop`` engine
(already SketchSpec-consumable), which previously was reachable only through rigid RE
auto-classifiers — now a user-facing atomic. Returns the sketch in ``extras`` (the
body is unchanged); a caller feeds ``extras['sketch']`` straight into a create skill.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="section_to_sketch",
    category="inspect",
    level="atomic",
    summary="Cross-section the body at a plane and recover the section as an "
            "EXECUTABLE SketchSpec dict (polygon | composite of line/arc/spline | "
            "bspline_closed) — feed extras['sketch'] straight into sketch_extrude/"
            "revolve/loft to re-derive a profile from existing geometry (SolidWorks "
            "Convert Entities). Read-only. Exposes recover_section_loop.",
    selector_kinds=[],
    history_rules={},
    produces_features=["section_sketch"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.empty_section", "fm.section_open", "fm.too_many_faces"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class SectionToSketch(SkillBase):
    class Args(BaseModel):
        plane_origin: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0), description="A point on the section plane.")
        plane_normal: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 1.0), description="The section-plane normal.")
        simplify_tol_mm: float = Field(
            default=0.01, ge=0.0,
            description="Douglas-Peucker tolerance on straight runs (mm).")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.inspect._section_curve import recover_section_loop

        if body is None:
            raise ValueError("fm.empty_section: section_to_sketch needs a body.")
        shape = body.wrapped if hasattr(body, "wrapped") else body

        rec = recover_section_loop(
            shape,
            tuple(args.plane_origin),
            tuple(args.plane_normal),
            simplify_tol_mm=args.simplify_tol_mm,
        )
        # The face-count guard SKIPS the section without cutting — that is not
        # an empty section (the plane may well pass through the body), so it
        # must not be mislabeled fm.empty_section ("plane may miss the body").
        if rec.get("skipped"):
            raise ValueError(
                f"fm.too_many_faces: body has {rec['face_count']} faces > "
                f"limit {rec['limit']} — section skipped by the face-count "
                "guard (raise PHONE_DESIGNER_MAX_FACE_COUNT to override).")
        sketch = rec.get("sketch")
        if sketch is None or rec.get("empty"):
            raise ValueError(
                "fm.empty_section: the plane produced no closed section loop "
                "(it may miss the body — check plane_origin/normal).")
        if not rec.get("closed", True):
            raise ValueError(
                "fm.section_open: the recovered section loop is not closed — the "
                "cut may graze a face; nudge the plane.")

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "sketch": sketch,
                "loop_kind": rec.get("loop_kind"),
                "area_mm2": rec.get("area_mm2"),
                "n_points": rec.get("n_points"),
                "n_loops": rec.get("n_loops"),
                "plane_origin": list(args.plane_origin),
                "plane_normal": list(args.plane_normal),
            },
        )
