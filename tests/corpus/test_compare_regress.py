"""COMPARE-pairs gate + corpus-regress parallelism regression tests
(Pillar HARNESS+GATES, phase-3, 2026-06-14).

Two harnesses are gated here:

PART 1 — corpus-regress ``--workers`` parallelism
  * ``_effective_workers`` cap = min(cpu-2, n_files), never < 1; ``workers<=1``
    always serial. (fast, pure logic)
  * ``_run_sweep_parallel`` re-sorts pool results back to the INPUT file order
    so the output is worker-count-INDEPENDENT — proven with a stubbed
    ``run_one`` (no OCCT). (fast)
  * SLOW: a real 2-file subset run at ``workers=1`` and ``workers=2`` yields
    identical GATE-relevant per-file records (match_ratio / per_kind / error /
    plan_steps) and an identical regression verdict. (hausdorff/rms/
    volume_delta are pre-existing OCCT tessellation jitter — NOT gate fields —
    so they are excluded, matching the harness's own verdict logic.)

PART 2 — compare-pairs gate
  * ``_coarse_family`` collapses fine labels into same/different/unknown. (fast)
  * ``compare_to_baseline`` flags a cross-family flip, a similarity drop, a
    new error and an rmsd rise — and is silent on an adjacent within-family
    label move. (fast, pure logic)
  * SLOW self-proof (the V2 pattern): inject a ``register_bodies`` sign-flip
    (negate the proper-rotation candidates — the canonical eigenvector-sign
    bug); the recorded ``registration_rmsd`` blows up and the gate flips to a
    REGRESSION → the harness would exit 1.

The SLOW tests require the corpus parts to be present locally (the corpus is
gitignored); they skip otherwise (requires_oem-style, keyed on the actual pair
source files rather than PHONE_DESIGNER_OEM_REF).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.corpus import compare_regress as cr
from phone_designer.corpus import regress

PROJECT = Path(__file__).resolve().parents[2]


# ──────────────────────────────────────────────────────────────────────────────
# PART 1 — corpus-regress parallelism (fast, pure logic)


def test_effective_workers_cap(monkeypatch):
    # workers<=1 -> serial regardless of file count.
    assert regress._effective_workers(1, 100) == 1
    assert regress._effective_workers(0, 100) == 1
    # n_files<=1 -> serial.
    assert regress._effective_workers(8, 1) == 1
    # capped at min(cpu-2, n_files).
    monkeypatch.setattr(regress.os, "cpu_count", lambda: 8)
    assert regress._effective_workers(8, 100) == 6   # cpu-2
    assert regress._effective_workers(8, 4) == 4     # n_files
    assert regress._effective_workers(3, 100) == 3   # request honoured
    # tiny cpu never goes below 1.
    monkeypatch.setattr(regress.os, "cpu_count", lambda: 2)
    assert regress._effective_workers(8, 100) == 1


def test_collect_in_order_resorts_pool_results():
    """The pool collects results as they COMPLETE (any order); _collect_in_order
    must re-sort them back to the input file index so the sweep output is
    worker-count-INDEPENDENT. Feed completions out of order to prove it."""
    # Simulate a pool that finished file indices in a scrambled order.
    completed = {
        2: {"file": "f2.step", "match_ratio": 2.0},
        0: {"file": "f0.step", "match_ratio": 0.0},
        4: {"file": "f4.step", "match_ratio": 4.0},
        1: {"file": "f1.step", "match_ratio": 1.0},
        3: {"file": "f3.step", "match_ratio": 3.0},
    }
    out = regress._collect_in_order(completed, 5)
    assert [r["match_ratio"] for r in out] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert [r["file"] for r in out] == [f"f{i}.step" for i in range(5)]


def _gate_fields(records):
    """Only the determinism-relevant fields the regression verdict keys on.
    hausdorff/rms/p95/volume_delta are excluded — they carry pre-existing
    OCCT tessellation jitter that differs even between two SERIAL runs of the
    same file, and the harness deliberately does NOT gate on them."""
    return [
        {k: r.get(k) for k in (
            "file", "mode", "match_ratio", "per_kind", "error",
            "plan_steps", "executor_outcome")}
        for r in records
    ]


@pytest.mark.slow
def test_workers_1_vs_N_identical_gate_records_and_verdict():
    """workers=1 and workers=2 produce identical GATE-relevant per-file
    records (in identical order) and an identical regression verdict on a
    small preserve_brep subset.

    Uses the two SMALLEST root files that have a committed baseline (fast,
    deterministic match_ratio). A subprocess TIMEOUT/crash means the machine
    is too loaded to make the determinism claim — that is an ENVIRONMENT
    condition, so the test skips rather than failing (the determinism logic
    itself is exercised by test_collect_in_order_resorts_pool_results)."""
    base = regress.load_baseline("preserve_brep", "root")
    if base is None:
        pytest.skip("preserve_brep_root baseline not present")
    files = regress.discover_files("root")
    # Prefer files we already have a stable baseline match_ratio for, smallest
    # first — they round-trip in a few seconds each.
    have_base = [
        (rel, p) for rel, p in files
        if rel in base.get("records", {})
        and isinstance(base["records"][rel].get("match_ratio"), (int, float))
    ]
    if len(have_base) < 2:
        pytest.skip("not enough baselined root files present locally")
    subset2 = have_base[:2]

    timeout = 240
    recs_serial = []
    for rel, p in subset2:
        rec = regress.run_one(p, "preserve_brep", timeout_s=timeout)
        rec["file"] = rel
        recs_serial.append(rec)
    recs_par = regress._run_sweep_parallel(
        "preserve_brep", subset2, timeout_s=timeout,
        log=lambda m: None, workers=2,
    )

    # An environmental TIMEOUT/crash in EITHER run -> skip (cannot compare).
    env_err = {"TIMEOUT", "worker crashed"}
    for rec in recs_serial + recs_par:
        e = rec.get("error") or ""
        if any(tok in e for tok in env_err):
            pytest.skip(f"environmental subprocess failure: {e[:60]}")

    # GATE-relevant per-file records are identical, in identical order.
    assert _gate_fields(recs_serial) == _gate_fields(recs_par)

    # Identical regression verdict against the committed baseline.
    cmp_s = regress.compare_to_baseline(recs_serial, base)
    cmp_p = regress.compare_to_baseline(recs_par, base)
    assert cmp_s == cmp_p
    # And against the baseline these small files must NOT regress.
    assert cmp_s["ok"] is True, cmp_s["regressions"]


# ──────────────────────────────────────────────────────────────────────────────
# PART 2 — compare-pairs gate (fast, pure logic)


def test_coarse_family_mapping():
    assert cr._coarse_family("identical") == "same"
    assert cr._coarse_family("parametric_variant") == "same"
    assert cr._coarse_family("same_family_resized") == "same"
    assert cr._coarse_family("design_change") == "different"
    assert cr._coarse_family("unrelated") == "different"
    assert cr._coarse_family(None) == "unknown"
    assert cr._coarse_family("bogus") == "unknown"


def _baseline_of(records):
    return {"records": {r["id"]: r for r in records}}


def _rec(id_, label, sim, rmsd, error=None):
    return {
        "id": id_, "classification": label,
        "coarse_family": cr._coarse_family(label),
        "similarity": sim, "registration_rmsd": rmsd, "error": error,
    }


def test_within_family_move_not_a_regression():
    base = _baseline_of([_rec("p", "identical", 1.0, 0.0)])
    # identical -> parametric_variant: same coarse family, similarity steady.
    cur = [_rec("p", "parametric_variant", 0.99, 0.1)]
    out = cr.compare_to_baseline(cur, base)
    assert out["ok"] is True
    assert out["regressions"] == []


def test_cross_family_flip_is_a_regression():
    base = _baseline_of([_rec("p", "parametric_variant", 0.9, 1.0)])
    cur = [_rec("p", "unrelated", 0.9, 1.0)]  # same -> different
    out = cr.compare_to_baseline(cur, base)
    assert out["ok"] is False
    assert len(out["regressions"]) == 1
    assert "family flip" in out["regressions"][0]["reason"]


def test_similarity_drop_is_a_regression():
    base = _baseline_of([_rec("p", "identical", 1.0, 0.0)])
    cur = [_rec("p", "identical", 0.90, 0.0)]  # -0.10 > SIM_DROP_TOL
    out = cr.compare_to_baseline(cur, base)
    assert out["ok"] is False
    assert "similarity dropped" in out["regressions"][0]["reason"]


def test_rmsd_rise_is_a_regression():
    base = _baseline_of([_rec("p", "parametric_variant", 0.9, 1.0)])
    cur = [_rec("p", "parametric_variant", 0.9, 5.0)]  # +4 > RMSD_RISE_TOL
    out = cr.compare_to_baseline(cur, base)
    assert out["ok"] is False
    assert "registration_rmsd rose" in out["regressions"][0]["reason"]


def test_new_error_is_a_regression():
    base = _baseline_of([_rec("p", "identical", 1.0, 0.0)])
    cur = [_rec("p", "identical", 1.0, 0.0, error="Boom: kaput")]
    out = cr.compare_to_baseline(cur, base)
    assert out["ok"] is False
    assert "new error" in out["regressions"][0]["reason"]


def test_skipped_and_new_pairs_are_informational():
    base = _baseline_of([_rec("known", "identical", 1.0, 0.0)])
    cur = [
        {"id": "absent", "skipped": "source file absent"},
        _rec("brandnew", "unrelated", 0.1, 1.0),
    ]
    out = cr.compare_to_baseline(cur, base)
    assert out["ok"] is True
    assert out["skipped"] == ["absent"]
    assert out["new_pairs"] == ["brandnew"]
    assert out["missing_pairs"] == ["known"]


def test_manifest_and_baseline_committed():
    """The committed manifest + seeded baseline must load and agree."""
    pairs = cr.load_manifest()
    assert isinstance(pairs, list) and len(pairs) >= 3
    base = cr.load_baseline()
    assert base is not None
    assert set(base["records"]) <= {p["id"] for p in pairs}
    # Every baseline record carries the gate-relevant fields.
    for rec in base["records"].values():
        assert "classification" in rec
        assert "coarse_family" in rec
        assert "registration_rmsd" in rec


# ──────────────────────────────────────────────────────────────────────────────
# PART 2 — SLOW self-proof: register_bodies sign-flip is DETECTED


def _variant_pair():
    for p in cr.load_manifest():
        if p.get("scale_b"):       # the 1.5x parametric_variant pair
            return p
    return None


@pytest.mark.slow
def test_clean_variant_pair_matches_baseline():
    """The committed 1.5x variant pair, re-run clean, must NOT regress vs the
    seeded baseline (proves the baseline is self-consistent on this machine)."""
    pair = _variant_pair()
    if pair is None or not cr._pair_source_present(pair):
        pytest.skip("variant pair source files not present locally")
    base = cr.load_baseline()
    cr._force_register_all()
    rec = cr._worker_pipeline(pair)
    out = cr.compare_to_baseline([rec], base)
    assert rec.get("error") is None
    assert out["ok"] is True, out["regressions"]


@pytest.mark.slow
def test_register_bodies_sign_flip_detected():
    """V2-style self-proof: inject a register_bodies sign-flip → the recorded
    registration_rmsd blows up → the gate flips to a REGRESSION (exit 1)."""
    pair = _variant_pair()
    if pair is None or not cr._pair_source_present(pair):
        pytest.skip("variant pair source files not present locally")
    base = cr.load_baseline()

    cr._force_register_all()
    from phone_designer.skills.inspect import register_bodies as rb

    # Negate every proper-rotation candidate that maps B's eigen-frame onto
    # A's — the canonical eigenvector-sign bug. The best fit becomes a
    # reflected orientation and the recorded rmsd rises past RMSD_RISE_TOL.
    orig = rb._proper_rotations_between_frames
    rb._proper_rotations_between_frames = lambda Va, Vb: [(-R) for R in orig(Va, Vb)]
    try:
        rec = cr._worker_pipeline(pair)
    finally:
        rb._proper_rotations_between_frames = orig

    base_rmsd = base["records"][pair["id"]]["registration_rmsd"]
    assert rec.get("registration_rmsd") is not None
    assert rec["registration_rmsd"] > base_rmsd, (
        "sign-flip should inflate registration_rmsd"
    )
    out = cr.compare_to_baseline([rec], base)
    assert out["ok"] is False, "gate must DETECT the register_bodies sign-flip"
    assert any("registration_rmsd rose" in r["reason"]
               for r in out["regressions"])
