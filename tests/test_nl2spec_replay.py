"""nl2spec REPLAY harness (evals/nl2spec/runner.py) — track 2-5.

Pins:
  * the task-card corpus loads (>= 12 cards) and every REFERENCE spec replays
    to a 100% invariant pass — the roadmap's baseline pin;
  * the COMMITTED baseline JSON records that 100% pass;
  * replay records are DETERMINISTIC (two sweeps byte-identical — the
    corpus-regress precondition for gating on them);
  * baseline compare catches a pass→fail flip and a vanished task, and treats
    new tasks as informational;
  * the CLI exits 2 on a missing baseline BEFORE running the sweep, 0 on a
    green compare (corpus-regress exit-code conventions).

LIVE-LLM lane is explicitly out of scope here (Phase 3; roadmap REJECT #9
forbids gating CI on live LLM runs).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "evals" / "nl2spec" / "runner.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("nl2spec_runner", RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("nl2spec_runner", mod)
    spec.loader.exec_module(mod)
    return mod


runner = _load_runner()


@pytest.fixture(scope="module")
def records():
    """One full replay sweep, shared across this module's tests."""
    return runner.run_replay()


# ── corpus + the 100%-pass baseline pin ──────────────────────────────────────

def test_task_corpus_loads_at_least_12_cards():
    tasks = runner.load_tasks()
    assert len(tasks) >= 12
    ids = [t["task_id"] for t in tasks]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    for t in tasks:
        assert t["nl_request_en"] and t["nl_request_kr"]
        lo, hi = t["invariants"]["volume_mm3"]
        assert 0 < lo <= hi


def test_replay_passes_100_percent_on_reference_specs(records):
    failed = [r for r in records if not r["passed"]]
    assert not failed, f"replay failures: " \
                       f"{[(r['task_id'], r['failures']) for r in failed]}"
    assert len(records) >= 12


def test_committed_baseline_is_100_percent_pass():
    baseline = runner.load_baseline()
    assert baseline is not None, (
        f"committed baseline missing at {runner.DEFAULT_BASELINE} — run "
        f"runner.py --update-baseline")
    recs = baseline["records"]
    assert baseline["n_tasks"] == len(recs) >= 12
    assert baseline["n_passed"] == len(recs)
    assert all(r["passed"] for r in recs.values())


def test_current_sweep_matches_committed_baseline(records):
    baseline = runner.load_baseline()
    cmp = runner.compare_to_baseline(records, baseline)
    assert cmp["regressions"] == [], cmp["regressions"]
    assert cmp["n_passed"] == cmp["n_tasks"]


# ── determinism (precondition for gating) ────────────────────────────────────

def test_replay_records_are_deterministic(records):
    again = runner.run_replay()
    assert json.dumps(again, sort_keys=True, allow_nan=False) == \
        json.dumps(records, sort_keys=True, allow_nan=False)


def test_records_are_strict_json_safe(records):
    json.dumps(records, allow_nan=False)


# ── baseline write / compare mechanics (no geometry re-runs) ─────────────────

def test_baseline_roundtrip_and_pass_flip_detected(records, tmp_path):
    path = runner.save_baseline(records, tmp_path / "b.json")
    baseline = json.loads(path.read_text(encoding="utf-8"))
    assert baseline["schema"] == 1 and baseline["lane"] == "nl2spec_replay"

    # tamper one record into a failure — must be flagged as a regression
    tampered = copy.deepcopy(records)
    tampered[0]["passed"] = False
    tampered[0]["failures"] = ["volume 1.0 outside [2.0, 3.0]"]
    cmp = runner.compare_to_baseline(tampered, baseline)
    assert len(cmp["regressions"]) == 1
    assert cmp["regressions"][0]["task_id"] == records[0]["task_id"]
    assert "True -> False" in cmp["regressions"][0]["reason"]


def test_missing_task_is_a_regression_and_new_task_is_informational(
        records, tmp_path):
    path = runner.save_baseline(records, tmp_path / "b.json")
    baseline = json.loads(path.read_text(encoding="utf-8"))

    dropped = records[1:]  # first task vanished from the current run
    cmp = runner.compare_to_baseline(dropped, baseline)
    assert [r["task_id"] for r in cmp["regressions"]] == \
        [records[0]["task_id"]]

    extra = copy.deepcopy(records)
    extra.append({**copy.deepcopy(records[0]), "task_id": "t99_new"})
    cmp2 = runner.compare_to_baseline(extra, baseline)
    assert cmp2["regressions"] == []
    assert cmp2["new_tasks"] == ["t99_new"]


# ── CLI conventions ──────────────────────────────────────────────────────────

def test_cli_exits_2_when_baseline_missing(tmp_path, capsys):
    # must fail fast — BEFORE any expensive geometry sweep
    rc = runner.main(["--baseline", str(tmp_path / "nope.json")])
    assert rc == 2
    assert "no baseline" in capsys.readouterr().err


def test_cli_update_then_compare_on_single_task(tmp_path, capsys):
    # exercise the full CLI loop cheaply: a 1-task corpus in tmp
    src = sorted((REPO_ROOT / "evals" / "nl2spec" / "tasks").glob("*.yaml"))[0]
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / src.name).write_text(src.read_text(encoding="utf-8"),
                                  encoding="utf-8")
    base = tmp_path / "baseline.json"
    out = tmp_path / "sweep.json"

    rc = runner.main(["--tasks-dir", str(tasks), "--baseline", str(base),
                      "--update-baseline"])
    assert rc == 0 and base.is_file()

    rc = runner.main(["--tasks-dir", str(tasks), "--baseline", str(base),
                      "--json-out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["comparison"]["regressions"] == []
    assert "no regressions" in capsys.readouterr().out
