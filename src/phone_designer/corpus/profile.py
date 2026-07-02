"""Per-stage duration profiling for the RE analyze round-trip (plan 2-4b).

Runs the SAME canonical per-file pipeline as ``corpus/regress.py``
(ImportStep → ExtractFeatureCatalog → PlanFromFeatureCatalog → PlanExecutor →
re-ExtractFeatureCatalog → FeatureFidelityDiff) IN-PROCESS and reports where
the wall-clock goes:

  * per-stage duration_ms — import / extract / plan / executor / re-extract /
    diff, measured here with ``time.perf_counter`` around each stage call;
  * per-detector breakdown of the extract stages — re-aggregated from the
    catalog's already-recorded ``_timings_sec`` (extract_feature_catalog
    records these per detector);
  * per-skill executor breakdown — re-aggregated from the already-recorded
    per-step ``step.metrics['duration_ms']`` (SkillBase.apply records it,
    PlanExecutor persists it on each PASS step).

HONEST LIMITS (stated in the output too):
  * durations are SINGLE-RUN wall-clock on this machine — no median-of-N, no
    calibration normalisation. Use for hotspot triage, not as a perf gate
    (the Phase-3 perf-budget gate adds median-of-3 + calibration).
  * the executor stage only breaks down PASS steps (failed/skipped steps
    never produced metrics); the stage total still includes their cost.

CLI:
    python -m phone_designer.corpus.profile <step files...>
        [--mode preserve_brep|box] [--top-steps N] [--json OUT.json]

Strict-JSON-safe: every emitted number is a finite float/int or None.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

#: canonical stage order — the analyze RE round-trip.
STAGES = ("import", "extract", "plan", "executor", "re-extract", "diff")


def _finite(x: Any) -> float | None:
    """Strict-JSON guard: finite float or None (never inf/nan)."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _ms(seconds: float) -> float:
    return round(seconds * 1000.0, 1)


# ──────────────────────────────────────────────────────────────────────────────
# Per-file profiling


