"""LOD / face-budget guard + opt-in cache (pillar-perf phase-4, 2026-06-15).

The per-face detector pipeline is NOT cheap on assembly leaves — a single
493-face RC_Buggy solid costs ~220 s in ExtractFeatureCatalog alone, far below
the 16 000 DEFAULT_MAX_FACE_COUNT hard cap, so the whole assembly used to time
out. Two levers fix it:

  * LOD guard — a component above ``lod_max_faces`` is reconstructed COARSE
    (bbox primitive base, detectors skipped) and flagged lod='coarse' /
    result_grade='estimate' instead of paying the full catalog. The catalog
    still covers every component (partial-but-complete). match stays None — a
    coarse bbox base must NOT claim a feature match (no fake-accuracy).
  * opt-in on-disk cache — keyed by geometric_signature; a re-run skips
    re-detection on a hit. Default OFF ⇒ behaviour unchanged.

These tests force a tiny ``lod_max_faces`` so an ordinary 6-face box trips the
coarse path — no 493-face monster required.
"""
from __future__ import annotations

import pytest
from build123d import Box as B3dBox, Part, Pos
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound

from phone_designer.skills.reverse_engineer._re_cache import ReCache
from phone_designer.skills.reverse_engineer.assembly_reverse_engineer import (
    AssemblyReverseEngineer,
)


def _compound(shapes):
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    for s in shapes:
        builder.Add(comp, s.wrapped)
    return Part(comp)


def _two_distinct_boxes():
    """Two DIFFERENT solid boxes ⇒ two signature classes (so two RE runs)."""
    a = B3dBox(20, 20, 10)
    b = Pos(60, 0, 0) * B3dBox(30, 15, 8)
    return _compound([a, b])


# ── LOD coarse path ─────────────────────────────────────────────────────────


def test_over_budget_component_takes_coarse_path_and_is_flagged():
    """lod_max_faces=2 ⇒ every 6-face box trips the budget and is
    reconstructed COARSE: lod='coarse', result_grade='estimate', match None,
    a bbox_fill_ratio, and NOT skipped (the catalog still covers it)."""
    body = _two_distinct_boxes()
    res = AssemblyReverseEngineer().apply(
        body, {"top_n": 10, "max_workers": 1, "lod_max_faces": 2}
    )
    e = res.extras
    assert e["components_processed"] == 2
    assert e["components_coarse"] == 2
    assert e["components_full"] == 0
    assert e["components_skipped"] == 0
    for r in e["assembly_re_results"]:
        assert r["lod"] == "coarse"
        assert r["result_grade"] == "estimate"
        assert r["outcome"] == "COARSE"
        assert r["skipped"] is False
        # Honest fallback: a coarse bbox base does NOT claim a feature match.
        assert r["match"] is None
        # A solid box exactly fills its bbox ⇒ fill ratio ~1.0.
        assert r["bbox_fill_ratio"] == pytest.approx(1.0, abs=1e-3)
        assert "coarse_reason" in r
    # No feature matches ⇒ the volume-weighted aggregate is 0.0 (honest: we
    # never invented a match for the coarse bodies).
    assert e["aggregate_match_ratio"] == 0.0


def test_full_path_when_under_budget():
    """A generous lod_max_faces ⇒ the full detector path runs (lod='full',
    grade='measured', a real match)."""
    body = _two_distinct_boxes()
    res = AssemblyReverseEngineer().apply(
        body, {"top_n": 10, "max_workers": 1, "lod_max_faces": 1000}
    )
    e = res.extras
    assert e["components_full"] == 2
    assert e["components_coarse"] == 0
    for r in e["assembly_re_results"]:
        assert r["lod"] == "full"
        assert r["result_grade"] == "measured"
        assert r["match"] is not None


def test_lod_disabled_with_none_runs_full():
    body = _two_distinct_boxes()
    res = AssemblyReverseEngineer().apply(
        body, {"top_n": 10, "max_workers": 1, "lod_max_faces": None}
    )
    e = res.extras
    assert e["components_coarse"] == 0
    assert e["components_full"] == 2


# ── opt-in cache ─────────────────────────────────────────────────────────────


def test_cache_default_off_no_disk(tmp_path):
    """Default cache=False ⇒ cache_stats.enabled is False and no files."""
    body = _two_distinct_boxes()
    e = AssemblyReverseEngineer().apply(
        body, {"top_n": 10, "max_workers": 1, "lod_max_faces": 2}
    ).extras
    assert e["cache_stats"]["enabled"] is False
    assert e["cache_stats"]["hits"] == 0
    assert e["cache_stats"]["writes"] == 0


def test_cache_hit_skips_redetection(tmp_path):
    """First run populates the cache (writes>0, re_runs>0); an identical second
    run with the same cache_dir hits the cache (hits>0) and does NOT re-run RE
    (re_runs==0) — proving re-detection was skipped. Uses the coarse path so
    each run is fast."""
    cache_dir = str(tmp_path / "re_cache")

    body1 = _two_distinct_boxes()
    first = AssemblyReverseEngineer().apply(
        body1,
        {"top_n": 10, "max_workers": 1, "lod_max_faces": 2,
         "cache": True, "cache_dir": cache_dir},
    ).extras
    assert first["cache_stats"]["enabled"] is True
    assert first["cache_stats"]["writes"] >= 1
    assert first["cache_stats"]["hits"] == 0
    assert first["re_runs"] >= 1  # actually reconstructed (coarse) this time

    body2 = _two_distinct_boxes()
    second = AssemblyReverseEngineer().apply(
        body2,
        {"top_n": 10, "max_workers": 1, "lod_max_faces": 2,
         "cache": True, "cache_dir": cache_dir},
    ).extras
    # Every signature class was served from cache ⇒ no re-detection.
    assert second["cache_stats"]["hits"] >= 1
    assert second["re_runs"] == 0
    # The cached results still cover every component identically.
    assert second["components_processed"] == first["components_processed"]
    for r in second["assembly_re_results"]:
        assert r["lod"] == "coarse"
        assert r["result_grade"] == "estimate"


def test_recache_unit_roundtrip(tmp_path):
    """ReCache stores + retrieves a JSON-able result, marks cache_hit, and a
    disabled cache is a no-op."""
    cache = ReCache(enabled=True, cache_dir=str(tmp_path))
    sig = ("v", (1.0, 2.0, 3.0), 6, 12, 0.01)
    assert cache.get(sig) is None  # miss
    cache.put(sig, {"match": 0.9, "outcome": "PASS", "lod": "full"})
    hit = cache.get(sig)
    assert hit is not None
    assert hit["match"] == 0.9
    assert hit["cache_hit"] is True

    off = ReCache(enabled=False)
    off.put(sig, {"match": 1.0})
    assert off.get(sig) is None
    assert off.stats()["enabled"] is False
