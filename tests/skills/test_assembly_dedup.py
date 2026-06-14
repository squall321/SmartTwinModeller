"""assembly_reverse_engineer — instance-dedup + worker-count-independence.

pillar-perf (2026-06-14): the macro now groups components that hash EQUAL
under ``_component_signature.geometric_signature`` into a signature CLASS and
runs the full RE pipeline ONCE per class, reusing the result for every
instance. These tests pin three contracts:

  1. DEDUP CORRECTNESS — a compound of 4 identical boxes + 1 different box
     splits into 2 signature classes; RE runs exactly twice (not 5x) yet the
     output catalog covers all 5 components with correct per-instance volume
     and an instance_count reflecting the whole assembly.
  2. AGGREGATION — the volume-weighted aggregate match covers every instance,
     not just representatives.
  3. WORKER-COUNT-INDEPENDENCE — serial (max_workers=1) and pooled
     (max_workers>1) runs produce byte-identical per-component results.
  4. ESCAPE HATCH — dedup_instances=False reverts to one RE run per
     component (historic behaviour): 5 components ⇒ 5 classes ⇒ 5 runs.

These build plain disjoint boxes — closed shells above the 100 mm³ floor —
so the loop completes without per-component errors and matches are 1.0.
"""
from __future__ import annotations

import pytest
from build123d import Box as B3dBox, Part, Pos
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound

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


def _four_identical_plus_one():
    """4 identical 20x20x10 boxes (4000 mm³) at distinct positions +
    1 different 30x15x8 box (3600 mm³). The four identical instances must
    collapse to ONE signature class; the odd box is its own class."""
    boxes = [Pos(60 * i, 0, 0) * B3dBox(20, 20, 10) for i in range(4)]
    diff = Pos(0, 80, 0) * B3dBox(30, 15, 8)
    return _compound(boxes + [diff])


def _strip(results):
    """Comparable projection of the per-component log (drops timing)."""
    return [
        (
            r["index"], r["volume_mm3"], r["match"], r["signature_class"],
            r["instance_count"], r["representative"], r["skipped"], r["error"],
        )
        for r in results
    ]


def test_dedup_runs_re_once_per_signature_class():
    body = _four_identical_plus_one()
    res = AssemblyReverseEngineer().apply(
        body, {"top_n": 10, "max_workers": 1}
    )
    e = res.extras

    # 5 instances, but only 2 distinct geometric signatures.
    assert e["components_total"] == 5
    assert e["components_processed"] == 5
    assert e["signature_classes"] == 2
    # The whole point: RE ran twice, not five times.
    assert e["re_runs"] == 2

    results = e["assembly_re_results"]
    assert len(results) == 5

    # Class 0 = the four identical boxes; exactly one representative.
    cls0 = [r for r in results if r["signature_class"] == 0]
    assert len(cls0) == 4
    assert sum(1 for r in cls0 if r["representative"]) == 1
    for r in cls0:
        assert r["instance_count"] == 4
        assert r["volume_mm3"] == pytest.approx(4000.0, rel=1e-3)
        assert r["error"] is None
        assert r["match"] is not None

    # Class 1 = the odd box (singleton).
    cls1 = [r for r in results if r["signature_class"] == 1]
    assert len(cls1) == 1
    assert cls1[0]["instance_count"] == 1
    assert cls1[0]["representative"] is True
    assert cls1[0]["volume_mm3"] == pytest.approx(3600.0, rel=1e-3)


def test_aggregate_covers_all_instances():
    body = _four_identical_plus_one()
    res = AssemblyReverseEngineer().apply(
        body, {"top_n": 10, "max_workers": 1}
    )
    # Plain boxes round-trip exactly ⇒ every instance matches 1.0 ⇒
    # volume-weighted aggregate is 1.0 (proves all five contribute weight).
    assert res.extras["aggregate_match_ratio"] == pytest.approx(1.0, abs=1e-3)