def profile_file(step_path: str | Path, mode: str = "preserve_brep",
                 top_steps: int = 8) -> dict[str, Any]:
    """Profile ONE file's RE round-trip. Never raises — failures land in
    ``record['error']`` with the stages that DID run still reported.

    Returns::

        {
          "file": str, "mode": str,
          "stages_ms": {stage: float|None},   # None = stage never ran
          "total_ms": float,
          "detector_ms": {detector: float},   # from catalog _timings_sec
          "executor_skill_ms": {skill: float},# summed step duration_ms
          "top_steps": [{"id","skill","duration_ms"}, ...],
          "plan_steps": int|None,
          "match_ratio": float|None,
          "skipped": str|None,                # catalog too_big reason
          "error": str|None,
        }
    """
    from phone_designer.corpus.regress import _force_register_all

    record: dict[str, Any] = {
        "file": Path(step_path).name,
        "mode": mode,
        "stages_ms": {s: None for s in STAGES},
        "total_ms": None,
        "detector_ms": {},
        "executor_skill_ms": {},
        "top_steps": [],
        "plan_steps": None,
        "match_ratio": None,
        "skipped": None,
        "error": None,
        # one-time skill-registry import cost (≈0 once warm). Reported
        # separately so per-stage numbers stay honest: total_ms ==
        # setup_ms + sum(stages_ms) + small glue overhead.
        "setup_ms": None,
    }
    stages_ms: dict[str, Any] = record["stages_ms"]
    t_total = time.perf_counter()

    def _stage(name: str, fn: Callable[[], Any]) -> Any:
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            stages_ms[name] = _ms(time.perf_counter() - t0)

    plan_tmp: str | None = None
    try:
        t_setup = time.perf_counter()
        _force_register_all()
        record["setup_ms"] = _ms(time.perf_counter() - t_setup)

        from phone_designer.plan.executor import PlanExecutor
        from phone_designer.plan.yaml_io import load_plan
        from phone_designer.skills.create.import_step import ImportStep
        from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
            ExtractFeatureCatalog,
        )
        from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
            FeatureFidelityDiff,
        )
        from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
            PlanFromFeatureCatalog,
        )

        # ── import ──────────────────────────────────────────────────────────
        body = _stage("import", lambda: ImportStep().apply(
            None, {"path": str(step_path)}).body)
        if body is None:
            record["error"] = "import_step produced no body"
            return record

        # ── extract (feature catalog) ───────────────────────────────────────
        cat = _stage("extract", lambda: ExtractFeatureCatalog().apply(
            body, {}).extras["feature_catalog"])
        _merge_detector_ms(record["detector_ms"], cat)
        if cat.get("skipped"):
            record["skipped"] = str(cat.get("reason"))
            return record

        # ── plan (write to a TEMP path — never clobber the repo's shared
        #    plans/reconstructed_plan.yaml from a profiling run) ─────────────
        fd_path = tempfile.mkstemp(prefix="corpus_profile_plan_",
                                   suffix=".yaml")
        import os
        os.close(fd_path[0])
        plan_tmp = fd_path[1]
        plan_args: dict[str, Any] = {
            "catalog": cat, "base_step_kind": mode, "plan_out_path": plan_tmp,
        }
        if mode == "box":
            # mirror corpus/regress.py: measure the shipped capability.
            plan_args["base_profile_mode"] = "auto"

        def _do_plan():
            PlanFromFeatureCatalog().apply(body, plan_args)
            return load_plan(Path(plan_tmp))

        plan = _stage("plan", _do_plan)
        record["plan_steps"] = len(plan.steps)

        # ── executor ────────────────────────────────────────────────────────
        initial = body if mode == "preserve_brep" else None
        result = _stage("executor",
                        lambda: PlanExecutor(plan).run(initial_body=initial))
        _merge_executor_ms(record, plan, top_steps)
        regen = result.final_body
        if regen is None:
            record["error"] = f"executor produced no body ({result.outcome})"
            return record

        # ── re-extract (catalog on the regen body) ──────────────────────────
        regen_cat = _stage("re-extract", lambda: ExtractFeatureCatalog().apply(
            regen, {}).extras["feature_catalog"])
        _merge_detector_ms(record["detector_ms"], regen_cat)

        # box-mode frame shift — same fix corpus/regress.py applies before diff.
        if mode == "box":
            bb = cat.get("initial_bbox_mm")
            if isinstance(bb, (list, tuple)) and len(bb) >= 6:
                cx = (float(bb[0]) + float(bb[3])) / 2.0
                cy = (float(bb[1]) + float(bb[4])) / 2.0
                regen_cat["frame_translation_mm"] = [-cx, -cy, -float(bb[2])]

        # ── diff ────────────────────────────────────────────────────────────
        fid = _stage("diff", lambda: FeatureFidelityDiff().apply(
            regen, {"catalog_a": cat, "catalog_b": regen_cat},
        ).extras["feature_fidelity"])
        record["match_ratio"] = _finite(fid.get("overall_match_ratio"))
    except Exception as exc:  # noqa: BLE001 — profiling must report, not die
        record["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        record["total_ms"] = _ms(time.perf_counter() - t_total)
        if plan_tmp is not None:
            try:
                Path(plan_tmp).unlink()
            except OSError:
                pass
    return record


def _merge_detector_ms(acc: dict[str, float], catalog: dict) -> None:
    """Fold a catalog's per-detector ``_timings_sec`` (already recorded by
    extract_feature_catalog) into ``acc`` as milliseconds."""
    timings = catalog.get("_timings_sec") if isinstance(catalog, dict) else None
    if not isinstance(timings, dict):
        return
    for name, sec in timings.items():
        v = _finite(sec)
        if v is None:
            continue
        acc[name] = round(acc.get(name, 0.0) + v * 1000.0, 1)


def _merge_executor_ms(record: dict[str, Any], plan: Any,
                       top_steps: int) -> None:
    """Aggregate the executor's already-recorded per-step
    ``step.metrics['duration_ms']`` into per-skill sums + a top-N step list."""
    per_skill: dict[str, float] = record["executor_skill_ms"]
    steps: list[dict[str, Any]] = []
    for step in getattr(plan, "steps", []):
        metrics = getattr(step, "metrics", None) or {}
        dur = _finite(metrics.get("duration_ms"))
        if dur is None:
            continue  # never ran (SKIPPED / failed before _apply returned)
        skill = getattr(step, "skill", "?")
        per_skill[skill] = round(per_skill.get(skill, 0.0) + dur, 1)
        steps.append({
            "id": getattr(step, "id", "?"),
            "skill": skill,
            "duration_ms": round(dur, 1),
        })
    steps.sort(key=lambda s: (-s["duration_ms"], s["id"]))
    record["top_steps"] = steps[:max(0, int(top_steps))]


# ──────────────────────────────────────────────────────────────────────────────
# Aggregation + rendering


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-file per-stage aggregation: total / mean / share-% per stage."""
    totals: dict[str, float] = {s: 0.0 for s in STAGES}
    counts: dict[str, int] = {s: 0 for s in STAGES}
    for rec in records:
        for s in STAGES:
            v = _finite((rec.get("stages_ms") or {}).get(s))
            if v is None:
                continue
            totals[s] += v
            counts[s] += 1
    grand = sum(totals.values())
    out: dict[str, Any] = {"stages": {}, "grand_total_ms": round(grand, 1),
                           "n_files": len(records)}
    for s in STAGES:
        n = counts[s]
        out["stages"][s] = {
            "total_ms": round(totals[s], 1),
            "mean_ms": round(totals[s] / n, 1) if n else None,
            "share_pct": round(totals[s] / grand * 100.0, 1) if grand > 0 else None,
            "n_files": n,
        }
    return out


def _fmt(v: Any, width: int = 10) -> str:
    return f"{'-' if v is None else v:>{width}}"


def render_table(records: list[dict[str, Any]], agg: dict[str, Any],
                 top_steps: int, write: Callable[[str], None] = print) -> None:
    header = f"{'file':<44}" + "".join(f"{s:>12}" for s in STAGES) + f"{'total':>12}"
    write(header)
    write("-" * len(header))
    for rec in records:
        sm = rec.get("stages_ms") or {}
        row = f"{rec['file'][:43]:<44}"
        row += "".join(_fmt(sm.get(s), 12) for s in STAGES)
        row += _fmt(rec.get("total_ms"), 12)
        write(row)
        note = rec.get("error") or rec.get("skipped")
        if note:
            kind = "error" if rec.get("error") else "catalog skipped"
            write(f"    !! {kind}: {note}")
        setup = _finite(rec.get("setup_ms"))
        if setup is not None and setup > 500.0:
            write(f"    (total includes one-time skill-registry import: "
                  f"{setup} ms)")
    write("-" * len(header))
    stages = agg["stages"]
    write(f"{'TOTAL (ms)':<44}"
          + "".join(_fmt(stages[s]["total_ms"], 12) for s in STAGES)
          + _fmt(agg["grand_total_ms"], 12))
    write(f"{'share %':<44}"
          + "".join(_fmt(stages[s]["share_pct"], 12) for s in STAGES))

    # hotspot drill-downs (from the pipeline's own recorded durations)
    det: dict[str, float] = {}
    for rec in records:
        for k, v in (rec.get("detector_ms") or {}).items():
            det[k] = round(det.get(k, 0.0) + v, 1)
    if det:
        write("")
        write("extract/re-extract detector breakdown (from catalog "
              "_timings_sec, ms):")
        for k, v in sorted(det.items(), key=lambda kv: -kv[1]):
            write(f"  {k:<32}{v:>12}")
    sk: dict[str, float] = {}
    for rec in records:
        for k, v in (rec.get("executor_skill_ms") or {}).items():
            sk[k] = round(sk.get(k, 0.0) + v, 1)
    if sk:
        write("")
        write("executor per-skill breakdown (from step.metrics duration_ms, "
              "ms, PASS steps only):")
        for k, v in sorted(sk.items(), key=lambda kv: -kv[1])[:12]:
            write(f"  {k:<32}{v:>12}")
    if top_steps > 0:
        all_steps: list[tuple[str, dict[str, Any]]] = []
        for rec in records:
            for st in rec.get("top_steps") or []:
                all_steps.append((rec["file"], st))
        all_steps.sort(key=lambda t: (-t[1]["duration_ms"], t[0], t[1]["id"]))
        if all_steps:
            write("")
            write(f"top {top_steps} executor steps across all files (ms):")
            for f, st in all_steps[:top_steps]:
                write(f"  {st['duration_ms']:>10}  {st['skill']:<28} "
                      f"{st['id']}  ({f})")
    write("")
    write("caveat: single-run wall-clock on this machine — hotspot triage "
          "only, NOT a perf gate (no median-of-N / calibration).")


# ──────────────────────────────────────────────────────────────────────────────
# CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m phone_designer.corpus.profile",
        description="Per-stage duration profiling of the RE analyze "
                    "round-trip (import/extract/plan/executor/re-extract/"
                    "diff) on one or more STEP files.",
    )
    ap.add_argument("files", nargs="+", help="STEP file path(s)")
    ap.add_argument("--mode", choices=("preserve_brep", "box"),
                    default="preserve_brep")
    ap.add_argument("--top-steps", type=int, default=8,
                    help="how many slowest executor steps to list (0=off)")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="also write the per-file records + aggregate to "
                         "this JSON path")
    args = ap.parse_args(argv)

    records: list[dict[str, Any]] = []
    missing = [f for f in args.files if not Path(f).is_file()]
    if missing:
        print(f"error: file(s) not found: {missing}", file=sys.stderr)
        return 2
    # Warm the skill registry ONCE up front so the first file's totals are
    # not inflated by the one-time walk-import (still timed per-file as
    # setup_ms, ≈0 when warm).
    from phone_designer.corpus.regress import _force_register_all
    _force_register_all()
    for f in args.files:
        print(f"[corpus-profile] {f} (mode={args.mode}) ...", file=sys.stderr)
        records.append(profile_file(f, mode=args.mode,
                                    top_steps=args.top_steps))
    agg = aggregate(records)
    render_table(records, agg, args.top_steps)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"records": records, "aggregate": agg},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[corpus-profile] JSON written: {args.json_out}",
              file=sys.stderr)
    # exit 1 only when EVERY file errored (profiling partial data is useful)
    return 0 if any(not r.get("error") for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
