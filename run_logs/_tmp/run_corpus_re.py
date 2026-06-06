"""Run the RE pipeline on every STEP file under corpus/oem/.

Per-file pipeline:
  1. ImportStep (or direct STEPControl_Reader fallback) → body.
  2. inspect_geometry  → vol / face_count / bbox / body_kind.
  3. extract_feature_catalog → pockets / holes / bosses / ribs / symmetries.
  4. plan_from_feature_catalog → step count.
  5. PlanExecutor.run → PASS / FAIL + error_count.
  6. inspect_geometry on regen body → vol / face_count.
  7. drift = |orig_bbox_vol - regen_bbox_vol| / orig_bbox_vol.

Each file is wrapped in try/except so one broken file does not poison the rest.
Per-file 5 min hard cap via threading + a watchdog flag.
Results land in run_logs/_tmp/corpus_re_results.json.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
import multiprocessing as mp

# Add src/ to path
HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CORPUS = REPO / "corpus" / "oem"
OUTPUT = REPO / "run_logs" / "_tmp" / "corpus_re_results.json"

PER_FILE_TIMEOUT_S = 300  # 5 min


def _bbox_vol(report: dict) -> float:
    bb = report.get("bbox") or {}
    size = bb.get("size") or [0.0, 0.0, 0.0]
    if not size or len(size) < 3:
        return 0.0
    return float(size[0]) * float(size[1]) * float(size[2])


def _process_one(step_path: str, result_q) -> None:
    """Worker (subprocess) — process a single STEP and push the record dict."""
    try:
        from phone_designer.skills.create.import_step import ImportStep
        from phone_designer.skills.inspect.inspect_geometry import InspectGeometry
        from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
            ExtractFeatureCatalog,
        )
        from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
            PlanFromFeatureCatalog,
        )
        from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
            FeatureFidelityDiff,
        )
        from phone_designer.plan.executor import PlanExecutor
        from phone_designer.plan.yaml_io import load_plan

        record: dict = {
            "filename": Path(step_path).name,
            "orig": None,
            "catalog": None,
            "plan_steps": None,
            "executor": None,
            "executor_errors": None,
            "regen": None,
            "bbox_vol_diff_pct": None,
            "feature_match_ratio": None,
            "avg_dim_drift_pct": None,
            "per_kind_diff": None,
            "error": None,
            "timings_s": {},
        }

        # Stage 1: Import STEP
        t0 = time.perf_counter()
        try:
            imp_res = ImportStep().apply(None, {"path": step_path})
            body = imp_res.body
        except Exception as exc:
            # Direct OCP fallback
            from OCP.STEPControl import STEPControl_Reader
            from OCP.IFSelect import IFSelect_RetDone
            from build123d import Part
            reader = STEPControl_Reader()
            status = reader.ReadFile(step_path)
            if status != IFSelect_RetDone:
                raise RuntimeError(f"STEP parse failed: status={status}") from exc
            reader.TransferRoots()
            body = Part(reader.OneShape())
        record["timings_s"]["import"] = round(time.perf_counter() - t0, 3)

        # Stage 2: inspect_geometry on orig
        t0 = time.perf_counter()
        insp_res = InspectGeometry().apply(body, {"include_faces": False, "include_edges": False})
        orig_report = insp_res.extras.get("inspection_report") or {}
        orig_bbox_vol = _bbox_vol(orig_report)
        record["orig"] = {
            "vol": orig_report.get("volume_mm3"),
            "face_count": orig_report.get("face_count"),
            "bbox_size": (orig_report.get("bbox") or {}).get("size"),
            "body_kind": orig_report.get("body_kind"),
            "bbox_vol": round(orig_bbox_vol, 4),
        }
        record["timings_s"]["inspect_orig"] = round(time.perf_counter() - t0, 3)

        # Stage 3: extract_feature_catalog
        t0 = time.perf_counter()
        cat_res = ExtractFeatureCatalog().apply(body, {})
        cat = cat_res.extras.get("feature_catalog") or {}
        catalog_time = round(time.perf_counter() - t0, 3)
        record["timings_s"]["catalog"] = catalog_time
        record["catalog"] = {
            "pockets": len(cat.get("pockets") or []),
            "holes": len(cat.get("holes") or []),
            "bosses": len(cat.get("bosses") or []),
            "ribs": len(cat.get("ribs") or []),
            "symmetries": len(cat.get("symmetries") or []),
            "catalog_time_s": catalog_time,
            "skipped": bool(cat.get("skipped")),
            "skipped_reason": cat.get("reason") if cat.get("skipped") else None,
        }

        # Stage 4: plan_from_feature_catalog
        t0 = time.perf_counter()
        plan_res = PlanFromFeatureCatalog().apply(body, {"catalog": cat})
        gen_plan = plan_res.extras.get("generated_plan") or {}
        plan_steps = gen_plan.get("steps") or []
        record["plan_steps"] = len(plan_steps)
        record["timings_s"]["plan"] = round(time.perf_counter() - t0, 3)

        # Stage 5: PlanExecutor.run
        # Plan is written to plans/reconstructed_plan.yaml; load it via yaml_io.
        t0 = time.perf_counter()
        plan_path = REPO / "plans" / "reconstructed_plan.yaml"
        try:
            plan = load_plan(plan_path)
            exec_result = PlanExecutor(plan).run()
            record["executor"] = exec_result.outcome  # PASS | FAIL
            record["executor_errors"] = exec_result.error_count
            regen_body = exec_result.final_body
        except Exception as exc:
            record["executor"] = "FAIL"
            record["executor_errors"] = 1
            record["executor_exception"] = f"{type(exc).__name__}: {exc}"
            regen_body = None
        record["timings_s"]["executor"] = round(time.perf_counter() - t0, 3)

        # Stage 6: inspect_geometry on regen
        if regen_body is not None:
            t0 = time.perf_counter()
            try:
                regen_res = InspectGeometry().apply(
                    regen_body, {"include_faces": False, "include_edges": False}
                )
                regen_rep = regen_res.extras.get("inspection_report") or {}
                regen_bbox_vol = _bbox_vol(regen_rep)
                record["regen"] = {
                    "vol": regen_rep.get("volume_mm3"),
                    "face_count": regen_rep.get("face_count"),
                    "bbox_size": (regen_rep.get("bbox") or {}).get("size"),
                    "bbox_vol": round(regen_bbox_vol, 4),
                }
                if orig_bbox_vol > 0.0:
                    record["bbox_vol_diff_pct"] = round(
                        abs(orig_bbox_vol - regen_bbox_vol) / orig_bbox_vol * 100.0,
                        3,
                    )
            except Exception as exc:
                record["regen_error"] = f"{type(exc).__name__}: {exc}"
            record["timings_s"]["inspect_regen"] = round(time.perf_counter() - t0, 3)

            # Stage 7: feature_fidelity_diff (orig catalog vs regen catalog)
            t0 = time.perf_counter()
            try:
                regen_cat_res = ExtractFeatureCatalog().apply(regen_body, {})
                regen_cat = regen_cat_res.extras.get("feature_catalog") or {}
                fid_res = FeatureFidelityDiff().apply(
                    regen_body,
                    {"catalog_a": cat, "catalog_b": regen_cat},
                )
                fid = fid_res.extras.get("feature_fidelity") or {}
                record["feature_match_ratio"] = fid.get("overall_match_ratio")
                record["avg_dim_drift_pct"] = fid.get("avg_dim_drift_pct")
                by_kind = fid.get("by_kind") or {}
                # Summarize per-kind as "holes:3->2 pockets:5->5", omitting all-zero kinds.
                parts: list[str] = []
                for kind, v in by_kind.items():
                    a = v.get("a", 0)
                    b = v.get("b", 0)
                    if a == 0 and b == 0:
                        continue
                    parts.append(f"{kind}:{a}->{b}")
                record["per_kind_diff"] = " ".join(parts) if parts else ""
            except Exception as exc:
                record["fidelity_error"] = f"{type(exc).__name__}: {exc}"
            record["timings_s"]["fidelity"] = round(time.perf_counter() - t0, 3)

        result_q.put(record)
    except Exception as exc:
        tb = traceback.format_exc(limit=8)
        try:
            result_q.put({
                "filename": Path(step_path).name,
                "orig": None,
                "catalog": None,
                "plan_steps": None,
                "executor": "FAIL",
                "executor_errors": 1,
                "regen": None,
                "bbox_vol_diff_pct": None,
                "error": f"{type(exc).__name__}: {exc}\n{tb}",
            })
        except Exception:
            pass


def run_with_timeout(step_path: str, timeout_s: int) -> dict:
    """Run _process_one in a subprocess with a hard timeout."""
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_process_one, args=(step_path, q))
    p.start()
    p.join(timeout_s)
    if p.is_alive():
        p.terminate()
        p.join(5)
        if p.is_alive():
            p.kill()
            p.join()
        return {
            "filename": Path(step_path).name,
            "orig": None,
            "catalog": None,
            "plan_steps": None,
            "executor": "FAIL",
            "executor_errors": 1,
            "regen": None,
            "bbox_vol_diff_pct": None,
            "error": f"TIMEOUT after {timeout_s}s",
        }
    # Drain the queue
    try:
        return q.get_nowait()
    except Exception:
        return {
            "filename": Path(step_path).name,
            "orig": None,
            "catalog": None,
            "plan_steps": None,
            "executor": "FAIL",
            "executor_errors": 1,
            "regen": None,
            "bbox_vol_diff_pct": None,
            "error": "no result returned from worker (likely crashed)",
        }


def main() -> int:
    # Recursive discovery — include industrial/ and any other subdirs.
    # Sort by size ascending so smaller (faster) files run first; cap at 100.
    patterns = ("**/*.step", "**/*.stp", "**/*.STEP", "**/*.STP")
    seen: set = set()
    candidates: list = []
    for pat in patterns:
        for p in CORPUS.glob(pat):
            if not p.is_file():
                continue
            key = str(p.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                sz = p.stat().st_size
            except OSError:
                sz = 0
            candidates.append((sz, p))
    candidates.sort(key=lambda t: (t[0], t[1].name))
    MAX_FILES = 100
    step_files = [p for _sz, p in candidates[:MAX_FILES]]
    print(
        f"[corpus_re] discovered {len(candidates)} STEP file(s) under {CORPUS}; "
        f"running first {len(step_files)} (capped at {MAX_FILES}, smallest first)",
        flush=True,
    )

    records: list[dict] = []
    files_pass = 0
    files_fail = 0
    for i, sp in enumerate(step_files, 1):
        print(f"[corpus_re] [{i}/{len(step_files)}] {sp.name} ...", flush=True)
        t_start = time.perf_counter()
        rec = run_with_timeout(str(sp), PER_FILE_TIMEOUT_S)
        elapsed = time.perf_counter() - t_start
        rec["total_time_s"] = round(elapsed, 2)
        records.append(rec)
        ok = rec.get("executor") == "PASS" and not rec.get("error")
        if ok:
            files_pass += 1
            tag = "PASS"
        else:
            files_fail += 1
            tag = "FAIL"
        print(
            f"[corpus_re]   -> {tag} exec={rec.get('executor')} "
            f"plan_steps={rec.get('plan_steps')} "
            f"drift={rec.get('bbox_vol_diff_pct')}% "
            f"err={rec.get('error') and rec['error'][:80]} "
            f"({elapsed:.1f}s)",
            flush=True,
        )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "files_processed": len(records),
                "files_pass": files_pass,
                "files_fail": files_fail,
                "results": records,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        f"[corpus_re] done. processed={len(records)} pass={files_pass} "
        f"fail={files_fail}  -> {OUTPUT}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
