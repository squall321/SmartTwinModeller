"""place_freeform_solid — atomic create skill (FREEFORM RE-SOLIDIFY, 2026-06-15).

Place a re-solidified freeform boundary as the base body for a reconstruction.

This is the executable counterpart of ``inspect/recover_freeform_solid``: it
loads a STEP file holding the body's as-imported boundary and RE-SOLIDIFIES it
via the same proven OCCT 7.8 recipe (sew the COMPLETE face set → largest shell
→ ShapeFix_Shell → MakeSolid → ShapeFix_Solid → BRepCheck.IsValid). The result
is the watertight freeform solid — the part's TRUE outer geometry recovered
near-losslessly (hausdorff ~0, volume == original).

HONEST FRAMING
--------------
The placed solid REUSES the body's own boundary faces, so it is the FULL
reconstruction of the as-imported outer geometry — every pocket / hole / boss
already present in the boundary is carried verbatim. When the planner emits this
as the base it must NOT also re-cut those features (that would double-count); it
labels the plan honestly as ``freeform_shell (re-solidified boundary)``. This is
a base-stock recovery, NOT a parametric rebuild-from-catalog: no primitive is
fit and no editable feature tree is produced.

Args round-trip through the plan executor as a STEP ``path`` + ``sew_tol_mm`` so
the step is fully YAML-serializable and re-runnable (no in-memory shape is
smuggled through the plan).

post_conditions = [body_present]. Same create-skill contract as ``box`` /
``extrude_profile_world``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _load_step_shape(path: str):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"place_freeform_solid: STEP not found: {p}")
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(p))
    if status != IFSelect_RetDone:
        raise RuntimeError(
            f"place_freeform_solid: STEP parse failed: {p} (status={status})"
        )
    reader.TransferRoots()
    return reader.OneShape()


@skill(
    name="place_freeform_solid",
    category="create",
    level="atomic",
    summary="Place a re-solidified freeform boundary as the base body: load a "
            "STEP boundary and sew its COMPLETE face set into a watertight "
            "solid (proven OCCT 7.8 recipe). Recovers the part's TRUE outer "
            "geometry near-losslessly (hausdorff ~0) by reusing its own faces "
            "— the FULL reconstruction base, not a parametric rebuild.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["freeform_solid"],
    preserves=[],
    manufacturing={},  # re-solidified import — no process inference
    failure_modes=["fm.file_not_found", "fm.step_parse_failed",
                   "fm.resolidify_open_boundary"],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class PlaceFreeformSolid(SkillBase):
    class Args(BaseModel):
        path: str = Field(
            min_length=1,
            description="STEP file holding the body's as-imported boundary "
                        "(repo-relative or absolute). Re-solidified at runtime.",
        )
        sew_tol_mm: float = Field(
            default=0.1, gt=0.0, le=5.0,
            description="BRepBuilderAPI_Sewing tolerance (mm). 0.1 mm is the "
                        "proven default that closes the Ventilator boundary.",
        )
        box_shift: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="world→box translation (-cx, -cy, -zmin) so the placed "
                        "solid lands in the box frame the placeholder box base "
                        "would have occupied. (0,0,0) keeps world coords.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        from phone_designer.skills.inspect.recover_freeform_solid import (
            recover_solid_from_boundary,
        )

        shape = _load_step_shape(args.path)
        solid, _sewed, _shells, _fixed = recover_solid_from_boundary(
            shape, args.sew_tol_mm,
        )
        if solid is None:
            # Honest hard failure — the boundary did NOT close into a valid
            # solid. We never fabricate one; the step FAILs so the plan can
            # fall back (the planner only emits this step after its own
            # solidify gate passes, so this path is a runtime safety net).
            raise RuntimeError(
                "place_freeform_solid: boundary did not re-solidify into a "
                f"watertight valid solid (path={args.path})"
            )

        # Land the recovered solid in the box frame if requested.
        sx, sy, sz = (float(c) for c in args.box_shift)
        if sx or sy or sz:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
            from OCP.gp import gp_Trsf, gp_Vec

            trsf = gp_Trsf()
            trsf.SetTranslation(gp_Vec(sx, sy, sz))
            solid = BRepBuilderAPI_Transform(solid, trsf, True).Shape()

        if solid is None or solid.IsNull():
            raise RuntimeError("place_freeform_solid: produced a null solid")

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(solid), history=history)
