"""Recipes corpus anti-rot pin (track 2-5).

EVERY committed recipe in recipes/*.yaml is re-EXECUTED here through
GenerateFromSpec and its expected invariants asserted:

  * positive recipes: ok + is_solid + volume inside the recorded ±2% range
    (+ bbox extents inside their recorded range when present);
  * NEGATIVE recipes: the spec FAILS on the declared op with the pinned fm.*
    refusal token (teaching the structured refusal is the recipe's payload).

The expected numbers were produced by EXECUTING each recipe at authoring time
(recipes/README.md) — this test is what keeps them true forever. A recipe that
stops meeting its own invariants (skill arg change, geometry regression, spec
rot) fails HERE, before any LLM ever sees a stale few-shot.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RECIPES_DIR = REPO_ROOT / "recipes"
RECIPE_FILES = sorted(RECIPES_DIR.glob("*.yaml"))


def _generated(spec):
    from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
    return GenerateFromSpec().apply(None, {"spec": spec}).extras["generated"]


# ── corpus shape ──────────────────────────────────────────────────────────────

def test_corpus_has_at_least_40_recipes():
    assert len(RECIPE_FILES) >= 40, (
        f"recipes corpus shrank to {len(RECIPE_FILES)} files — the roadmap "
        f"floor is 40")


def test_corpus_contains_the_kink_negative_recipe():
    # the roadmap's pinned NEGATIVE example: a kinked sweep path must be a
    # structured refusal, taught as a recipe.
    path = RECIPES_DIR / "neg_sweep_kinked_path_refused.yaml"
    assert path.is_file()
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["expected"]["ok"] is False
    assert doc["expected"]["error_contains"] == "fm.sweep_tangent_discontinuity"
    assert doc["expected"]["failing_op"] == "sketch_sweep"


# ── THE pin: every recipe executes and meets its invariants ──────────────────

@pytest.mark.parametrize(
    "path", RECIPE_FILES, ids=[p.stem for p in RECIPE_FILES])
def test_recipe_executes_and_meets_expected_invariants(path: Path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    exp = doc["expected"]
    gen = _generated(doc["spec"])

    if "error_contains" in exp:
        # NEGATIVE recipe — the refusal IS the expected behaviour.
        assert gen["ok"] is False, (
            f"{path.stem}: negative recipe unexpectedly built OK — the "
            f"refusal it teaches ({exp['error_contains']}) no longer fires")
        failed = [s for s in gen["steps"]
                  if s["op"] == exp["failing_op"] and s["status"] != "pass"]
        assert failed, (
            f"{path.stem}: expected op '{exp['failing_op']}' to fail; "
            f"steps={gen['steps']}")
        joined = " | ".join(s.get("error") or "" for s in failed)
        assert exp["error_contains"] in joined, (
            f"{path.stem}: refusal token '{exp['error_contains']}' not in "
            f"step error(s): {joined}")
        return

    # positive recipe
    assert gen["ok"] is True, (
        f"{path.stem}: build failed — steps={gen['steps']} "
        f"spec_errors={gen['spec_errors']}")
    assert gen["is_solid"] == exp["is_solid"]
    lo, hi = exp["volume_mm3"]
    assert lo <= gen["volume_mm3"] <= hi, (
        f"{path.stem}: volume {gen['volume_mm3']} outside pinned "
        f"[{lo}, {hi}]")
    if exp.get("bbox_mm"):
        (bmin, bmax), got = exp["bbox_mm"], gen["bbox_mm"]
        assert got is not None
        for axis in range(3):
            assert bmin[axis] <= got[axis] <= bmax[axis], (
                f"{path.stem}: bbox[{axis}] = {got[axis]} outside "
                f"[{bmin[axis]}, {bmax[axis]}] (full bbox {got})")
