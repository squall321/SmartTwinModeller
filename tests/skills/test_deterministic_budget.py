"""Box-pipeline determinism under ``corpus-regress --workers N`` (budget-fix).

THE LOCATED MECHANISM (probe-verified 2026-07-03) — NOT a wall-clock budget.
A grep of every box-pipeline stage (regress._worker_pipeline, import_step,
extract_feature_catalog, plan_from_feature_catalog + its detectors and A/B
scoring, plan executor, feature_fidelity_diff, geometry_deviation) found NO
time-based degradation anywhere: the only wall-clock deadline in the tree is
register_bodies._icp_refine (_ICP_WALL_S), which the box lane never calls.

The bimodal match flap (1.0 <-> ~0.25, 9/55 files under ``--workers 4``) was
a cross-PROCESS scratch-file race: ``_write_body_as_step`` wrote every plan's
base-step STEP to the SHARED ``run_logs/_tmp/<plan_name>_base.step`` with
``plan_name`` always ``"reconstructed_plan"``, and the freeform-shell /
import_step base steps IMPORT that file at executor time — so a concurrent
sibling worker could overwrite it between write and read and the plan rebuilt
ANOTHER part's geometry. Measured pre-fix on kicad__Buzzer_15x7.5RM7.6.step:
a sibling writer flips match 1.0 -> 0.4667 (plan 1 step -> 4 steps, freeform
base reverted to box). CPU contention merely widens the write->read window,
which is why the flap correlated with ``--workers 4`` load.

THE FIX (plan_from_feature_catalog + regress._parallel_worker): when a caller
isolates its plan file via ``plan_out_path`` (every parallel corpus worker,
analyze_part, assembly_reverse_engineer, variants), the scratch STEP is now
the DERIVED sibling ``<plan_out_path stem>_base.step`` — never shared.
``plan_out_path=None`` (the serial default) keeps the historic shared path,
byte-identical serial behaviour.

Pins here:
  * THE mechanism pin — the Buzzer through the box worker pipeline twice,
    once clean and once under artificial CPU load PLUS a hostile sibling
    writer on the legacy shared path: match_ratio must be EQUAL (pre-fix the
    sibling writer flips it; probe-verified);
  * unit pins — the derived path is a pure function of ``plan_out_path``
    (identical across repeated calls regardless of sleep injection), distinct
    plan paths get distinct scratch files, and the serial default still uses
    the historic shared location (serial byte-identity).
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BUZZER = REPO / "corpus" / "oem" / "kicad__Buzzer_15x7.5RM7.6.step"
LEGACY_SHARED = REPO / "run_logs" / "_tmp" / "reconstructed_plan_base.step"
# Any small VALID part works as the hostile sibling's geometry — it only has
# to be a DIFFERENT body than the file under test.
SIBLING_PART = REPO / "corpus" / "oem" / "kicad__R_0402_1005Metric.step"


def _small_body():
    from phone_designer.skills.create.box import Box

    return Box().apply(None, {
        "length_mm": 20.0, "width_mm": 12.0, "height_mm": 4.0,
    }).body


# ──────────────────────────────────────────────────────────────────────────────
# Unit pins — scratch-path derivation is deterministic + isolated


def test_write_body_as_step_honors_out_path(tmp_path):
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        _write_body_as_step,
    )

    target = tmp_path / "nested" / "worker7_base.step"
    got = _write_body_as_step(_small_body(), "reconstructed_plan",
                              out_path=str(target))
    assert got == str(target)
    assert target.is_file() and target.stat().st_size > 0


def test_write_body_as_step_default_is_historic_shared_path():
    """Serial byte-identity: ``out_path=None`` must keep writing the historic
    shared location (run_logs/_tmp/<plan_name>_base.step)."""
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        _write_body_as_step,
    )

    got = _write_body_as_step(_small_body(), "reconstructed_plan")
    assert got is not None
    assert Path(got) == LEGACY_SHARED
    assert Path(got).is_file()


def _plan_via_skill(plan_out: Path) -> dict:
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        PlanFromFeatureCatalog,
    )

    res = PlanFromFeatureCatalog().apply(_small_body(), {
        "catalog": {},
        "base_step_kind": "import_step",  # the base kind that round-trips
        "plan_out_path": str(plan_out),   # through the scratch STEP file
    })
    return res.extras["generated_plan"]


def test_planner_derives_isolated_scratch_path_from_plan_out_path(tmp_path):
    plan_out = tmp_path / "corpus_regress_plan_1234_7.yaml"
    plan = _plan_via_skill(plan_out)
    base = plan["steps"][0]
    assert base["skill"] == "import_step"
    expected = tmp_path / "corpus_regress_plan_1234_7_base.step"
    assert base["args"]["path"] == str(expected)
    assert expected.is_file() and expected.stat().st_size > 0
    # the legacy shared path is NOT referenced anywhere in this plan
    assert str(LEGACY_SHARED) not in str(plan)


def test_derived_path_deterministic_regardless_of_sleep_injection(tmp_path):
    """Same inputs -> same derived path + same plan, even with wall-clock
    time injected between runs (there is nothing time-based to expire)."""
    plan_out = tmp_path / "p.yaml"
    a = _plan_via_skill(plan_out)
    time.sleep(1.2)  # sleep injection — must change NOTHING
    b = _plan_via_skill(plan_out)
    assert a == b
    assert a["steps"][0]["args"]["path"] == str(tmp_path / "p_base.step")


def test_distinct_plan_paths_get_distinct_scratch_files(tmp_path):
    """Two concurrent-style workers (distinct plan_out_path) must never share
    a scratch STEP — the exact isolation ``--workers N`` relies on."""
    p1 = _plan_via_skill(tmp_path / "w1.yaml")
    p2 = _plan_via_skill(tmp_path / "w2.yaml")
    path1 = p1["steps"][0]["args"]["path"]
    path2 = p2["steps"][0]["args"]["path"]
    assert path1 != path2
    assert Path(path1).is_file() and Path(path2).is_file()


def test_parallel_worker_cleans_derived_scratch_file(monkeypatch):
    """regress._parallel_worker unlinks the DERIVED <plan stem>_base.step
    together with the plan file (no temp-dir leak — one scratch STEP per
    pool task). Exercises the REAL _parallel_worker with run_one stubbed to
    create both files exactly where the worker points it."""
    import tempfile

    import phone_designer.corpus.regress as regress

    created: dict[str, Path] = {}

    def _fake_run_one(path, mode, timeout_s, plan_out_path):
        plan = Path(plan_out_path)
        derived = Path(str(plan.with_suffix("")) + "_base.step")
        plan.write_text("plan", encoding="utf-8")
        derived.write_text("step", encoding="utf-8")
        created["plan"] = plan
        created["derived"] = derived
        return {"file": Path(path).name, "mode": mode}

    monkeypatch.setattr(regress, "run_one", _fake_run_one)
    idx, rel, rec = regress._parallel_worker(
        (3, "rel/x.step", "x.step", "box", 5))
    assert (idx, rel) == (3, "rel/x.step")
    assert rec["file"] == "rel/x.step" or rec["file"] == "x.step"
    # the worker pointed run_one at its pid+idx plan path in tempdir
    assert created["plan"].parent == Path(tempfile.gettempdir())
    assert f"_{os.getpid()}_3" in created["plan"].name
    # and cleaned BOTH files up afterwards
    assert not created["plan"].exists(), "plan file leaked"
    assert not created["derived"].exists(), "derived scratch STEP leaked"


# ──────────────────────────────────────────────────────────────────────────────
# THE mechanism pin — worker pipeline immune to load + hostile sibling writer


@pytest.mark.slow
def test_box_worker_match_equal_under_load_and_sibling_writer(tmp_path):
    """kicad__Buzzer through the box worker pipeline twice — once clean, once
    under 3 CPU-burner SUBPROCESSES (real core contention, like ``--workers``
    siblings — burner THREADS would only GIL-starve this process, which is
    not the corpus phenomenon) PLUS a hostile writer clobbering the LEGACY
    shared scratch path (exactly what a concurrent worker used to do).
    match_ratio must be EQUAL. Pre-fix: the sibling writer flips the Buzzer
    from match 1.0 (1-step freeform-shell plan) to 0.4667 (4-step box
    fallback) — probe-verified 2026-07-03 in BOTH lanes (serial and
    plan_out_path)."""
    if not BUZZER.is_file() or not SIBLING_PART.is_file():
        pytest.skip("corpus/oem fixtures not present")

    import subprocess
    import sys

    from phone_designer.corpus.regress import _worker_pipeline

    def _run(tag: str) -> dict:
        plan_out = tmp_path / f"buzzer_{tag}.yaml"
        rec = _worker_pipeline(
            str(BUZZER), "box", plan_out_path=str(plan_out))
        assert rec.get("error") is None, f"{tag}: {rec.get('error')}"
        return rec

    clean = _run("clean")

    stop = threading.Event()
    sibling_bytes = SIBLING_PART.read_bytes()

    def _hostile_sibling():
        LEGACY_SHARED.parent.mkdir(parents=True, exist_ok=True)
        while not stop.is_set():
            try:
                LEGACY_SHARED.write_bytes(sibling_bytes)
            except OSError:
                pass
            time.sleep(0.05)

    burners = [
        subprocess.Popen(
            [sys.executable, "-c", "while True: pass"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(3)
    ]
    saboteur = threading.Thread(target=_hostile_sibling, daemon=True)
    saboteur.start()
    try:
        contended = _run("contended")
    finally:
        stop.set()
        saboteur.join(timeout=5)
        for p in burners:
            p.kill()

    assert clean["match_ratio"] == contended["match_ratio"], (
        f"match flapped: clean={clean['match_ratio']} "
        f"contended={contended['match_ratio']} — worker pipeline is not "
        f"contention-deterministic"
    )
    assert clean["plan_steps"] == contended["plan_steps"], (
        f"plan flapped: clean={clean['plan_steps']} steps, "
        f"contended={contended['plan_steps']} steps"
    )
