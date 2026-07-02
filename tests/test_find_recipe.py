"""find_recipe + load_recipes (mcp_support._recipes) — track 2-5.

Pins:
  * the corpus loads, schema-validates and is cached;
  * find_recipe surfaces the bent-pipe SWEEP recipe for both the Korean
    ('둥근 관을 구부려') and English ('bent pipe') phrasings — the roadmap's
    named retrieval pin;
  * results carry the ready-to-run few-shot payload (spec + expected) and are
    strict-JSON-safe;
  * NEGATIVE recipes surface WITH their fm.* refusal token so a client learns
    the refusal before tripping it;
  * malformed committed recipes fail LOUDLY (never silently skipped).

No geometry is executed here — execution is pinned by test_recipes_execute.py.
"""
from __future__ import annotations

import json

import pytest

from phone_designer.mcp_support._recipes import (
    clear_recipe_cache,
    find_recipe,
    load_recipes,
    recipe_by_name,
)


# ── loading + caching ─────────────────────────────────────────────────────────

def test_corpus_loads_with_at_least_40_valid_recipes():
    recipes = load_recipes()
    assert len(recipes) >= 40
    names = [r["name"] for r in recipes]
    assert len(set(names)) == len(names), "duplicate recipe names"
    assert names == sorted(names), "load_recipes must be name-sorted"


def test_load_recipes_is_cached_and_reloadable():
    a = load_recipes()
    assert load_recipes() is a          # cache hit — same object
    b = load_recipes(force_reload=True)
    assert b is not a and b == a        # reload — fresh but equal
    clear_recipe_cache()
    assert load_recipes() is not b


def test_malformed_recipe_raises_with_file_path(tmp_path):
    bad = tmp_path / "broken_recipe.yaml"
    bad.write_text("name: broken_recipe\nintent_en: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="broken_recipe"):
        load_recipes(tmp_path)


def test_recipe_by_name_exact_and_missing():
    r = recipe_by_name("sketch_sweep_bent_pipe")
    assert r["spec"][0]["op"] == "sketch_sweep"
    with pytest.raises(KeyError):
        recipe_by_name("no_such_recipe")


# ── the roadmap retrieval pin: bent pipe → sweep recipe ──────────────────────

def test_find_recipe_korean_bent_pipe_query_hits_sweep_recipe():
    results = find_recipe("둥근 관을 구부려")
    names = [r["name"] for r in results]
    assert "sketch_sweep_bent_pipe" in names
    assert names[0] == "sketch_sweep_bent_pipe"   # and it is the TOP hit


def test_find_recipe_english_bent_pipe_query_hits_sweep_recipe():
    results = find_recipe("bent pipe", top_k=5)
    assert "sketch_sweep_bent_pipe" in [r["name"] for r in results]


# ── few-shot payload shape + JSON safety ─────────────────────────────────────

def test_results_carry_runnable_spec_and_pinned_expected():
    (top, *_rest) = find_recipe("둥근 관을 구부려", top_k=1)
    assert isinstance(top["spec"], list) and top["spec"], \
        "few-shot spec payload missing"
    lo, hi = top["expected"]["volume_mm3"]
    assert 0 < lo <= hi
    assert top["score"] > 0
    assert top["intent"] == top["intent_en"]
    assert top["intent_kr"]


def test_results_are_strict_json_safe():
    results = find_recipe("box plate", top_k=10)
    assert results
    json.dumps(results, allow_nan=False)   # raises on inf/nan


def test_negative_recipe_surfaces_with_refusal_token():
    # a client asking about a sharp-cornered sweep should learn the refusal
    # BEFORE tripping it — the negative recipe is retrievable and carries the
    # pinned fm.* token in its expected block.
    results = find_recipe("sweep a sharp 90 degree corner kink refusal",
                          top_k=5)
    neg = [r for r in results if r["name"] == "neg_sweep_kinked_path_refused"]
    assert neg, f"negative kink recipe not in top-5: " \
                f"{[r['name'] for r in results]}"
    assert neg[0]["expected"]["error_contains"] == \
        "fm.sweep_tangent_discontinuity"


# ── query handling ───────────────────────────────────────────────────────────

def test_unmatched_query_returns_empty_not_noise():
    assert find_recipe("zzzz qqqq wwww nonsense") == []


def test_top_k_is_respected_and_clamped():
    assert len(find_recipe("box", top_k=3)) <= 3
    assert len(find_recipe("box", top_k=0)) <= 1   # clamped up to 1


def test_empty_query_refused():
    with pytest.raises(ValueError, match="non-empty"):
        find_recipe("   ")
