"""infill_region_tag — atomic. Tag a region of the body with an infill spec.

3D printer slicers fill solid regions with a sparse internal pattern (grid,
gyroid, honeycomb, cubic, ...). The chosen pattern and percentage trade off
strength, weight, print time, and surface quality. This skill does not modify
the geometry — it attaches a tag (``body._pd_infill``) describing the desired
infill of a region so a downstream exporter / slicer hand-off step can pick
it up.

A face_selector or selector pointing at a region is interpreted as "this
region of the body should receive the given infill". The selector itself is
stored verbatim so the slicer hand-off can re-resolve it on the final mesh.

extras["infill_tag"] schema:
    {
        "region": {selector dict ...},
        "pct": float,           # 0–100 (clamped to allowed range)
        "pattern": str,         # grid | gyroid | honeycomb | cubic
    }

Also written to body._pd_infill for tag-style propagation through later
skills (mirroring the existing _pd_tags channel).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


InfillPattern = Literal["grid", "gyroid", "honeycomb", "cubic"]


@skill(
    name="infill_region_tag",
    category="modify/3dprint",
    level="atomic",
    summary="Tag a region of the body with an infill density and pattern for "
            "slicer hand-off. Does not modify geometry; writes "
            "body._pd_infill={region,pct,pattern} and extras['infill_tag'].",
    selector_kinds=["faces", "edges"],
    history_rules={},
    produces_features=["infill_tag"],
    preserves=["body_topology"],
    manufacturing={
        "fdm":  {"extras": {"infill_default_pct": 20,
                            "infill_default_pattern": "gyroid",
                            "purpose": "internal_sparse_infill"}},
        "sla":  {"extras": {"hollowing_supported": True,
                            "infill_pattern": "honeycomb"}},
        "sls":  {"extras": {"self_supporting": True,
                            "infill_unnecessary": True}},
    },
    failure_modes=["fm.bad_infill_pct", "fm.unknown_infill_pattern"],
    cost_hint=0.05,
    # Read-only — just ensure body still exists.
    post_conditions=[PostCondition(kind="body_present")],
)
class InfillRegionTag(SkillBase):
    class Args(BaseModel):
        face_selector_or_body_region: SelectorRef
        infill_pct: float = Field(default=20.0, ge=0.0, le=100.0)
        pattern: InfillPattern = "gyroid"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        # Serialise the selector to a JSON-safe dict for downstream consumers.
        try:
            region_dict = args.face_selector_or_body_region.model_dump()
        except Exception:
            region_dict = {"kind": getattr(
                args.face_selector_or_body_region, "kind", "unknown",
            )}

        tag = {
            "region": region_dict,
            "pct": float(args.infill_pct),
            "pattern": args.pattern,
        }

        # Attach to body for tag-style propagation. _pd_infill may already
        # exist on the body if a prior infill_region_tag step ran — accumulate
        # into a list so multiple regions can have distinct infill specs.
        existing = getattr(body, "_pd_infill", None)
        if existing is None:
            new_infill: Any = tag
        elif isinstance(existing, list):
            new_infill = list(existing) + [tag]
        else:
            new_infill = [existing, tag]
        try:
            body._pd_infill = new_infill
        except Exception:
            # build123d Part subclasses may not accept arbitrary attrs in
            # rare cases — fall back to extras only.
            pass

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"infill_tag": tag},
        )
