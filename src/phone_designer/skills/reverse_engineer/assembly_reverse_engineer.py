"""assembly_reverse_engineer — first-class macro skill for multi-shell parts.

COMPLEX-CAD pass-6 (2026-06-09): the 0.913-on-RC_Buggy pipeline lived
only in run_logs/_tmp/run_assembly_re.py — invisible to the @skill
registry and unreachable from the planner. This macro skill promotes the
flow so future ARBITRARY assemblies route through the registry instead
of needing a hand-written script.

Flow:
    body
      ├─ split_into_components(min_volume_mm3) → components
      ├─ for top-N components by volume:
      │    ├─ ExtractFeatureCatalog
      │    ├─ PlanFromFeatureCatalog(base_step_kind=preserve_brep)
      │    ├─ PlanExecutor.run(initial_body=component_body)
      │    └─ FeatureFidelityDiff
      └─ aggregate match (volume-weighted)

extras schema::

    extras["assembly_re_results"] = [
        {"index": int, "volume_mm3": float, "match": float | None,
         "outcome": "PASS"|"FAIL", "skipped": bool, "error": str | None,
         "plan_steps": int | None},
        ...
    ]
    extras["aggregate_match_ratio"]   = float in [0, 1]
    extras["components_total"]        = int
    extras["components_processed"]    = int

post_conditions = [body_present]. Body returned unchanged (this is a
read-only diagnostic macro).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _plan_yaml_path() -> Path:
    """Match the runner's path so the PlanExecutor can load_plan after
    each PlanFromFeatureCatalog.apply() call writes to disk."""
    # Walk up from this file: src/phone_designer/skills/reverse_engineer/
    # → src/phone_designer/skills/ → src/phone_designer/ → src/ → repo/
    here = Path(__file__).resolve()
    repo = here.parents[4]
    return repo / "plans" / "reconstructed_plan.yaml"


@skill(
    name="assembly_reverse_engineer",
    category="reverse_engineer",
    level="macro",
    summary="Reverse-engineer a multi-shell assembly: split_into_components, "
            "run ExtractFeatureCatalog + PlanFromFeatureCatalog + "
            "FeatureFidelityDiff on the top-N components by volume, aggregate "
            "match as a volume-weighted average. Returns the per-component "
            "log + aggregate ratio in extras.",
    selector_kinds=[],
    history_rules={},
    produces_features=["assembly_re_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.too_many_shells", "fm.empty_assembly"],
    cost_hint=0.9,
    post_conditions=[PostCondition(kind="body_present")],
)
class AssemblyReverseEngineer(SkillBase):
    class Args(BaseModel):
        top_n: int = Field(
            default=10, ge=1, le=1000,
            description="Number of components to process, sorted by volume "
                        "(biggest first — biggest components carry the most "
                        "feature topology).",
        )
        min_volume_mm3: float = Field(
            default=100.0, ge=0.0,
            description="Components below this volume are treated as mesh "
                        "artefacts (bolts / washers / debris) and skipped at "
                        "split time. Default 100 mm³ catches anything "
                        "screw-head-sized or larger.",
        )
        base_step_kind: str = Field(
            default="preserve_brep",
            description="Passed to PlanFromFeatureCatalog for each component. "
                        "preserve_brep is the right choice for assembly "
                        "components (they are real BREP shells, not bbox "
                        "placeholders); 'box' is supported but rarely useful.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.repair.split_into_components import (
            SplitIntoComponents,
        )
        from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
            ExtractFeatureCatalog,
        )
        from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
            PlanFromFeatureCatalog,
        )
        from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
            FeatureFidelityDiff,
        )
        from phone_designer.plan.executor import PlanExecutor
        from phone_designer.plan.yaml_io import load_plan

        if body is None:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={
                    "assembly_re_results": [],
                    "aggregate_match_ratio": 0.0,
                    "components_total": 0,
                    "components_processed": 0,
                    "error": "no body",
                },
            )

        # 1. Split into components.
        split_extras = SplitIntoComponents().apply(
            body, {"min_volume_mm3": args.min_volume_mm3}
        ).extras
        comps: list = list(split_extras.get("components") or [])
        comps.sort(key=lambda c: -float(c.get("volume_mm3") or 0))
        top_comps = comps[: args.top_n]

        # 2. Per-component RE.
        plan_path = _plan_yaml_path()
        results: list[dict] = []
        weighted_match = 0.0
        total_weight = 0.0

        for i, c in enumerate(top_comps):
            entry: dict = {
                "index": i,
                "volume_mm3": float(c.get("volume_mm3") or 0),
                "match": None,
                "outcome": None,
                "skipped": False,
                "error": None,
                "plan_steps": None,
                "dt_s": None,
            }
            body_ref = c.get("body_ref")
            if body_ref is None or entry["volume_mm3"] <= 0:
                entry["skipped"] = True
                entry["error"] = "no body_ref or zero volume"
                results.append(entry)
                continue
            t0 = time.perf_counter()
            try:
                cat = ExtractFeatureCatalog().apply(
                    body_ref, {}
                ).extras["feature_catalog"]
                if cat.get("skipped"):
                    entry["skipped"] = True
                    entry["error"] = (
                        f"catalog skipped: {cat.get('reason')}"
                    )
                    results.append(entry)
                    continue
                PlanFromFeatureCatalog().apply(
                    body_ref,
                    {"catalog": cat, "base_step_kind": args.base_step_kind},
                )
                plan = load_plan(plan_path)
                entry["plan_steps"] = len(plan.steps)
                exec_result = PlanExecutor(plan).run(initial_body=body_ref)
                regen = exec_result.final_body
                entry["outcome"] = exec_result.outcome
                if regen is None:
                    entry["match"] = 0.0
                else:
                    regen_cat = ExtractFeatureCatalog().apply(
                        regen, {}
                    ).extras["feature_catalog"]
                    fid = FeatureFidelityDiff().apply(
                        regen,
                        {"catalog_a": cat, "catalog_b": regen_cat},
                    ).extras["feature_fidelity"]
                    m = float(fid.get("overall_match_ratio") or 0.0)
                    entry["match"] = m
                    weighted_match += m * entry["volume_mm3"]
                    total_weight += entry["volume_mm3"]
                entry["dt_s"] = round(time.perf_counter() - t0, 2)
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {str(exc)[:140]}"
                entry["dt_s"] = round(time.perf_counter() - t0, 2)
            results.append(entry)

        aggregate = (
            weighted_match / total_weight if total_weight > 0 else 0.0
        )
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "assembly_re_results": results,
                "aggregate_match_ratio": round(aggregate, 4),
                "components_total": len(comps),
                "components_processed": len(results),
            },
        )