def test_worker_count_independence():
    body = _four_identical_plus_one()
    serial = AssemblyReverseEngineer().apply(
        body, {"top_n": 10, "max_workers": 1}
    ).extras
    pooled = AssemblyReverseEngineer().apply(
        body, {"top_n": 10, "max_workers": 4}
    ).extras

    # Per-component results must be identical regardless of worker count.
    assert _strip(serial["assembly_re_results"]) == _strip(
        pooled["assembly_re_results"]
    )
    assert serial["re_runs"] == pooled["re_runs"] == 2
    assert serial["signature_classes"] == pooled["signature_classes"] == 2
    assert serial["aggregate_match_ratio"] == pooled["aggregate_match_ratio"]


def test_dedup_disabled_runs_per_component():
    body = _four_identical_plus_one()
    res = AssemblyReverseEngineer().apply(
        body,
        {"top_n": 10, "max_workers": 1, "dedup_instances": False},
    )
    e = res.extras
    # Escape hatch: every component is its own class ⇒ RE runs 5 times.
    assert e["components_processed"] == 5
    assert e["signature_classes"] == 5
    assert e["re_runs"] == 5
    for r in e["assembly_re_results"]:
        assert r["instance_count"] == 1
        assert r["representative"] is True
        assert r["signature_class"] == r["index"]


def test_face_count_guard_records_known_gap(monkeypatch):
    """Time-box guard (pillar-perf point 3): a component whose face count
    exceeds DEFAULT_MAX_FACE_COUNT is recorded as a per-component KNOWN-GAP
    (skipped, with a reason + face_count) instead of hanging the assembly.
    We force the cap to 2 so an ordinary 6-face box trips it — no need for a
    real 4000-face monster. Serial path so the env override is in-process."""
    monkeypatch.setenv("PHONE_DESIGNER_MAX_FACE_COUNT", "2")
    # Reload the guard module so DEFAULT_MAX_FACE_COUNT re-reads the env.
    import importlib

    from phone_designer.skills import _face_count_guard
    importlib.reload(_face_count_guard)
    try:
        body = _compound([B3dBox(20, 20, 10), Pos(60, 0, 0) * B3dBox(30, 15, 8)])
        res = AssemblyReverseEngineer().apply(
            body, {"top_n": 10, "max_workers": 1}
        )
        e = res.extras
        # Both classes tripped the guard ⇒ no RE actually completed, but the
        # catalog still covers every component (partial-but-complete).
        assert e["components_processed"] == 2
        assert e["re_runs"] == 0
        for r in e["assembly_re_results"]:
            assert r["skipped"] is True
            assert r["error"] is not None
            assert "too_many_faces" in r["error"]
            assert r.get("face_count") == 6
    finally:
        # Restore the cap BEFORE reloading so the module re-reads the real
        # default (16000), not the still-set "2" — monkeypatch's own teardown
        # runs after this finally block, so we must clear the env ourselves.
        monkeypatch.delenv("PHONE_DESIGNER_MAX_FACE_COUNT", raising=False)
        importlib.reload(_face_count_guard)


def test_default_two_box_compound_unchanged_under_dedup():
    """The historic 2-box smoke test result must not change: two identical
    20x20x10 boxes now collapse to ONE class (RE runs once) but the output
    still reports two processed components, each with a plan + a valid match.
    This guards the small-compound DEFAULT path against the dedup change."""
    a = B3dBox(20, 20, 10)
    b = Pos(50, 0, 0) * B3dBox(20, 20, 10)
    body = _compound([a, b])
    res = AssemblyReverseEngineer().apply(body, {"top_n": 2})
    e = res.extras
    assert e["components_total"] == 2
    assert e["components_processed"] == 2
    # Identical boxes ⇒ a single class, RE runs once, both reuse the result.
    assert e["signature_classes"] == 1
    assert e["re_runs"] == 1
    for r in e["assembly_re_results"]:
        assert r["skipped"] is False
        assert r["error"] is None
        assert r["plan_steps"] is not None
        assert r["match"] is not None
        assert 0.0 <= r["match"] <= 1.0
    assert 0.0 <= e["aggregate_match_ratio"] <= 1.0
    assert res.body is body
