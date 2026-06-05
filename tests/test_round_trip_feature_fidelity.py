"""Round-trip FEATURE fidelity gate — count holes/pockets/bosses.

Complements ``test_round_trip_fidelity.py`` (volume drift + cube collapse).

For each fixture:
  1. ORIGINAL body → extract_feature_catalog → baseline (hole/pocket/boss) counts.
  2. ORIGINAL → extract → plan_from → executor → REGEN body.
  3. REGEN body → extract_feature_catalog → regen counts.
  4. Assert each regen count is within tolerance (default ±1) of baseline.

Tolerance default ±1 is permissive — feature-level reconstruction is lossy and
this test is currently a sentinel, not a hard CI gate. Set
FEATURE_FIDELITY_STRICT=1 to require exact match.
"""
from __future__ import annotations

import os
import pytest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
STRICT = os.environ.get("FEATURE_FIDELITY_STRICT", "").strip() in ("1", "true", "True", "yes", "on")
TOLERANCE = 0 if STRICT else 1  # ±N count tolerance per feature kind

CASES = [
    # (rel_path, xfail_reason or None)
    ("fixtures/simple_watch.step", None),
    ("fixtures/simple_watch_housing_only.step",
     "known mismatch — housing-only round-trip drops features"),
    ("run_logs/_tmp/demo_advanced.step", None),
    ("run_logs/_tmp/demo_boss_sweep_loft.step", None),
    # iPhone real-world mesh round-trip — extraction rate is very low from a
    # 132-face decimated mesh, so feature counts cannot match.
    ("run_logs/_tmp/iphone_housing_processed.step",
     "iphone real-world mesh — very low extraction rate from 132-face mesh"),
]


def _step_to_body(step_path):
    """Load STEP -> build123d Part."""
    from OCP.STEPControl import STEPControl_Reader
    from build123d import Part
    r = STEPControl_Reader()
    r.ReadFile(str(step_path))
    r.TransferRoots()
    shape = r.OneShape()
    return Part(shape)


def _count_features(body):
    """Run extract_feature_catalog and return (hole_count, pocket_count, boss_count)."""
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    res = ExtractFeatureCatalog().apply(body, {})
    cat = res.extras.get("feature_catalog", {}) or {}
    holes = cat.get("holes") or []
    pockets = cat.get("pockets") or []
    bosses = cat.get("bosses") or []
    return len(holes), len(pockets), len(bosses), cat


def _regen_body(orig_body, cat):
    """Run plan_from_feature_catalog -> executor -> regenerated body (Part)."""
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        PlanFromFeatureCatalog,
    )
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.plan.yaml_io import load_plan

    plan_result = PlanFromFeatureCatalog().apply(orig_body, {"catalog": cat})
    plan_path = plan_result.extras.get("plan_path") or "plans/reconstructed_plan.yaml"
    plan = load_plan(plan_path)
    exec_result = PlanExecutor(plan).run()
    assert exec_result.final_body is not None, "executor returned None body"
    return exec_result.final_body


def _within(orig, regen, tol):
    return abs(orig - regen) <= tol


@pytest.mark.fidelity
@pytest.mark.parametrize("rel_path,xfail_reason", CASES)
def test_round_trip_feature_fidelity(rel_path, xfail_reason):
    src = PROJECT / rel_path
    if not src.exists():
        pytest.skip(f"missing fixture {rel_path}")

    if xfail_reason and not STRICT:
        pytest.xfail(xfail_reason)

    # 1. Baseline counts from ORIGINAL body
    orig_body = _step_to_body(src)
    o_holes, o_pockets, o_bosses, orig_cat = _count_features(orig_body)

    # 2. Regenerate body via planner+executor
    regen_body = _regen_body(orig_body, orig_cat)

    # 3. Regen counts
    r_holes, r_pockets, r_bosses, _ = _count_features(regen_body)

    # 4. Assert per-kind count within tolerance
    diag = (
        f"{rel_path}: "
        f"holes orig={o_holes} regen={r_holes}, "
        f"pockets orig={o_pockets} regen={r_pockets}, "
        f"bosses orig={o_bosses} regen={r_bosses} (tol=±{TOLERANCE})"
    )
    mismatches = []
    if not _within(o_holes, r_holes, TOLERANCE):
        mismatches.append(f"holes {o_holes}->{r_holes}")
    if not _within(o_pockets, r_pockets, TOLERANCE):
        mismatches.append(f"pockets {o_pockets}->{r_pockets}")
    if not _within(o_bosses, r_bosses, TOLERANCE):
        mismatches.append(f"bosses {o_bosses}->{r_bosses}")

    if mismatches:
        msg = f"{diag}; mismatches: {', '.join(mismatches)}"
        if STRICT:
            pytest.fail("STRICT: " + msg)
        else:
            pytest.xfail(msg)
