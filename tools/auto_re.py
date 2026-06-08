"""auto_re.py — auto-routing reverse-engineering CLI.

COMPLEX-CAD pass-7 (2026-06-09): wires the three pass-6 inspect-side
skills into a one-shot CLI so an arbitrary STEP file gets routed to the
right RE strategy without the user having to know the body shape ahead
of time.

Flow:
    ImportStep(path) → body
    BodyOrientationCheck(body) — warn if signed volume < 0
    BaseStepKindChooser(body) → "box" | "preserve_brep" | "assembly"
    if hint == "assembly":
        AssemblyReverseEngineer(body) — per-component RE
    else:
        ExtractFeatureCatalog → PlanFromFeatureCatalog(base_step_kind=hint)
        PlanExecutor.run(initial_body=body if hint=="preserve_brep" else None)
        FeatureFidelityDiff(orig_cat, regen_cat)
    print human-readable summary
    exit 0 if overall match ≥ cutoff else 1

Usage:
    python tools/auto_re.py PATH/TO/PART.step
    python tools/auto_re.py PATH/TO/ASSEMBLY.step --top-n 20
"""
from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _register_all_skills() -> int:
    import phone_designer.skills as skills_pkg
    n = 0
    for mod in pkgutil.walk_packages(skills_pkg.__path__, skills_pkg.__name__ + "."):
        try:
            importlib.import_module(mod.name); n += 1
        except Exception:
            pass
    return n


def _import_body(path: Path):
    from phone_designer.skills.create.import_step import ImportStep
    return ImportStep().apply(None, {"path": str(path)}).body


def _check_orientation(body) -> dict:
    from phone_designer.skills.inspect.body_orientation_check import (
        BodyOrientationCheck,
    )
    return BodyOrientationCheck().apply(body, {}).extras["body_orientation"]


def _choose_base(body) -> dict:
    from phone_designer.skills.inspect.base_step_kind_chooser import (
        BaseStepKindChooser,
    )
    return BaseStepKindChooser().apply(body, {}).extras["base_step_kind_hint"]


def _run_standard_re(body, base_step_kind: str) -> dict:
    """Standard ExtractFeatureCatalog → Plan → Executor → FidelityDiff."""
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

    cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    if cat.get("skipped"):
        return {"mode": "standard", "skipped": cat.get("reason"), "match": 0.0}
    PlanFromFeatureCatalog().apply(
        body, {"catalog": cat, "base_step_kind": base_step_kind}
    )
    plan_path = REPO / "plans" / "reconstructed_plan.yaml"
    plan = load_plan(plan_path)
    initial = body if base_step_kind == "preserve_brep" else None
    exec_result = PlanExecutor(plan).run(initial_body=initial)
    regen = exec_result.final_body or body
    regen_cat = ExtractFeatureCatalog().apply(regen, {}).extras["feature_catalog"]
    fid = FeatureFidelityDiff().apply(
        regen, {"catalog_a": cat, "catalog_b": regen_cat}
    ).extras["feature_fidelity"]
    return {
        "mode": "standard",
        "base_step_kind": base_step_kind,
        "match": float(fid.get("overall_match_ratio") or 0),
        "outcome": exec_result.outcome,
        "plan_steps": len(plan.steps),
        "fidelity": fid,
    }


def _run_assembly_re(body, top_n: int, min_volume_mm3: float) -> dict:
    from phone_designer.skills.reverse_engineer.assembly_reverse_engineer import (
        AssemblyReverseEngineer,
    )
    extras = AssemblyReverseEngineer().apply(
        body,
        {"top_n": top_n, "min_volume_mm3": min_volume_mm3,
         "base_step_kind": "preserve_brep"},
    ).extras
    return {
        "mode": "assembly",
        "match": float(extras.get("aggregate_match_ratio") or 0),
        "components_total": extras.get("components_total"),
        "components_processed": extras.get("components_processed"),
        "per_component": extras.get("assembly_re_results") or [],
    }


def print_summary(orientation: dict, hint: dict, result: dict, pass_cutoff: float) -> None:
    print("\n=== auto_re summary ===")
    print(f"orientation : signed={orientation['signed_volume_mm3']} "
          f"magnitude={orientation['magnitude_mm3']} "
          f"solidity={orientation.get('solidity_ratio')} "
          f"inverted={orientation['inverted']}")
    if orientation.get("advice"):
        print(f"  advice    : {orientation['advice']}")
    print(f"hint        : {hint['chosen']} — {hint['rationale']}")

    mode = result["mode"]
    print(f"mode        : {mode}")
    if mode == "standard":
        print(f"base_step   : {result.get('base_step_kind')}")
        print(f"plan_steps  : {result.get('plan_steps')}")
        print(f"outcome     : {result.get('outcome')}")
        print(f"match       : {result['match']:.4f}")
        fid = result.get("fidelity") or {}
        for k, v in (fid.get("by_kind") or {}).items():
            a = v.get("a", 0); b = v.get("b", 0)
            if a == 0 and b == 0:
                continue
            print(f"  {k:<20} a={a:>4} b={b:>4} matched={v.get('matched', 0):>4}")
    else:
        print(f"components  : {result.get('components_processed')}/"
              f"{result.get('components_total')} processed")
        print(f"aggregate   : {result['match']:.4f}")
        for c in (result.get("per_component") or [])[:10]:
            print(f"  comp[{c['index']:>2d}] vol={c['volume_mm3']:>10.0f} "
                  f"match={c['match']} outcome={c.get('outcome')} "
                  f"steps={c.get('plan_steps')} dt={c.get('dt_s')}s")

    print(f"\nresult: {'PASS' if result['match'] >= pass_cutoff else 'FAIL'} "
          f"(match={result['match']:.4f} cutoff={pass_cutoff})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path", type=Path, help="STEP file to reverse-engineer.")
    ap.add_argument(
        "--pass-cutoff", type=float, default=0.5,
        help="Exit 0 if overall match ≥ this (default 0.5).",
    )
    ap.add_argument(
        "--top-n", type=int, default=10,
        help="Assembly mode: components to process by descending volume.",
    )
    ap.add_argument(
        "--min-volume-mm3", type=float, default=100.0,
        help="Assembly mode: skip components below this volume.",
    )
    ap.add_argument(
        "--force", choices=["box", "preserve_brep", "assembly"], default=None,
        help="Override the BaseStepKindChooser hint.",
    )
    args = ap.parse_args(argv)

    _register_all_skills()
    body = _import_body(args.path)

    orientation = _check_orientation(body)
    hint = _choose_base(body)
    chosen = args.force or hint["chosen"]
    print(f"[auto_re] {args.path.name}: orientation inverted={orientation['inverted']}, "
          f"hint={hint['chosen']}, running={chosen}")

    if chosen == "assembly":
        result = _run_assembly_re(body, args.top_n, args.min_volume_mm3)
    else:
        result = _run_standard_re(body, chosen)

    print_summary(orientation, hint, result, args.pass_cutoff)
    return 0 if result["match"] >= args.pass_cutoff else 1


if __name__ == "__main__":
    sys.exit(main())
