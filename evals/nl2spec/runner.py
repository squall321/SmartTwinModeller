"""nl2spec REPLAY-mode eval runner (track 2-5 skeleton).

Task cards live in ``evals/nl2spec/tasks/*.yaml``::

    task_id: t03_bent_pipe
    nl_request_en: "Bend a round 6 mm tube along ..."
    nl_request_kr: "둥근 관을 구부려 ..."
    reference_spec: [{op: <skill>, args: {...}}, ...]
    invariants:
      is_solid: true
      volume_mm3: [min, max]
      feature_counts: {sketch_sweep: 1}     # optional: op -> PASSING step count

REPLAY mode (this file, the only shipped lane): the REFERENCE spec is executed
via generate_from_spec and scored against the invariants. It is fully
deterministic (no LLM, no network) and CI-gateable — the corpus-regress
philosophy applied to nl2spec. The ``nl_request_*`` fields are carried but NOT
interpreted here.

HONEST SCOPE — the LIVE-LLM lane (an anthropic client actually turning
nl_request into a spec, n>=3 trials, private hold-out split) is explicitly OUT
OF SCOPE per the roadmap (Phase 3, nightly/manual only; REJECT #9 forbids
making live LLM runs a CI blocker). Replay scores test that the REFERENCE
specs still build what the cards promise — recipe/task rot protection, and the
substrate the live lane will diff against.

CLI (corpus-regress conventions — baseline compare, exit 0/1/2):

    python evals/nl2spec/runner.py --update-baseline      # (re)write baseline
    python evals/nl2spec/runner.py                        # compare vs baseline
    python evals/nl2spec/runner.py --baseline out.json    # write to a path
    python evals/nl2spec/runner.py --json-out sweep.json  # dump records too

Records carry NO timing fields, so two runs of the same code produce
byte-identical ``records`` — determinism is pinned by test_nl2spec_replay.py.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
TASKS_DIR = HERE / "tasks"
DEFAULT_BASELINE = HERE / "baseline.json"

os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")

try:  # editable install is the normal path; src fallback for bare checkouts
    import phone_designer  # noqa: F401
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(REPO_ROOT / "src"))


# ──────────────────────────────────────────────────────────────────────────────
# task loading


def load_tasks(tasks_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """Load + schema-check every task card, sorted by task_id (deterministic).
    A malformed committed card raises ValueError naming the file."""
    import yaml

    directory = Path(tasks_dir) if tasks_dir is not None else TASKS_DIR
    if not directory.is_dir():
        raise FileNotFoundError(f"tasks directory not found: {directory}")
    tasks: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError(f"task {path}: top level must be a mapping")
        for key in ("task_id", "nl_request_en", "nl_request_kr",
                    "reference_spec", "invariants"):
            if key not in doc:
                raise ValueError(f"task {path}: missing required key '{key}'")
        if doc["task_id"] != path.stem:
            raise ValueError(f"task {path}: task_id '{doc['task_id']}' != "
                             f"file stem '{path.stem}'")
        inv = doc["invariants"]
        if not isinstance(inv, dict) or "is_solid" not in inv \
                or "volume_mm3" not in inv:
            raise ValueError(f"task {path}: invariants need at least "
                             f"is_solid + volume_mm3 [min, max]")
        tasks.append(doc)
    tasks.sort(key=lambda t: t["task_id"])
    return tasks


# ──────────────────────────────────────────────────────────────────────────────
# replay + scoring


def _finite(x: Any) -> Any:
    """strict-JSON-safe: non-finite floats become None, never inf/nan."""
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def run_task(task: dict[str, Any]) -> dict[str, Any]:
    """Execute one task's REFERENCE spec via generate_from_spec and score the
    invariants. Deterministic; no timing fields in the record."""
    from phone_designer.skills.create.generate_from_spec import GenerateFromSpec

    record: dict[str, Any] = {
        "task_id": task["task_id"],
        "lane": "replay",
        "ok": False,
        "passed": False,
        "is_solid": None,
        "volume_mm3": None,
        "n_steps": None,
        "n_ok": None,
        "checks": {},
        "failures": [],
        "error": None,
    }
    try:
        gen = GenerateFromSpec().apply(
            None, {"spec": task["reference_spec"],
                   "plan_name": f"nl2spec_{task['task_id']}"},
        ).extras["generated"]
    except Exception as exc:  # noqa: BLE001 — recorded verbatim, never masked
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["failures"] = ["execution_raised"]
        return record

    record["ok"] = bool(gen["ok"])
    record["is_solid"] = bool(gen["is_solid"])
    record["volume_mm3"] = _finite(gen["volume_mm3"])
    record["n_steps"] = gen["n_steps"]
    record["n_ok"] = gen["n_ok"]

    inv = task["invariants"]
    checks: dict[str, str] = {}
    failures: list[str] = []

    if not gen["ok"]:
        failures.append(
            "generate_not_ok: "
            + "; ".join(f"{s['op']}:{s['status']}:{s.get('error')}"
                        for s in gen["steps"] if s["status"] != "pass")
            + " | ".join(gen.get("spec_errors") or []))

    want_solid = bool(inv["is_solid"])
    checks["is_solid"] = "pass" if record["is_solid"] == want_solid else "fail"
    if checks["is_solid"] == "fail":
        failures.append(f"is_solid {record['is_solid']} != {want_solid}")

    lo, hi = inv["volume_mm3"]
    vol = record["volume_mm3"]
    vol_ok = isinstance(vol, (int, float)) and lo <= vol <= hi
    checks["volume_mm3"] = "pass" if vol_ok else "fail"
    if not vol_ok:
        failures.append(f"volume {vol} outside [{lo}, {hi}]")

    counts = inv.get("feature_counts")
    if counts:
        got = {}
        for s in gen["steps"]:
            if s["status"] == "pass":
                got[s["op"]] = got.get(s["op"], 0) + 1
        bad = {op: (got.get(op, 0), n) for op, n in counts.items()
               if got.get(op, 0) != n}
        checks["feature_counts"] = "pass" if not bad else "fail"
        if bad:
            failures.append(f"feature_counts mismatch (got, want): {bad}")

    record["checks"] = checks
    record["failures"] = failures
    record["passed"] = bool(gen["ok"] and not failures)
    return record


def run_replay(tasks_dir: Path | str | None = None,
               log=None) -> list[dict[str, Any]]:
    records = []
    for task in load_tasks(tasks_dir):
        rec = run_task(task)
        records.append(rec)
        if log:
            log(f"  {'PASS' if rec['passed'] else 'FAIL'} "
                f"{rec['task_id']:32s} vol={rec['volume_mm3']}"
                + (f"  {rec['failures']}" if rec["failures"] else ""))
    return records


# ──────────────────────────────────────────────────────────────────────────────
# baseline (corpus-regress conventions: committed JSON, compare, exit codes)


def save_baseline(records: list[dict[str, Any]],
                  path: Path | str = DEFAULT_BASELINE) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "lane": "nl2spec_replay",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_tasks": len(records),
        "n_passed": sum(1 for r in records if r["passed"]),
        "records": {r["task_id"]: r for r in records},
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def load_baseline(path: Path | str = DEFAULT_BASELINE) -> dict[str, Any] | None:
    path = Path(path)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def compare_to_baseline(records: list[dict[str, Any]],
                        baseline: dict[str, Any]) -> dict[str, Any]:
    """Regression = a baselined task that flips passed True→False, gains a NEW
    error, or disappears from the current run. New tasks are informational."""
    base_records: dict[str, dict] = baseline.get("records") or {}
    current = {r["task_id"]: r for r in records}
    regressions: list[dict[str, Any]] = []
    new_tasks: list[str] = []
    missing_tasks: list[str] = []

    for tid, base in base_records.items():
        rec = current.get(tid)
        if rec is None:
            missing_tasks.append(tid)
            regressions.append(
                {"task_id": tid, "reason": "task missing from current run"})
            continue
        if rec.get("error") and not base.get("error"):
            regressions.append(
                {"task_id": tid, "reason": f"new error: {rec['error']}"})
            continue
        if base.get("passed") and not rec.get("passed"):
            regressions.append(
                {"task_id": tid,
                 "reason": f"passed True -> False: {rec.get('failures')}"})
    for tid in current:
        if tid not in base_records:
            new_tasks.append(tid)

    return {
        "regressions": regressions,
        "new_tasks": sorted(new_tasks),
        "missing_tasks": sorted(missing_tasks),
        "n_tasks": len(records),
        "n_passed": sum(1 for r in records if r["passed"]),
    }


# ──────────────────────────────────────────────────────────────────────────────
# CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="nl2spec REPLAY eval — execute reference specs, score "
                    "invariants, compare against the committed baseline "
                    "(exit 0 ok / 1 regression / 2 usage).")
    ap.add_argument("--tasks-dir", default=None,
                    help=f"task card directory (default {TASKS_DIR})")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE),
                    help="baseline JSON path (compare target / write target)")
    ap.add_argument("--update-baseline", action="store_true",
                    help="(re)write the baseline instead of comparing")
    ap.add_argument("--json-out", default=None,
                    help="optional path for the full sweep+comparison JSON")
    args = ap.parse_args(argv)

    baseline: dict[str, Any] | None = None
    if not args.update_baseline:
        baseline = load_baseline(args.baseline)
        if baseline is None:  # usage error BEFORE the expensive sweep
            print(f"[error] no baseline at {args.baseline} — run with "
                  f"--update-baseline first", file=sys.stderr)
            return 2

    records = run_replay(args.tasks_dir, log=print)
    n_passed = sum(1 for r in records if r["passed"])
    print(f">>> replay: {n_passed}/{len(records)} task(s) passed")

    payload: dict[str, Any] = {"lane": "nl2spec_replay", "records": records}
    rc = 0
    if args.update_baseline:
        path = save_baseline(records, args.baseline)
        print(f">>> baseline written: {path}")
    else:
        assert baseline is not None  # checked before the sweep
        cmp = compare_to_baseline(records, baseline)
        payload["comparison"] = cmp
        for r in cmp["regressions"]:
            print(f"REGRESSION {r['task_id']}: {r['reason']}", file=sys.stderr)
        for tid in cmp["new_tasks"]:
            print(f"new task   {tid} (informational)")
        if cmp["regressions"]:
            rc = 1
        else:
            print(">>> no regressions vs baseline")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
            + "\n", encoding="utf-8")
        print(f">>> json: {args.json_out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
