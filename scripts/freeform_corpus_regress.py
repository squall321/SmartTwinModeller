"""Standalone FREEFORM corpus-regress runner (PILLAR FREEFORM, phase-3,
2026-06-14).

A dedicated lane for the freeform reconstruction fixtures. It is SEPARATE from
the prismatic ``box_complex`` lane on purpose: the freeform path is engaged via
``PlanFromFeatureCatalog(base_profile_mode="auto")`` — the existing
``corpus.regress.run_one`` pipeline does NOT pass that arg (it would change the
committed box_complex baselines), so this runner drives its own in-process
pipeline while REUSING ``corpus.regress``'s proven subprocess-isolation,
registry priming, volume, and geometry-deviation helpers. cli.py is untouched.

Per-fixture record (mirrors corpus.regress columns + a freeform proof column):
  * ``match_ratio``        — FeatureFidelityDiff overall_match_ratio (box frame)
  * ``hausdorff_mm``       — geometry_deviation hausdorff vs the ORIGINAL body,
                             reconstructed WITH base_profile_mode="auto"
  * ``volume_delta_pct``   — (regen − orig)/orig × 100
  * ``box_hausdorff_mm``   — the SAME reconstruction with base_profile_mode="off"
                             (plain bbox box base). The auto-vs-box hausdorff
                             delta is the anti-fake proof that the silhouette
                             base is materially better, not a relabelled box.
  * ``mechanism``          — silhouette_extrude / loft_section / revolve / sweep
  * ``analytic_volume_mm3``— the builder's closed-form volume (ground truth)

Each fixture runs in its OWN subprocess (300 s cap) so one hung OCCT call can't
kill the sweep — the watchdog pattern is inherited from corpus.regress.run_one.

Usage (from repo root):
    venv/Scripts/python.exe scripts/freeform_corpus_regress.py --update   # write baseline
    venv/Scripts/python.exe scripts/freeform_corpus_regress.py            # compare to baseline
    venv/Scripts/python.exe scripts/freeform_corpus_regress.py --worker NAME OUT_JSON  # internal
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent


def _ensure_import_path() -> None:
    src = PROJECT / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    fixtures = PROJECT / "fixtures"
    if fixtures.exists() and str(fixtures) not in sys.path:
        sys.path.insert(0, str(fixtures))


_ensure_import_path()

#: per-fixture subprocess hard timeout (seconds) — same as corpus.regress.
PER_FIXTURE_TIMEOUT_S = 300

#: a baseline match_ratio may drop by at most this much before regression.
MATCH_DROP_TOL = 0.005

#: hausdorff may grow by at most this RELATIVE fraction before regression
#: (freeform geometry deviation is the headline metric for this lane).
HAUSDORFF_GROW_REL_TOL = 0.02

BASELINE_PATH = PROJECT / "corpus" / "baselines" / "box_freeform.json"
FIXTURE_DIR = PROJECT / "fixtures" / "freeform"


# ──────────────────────────────────────────────────────────────────────────────
# Worker side — runs INSIDE the per-fixture subprocess.


def _reconstruct(step_path: str, body, cat, base_profile_mode: str):
    """Box-mode reconstruction of ``body`` with the given base_profile_mode.

    Returns ``(match_ratio, hausdorff_mm, volume_delta_pct, regen_body)`` or
    raises. Mirrors corpus.regress._worker_pipeline's box-mode branch but
    threads ``base_profile_mode`` through PlanFromFeatureCatalog and writes the
    plan to a PER-CALL temp path (plan_out_path) so the box-vs-auto A/B never
    races on plans/reconstructed_plan.yaml.
    """
    from phone_designer.corpus.regress import (
        _geometry_deviation_inprocess,
        _volume_mm3,
    )
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.plan.yaml_io import load_plan
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
        FeatureFidelityDiff,
    )
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        PlanFromFeatureCatalog,
    )

    fd, plan_path = tempfile.mkstemp(prefix="freeform_plan_", suffix=".yaml")
    os.close(fd)
    try:
        PlanFromFeatureCatalog().apply(
            body,
            {
                "catalog": cat,
                "base_step_kind": "box",
                "base_profile_mode": base_profile_mode,
                "plan_out_path": plan_path,
            },
        )
        plan = load_plan(plan_path)
    finally:
        try:
            os.unlink(plan_path)
        except OSError:
            pass

    result = PlanExecutor(plan).run(initial_body=None)
    regen = result.final_body
    if regen is None:
        return (0.0, None, None, None, len(plan.steps))

    regen_cat = ExtractFeatureCatalog().apply(regen, {}).extras["feature_catalog"]

    # box-frame shift from the ORIGINAL catalog bbox (world→box), exactly as
    # corpus.regress does for box mode.
    frame_shift = None
    bb = cat.get("initial_bbox_mm")
    if isinstance(bb, (list, tuple)) and len(bb) >= 6:
        cx = (float(bb[0]) + float(bb[3])) / 2.0
        cy = (float(bb[1]) + float(bb[4])) / 2.0
        zmin = float(bb[2])
        frame_shift = (-cx, -cy, -zmin)
        regen_cat["frame_translation_mm"] = [-cx, -cy, -zmin]

    fid = FeatureFidelityDiff().apply(
        regen, {"catalog_a": cat, "catalog_b": regen_cat}
    ).extras["feature_fidelity"]
    match = fid.get("overall_match_ratio")

    vol_orig = _volume_mm3(body)
    vol_regen = _volume_mm3(regen)
    vol_delta = None
    if vol_orig and vol_orig > 0.0 and vol_regen is not None:
        vol_delta = round((vol_regen - vol_orig) / vol_orig * 100.0, 3)

    haus = None
    try:
        dev = _geometry_deviation_inprocess(regen, body, frame_shift)
        haus = dev["hausdorff_mm"]
    except Exception:
        haus = None

    return (match, haus, vol_delta, regen, len(plan.steps))


def _forced_profile_hausdorff(body, cat) -> float | None:
    """Score a reconstruction whose ``s_base`` is FORCED to the recovered
    silhouette profile (``extrude_profile_world``) — bypassing the production
    A/B revert-guard.

    Returns the box-frame hausdorff_mm, or ``None`` when the body is not
    prismatic (no profile recoverable). This is the honest "profile-base"
    half of the anti-fake delta: it shows what the silhouette base achieves
    geometrically REGARDLESS of whether the conservative match-ratio guard
    keeps it in production.

    Reuses the planner's own ``_profile_base_step_args`` +
    ``_score_reconstruction`` so the metric is identical to the guard's.
    """
    import copy

    from phone_designer.skills.reverse_engineer import (
        plan_from_feature_catalog as P,
    )

    bb = cat.get("initial_bbox_mm")
    if not (isinstance(bb, (list, tuple)) and len(bb) >= 6):
        return None
    bbox = tuple(float(c) for c in bb[:6])
    prof_args = P._profile_base_step_args(body, bbox, 1.0)
    if prof_args is None:
        return None
    box_plan = P._build_plan(
        cat, body=body, base_step_kind="box", base_profile_mode="off"
    )
    alt_plan = copy.deepcopy(box_plan)
    swapped = False
    for st in alt_plan.get("steps") or []:
        if str(st.get("id", "")).startswith("s_base"):
            st["skill"] = "extrude_profile_world"
            st["args"] = prof_args
            swapped = True
            break
    if not swapped:
        return None
    score = P._score_reconstruction(alt_plan, body, bbox)
    if score is None:
        return None
    return round(float(score[1]), 6)


def _worker(name: str, out_json: str) -> int:
    """Reconstruct ONE freeform fixture and dump the record.

    Records THREE base variants honestly:
      * ``hausdorff_mm`` / ``match_ratio`` — the SHIPPING base_profile_mode=
        "auto" reconstruction (revert-guarded: keeps box unless a non-box base
        is both no-worse on match AND strictly better on hausdorff).
      * ``box_hausdorff_mm`` — forced plain bbox box base (mode="off").
      * ``profile_hausdorff_mm`` — forced silhouette-profile base (prismatic
        fixtures only; ``None`` otherwise). box_haus − profile_haus is the
        anti-fake proof that the recovered outline is geometrically tighter.
    """
    import freeform  # fixtures/freeform/__init__.py

    from phone_designer.corpus.regress import _force_register_all
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )

    t0 = time.perf_counter()
    record: dict[str, Any] = {
        "file": name,
        "mechanism": freeform.MECHANISM.get(name),
        "match_ratio": None,
        "hausdorff_mm": None,
        "box_hausdorff_mm": None,
        "profile_hausdorff_mm": None,
        "auto_base_is_profile": None,
        "volume_delta_pct": None,
        "analytic_volume_mm3": None,
        "plan_steps": None,
        "error": None,
        "duration_s": None,
    }
    try:
        _force_register_all()
        # ground-truth analytic volume from the builder (deterministic).
        _part, analytic_vol = freeform.BUILDERS[name]()
        record["analytic_volume_mm3"] = round(float(analytic_vol), 4)

        step_path = str(FIXTURE_DIR / f"{name}.step")
        body = ImportStep().apply(None, {"path": step_path}).body
        cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
        if cat.get("skipped"):
            record["error"] = f"catalog skipped: {cat.get('reason')}"
            return 0

        # 1. SHIPPING freeform path (base_profile_mode="auto").
        match, haus, vdelta, _regen, nsteps = _reconstruct(
            step_path, body, cat, "auto"
        )
        record["match_ratio"] = match
        record["hausdorff_mm"] = haus
        record["volume_delta_pct"] = vdelta
        record["plan_steps"] = nsteps

        # 2. plain box base (base_profile_mode="off").
        _bm, box_haus, _bv, _br, _bs = _reconstruct(
            step_path, body, cat, "off"
        )
        record["box_hausdorff_mm"] = box_haus

        # 3. forced silhouette-profile base (prismatic fixtures only) — the
        #    anti-fake proof. Whether the auto path actually KEPT the profile
        #    is recorded too (it reverts to box when match would drop).
        prof_haus = _forced_profile_hausdorff(body, cat)
        record["profile_hausdorff_mm"] = prof_haus
        if isinstance(haus, (int, float)) and isinstance(box_haus, (int, float)):
            record["auto_base_is_profile"] = bool(haus < box_haus - 1e-6)
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        record["duration_s"] = round(time.perf_counter() - t0, 1)
        Path(out_json).write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Parent side — subprocess isolation + watchdog.


def run_one(name: str, timeout_s: int = PER_FIXTURE_TIMEOUT_S) -> dict[str, Any]:
    env = os.environ.copy()
    src = PROJECT / "src"
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{src}{os.pathsep}{existing}" if existing else str(src)
    )
    fd, out_json = tempfile.mkstemp(prefix="freeform_regress_", suffix=".json")
    os.close(fd)
    t0 = time.perf_counter()
    try:
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            name,
            out_json,
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=str(PROJECT), env=env,
                capture_output=True, text=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return {
                "file": name, "error": f"TIMEOUT after {timeout_s}s",
                "duration_s": round(time.perf_counter() - t0, 1),
            }
        try:
            rec = json.loads(Path(out_json).read_text(encoding="utf-8"))
        except Exception:
            tail = (proc.stderr or "").strip().splitlines()[-3:]
            rec = {
                "file": name,
                "error": (
                    f"worker crashed (exit={proc.returncode}): "
                    + " | ".join(tail)[:300]
                ),
            }
        rec["duration_s"] = round(time.perf_counter() - t0, 1)
        return rec
    finally:
        try:
            os.unlink(out_json)
        except OSError:
            pass


def run_sweep() -> list[dict[str, Any]]:
    import freeform

    records: list[dict[str, Any]] = []
    names = list(freeform.BUILDERS.keys())
    print(f"[freeform-regress] {len(names)} fixture(s), serial, "
          f"{PER_FIXTURE_TIMEOUT_S}s/fixture cap")
    for i, name in enumerate(names, 1):
        print(f"[{i}/{len(names)}] {name} ...", flush=True)
        rec = run_one(name)
        records.append(rec)
        print(
            f"          -> match={rec.get('match_ratio')} "
            f"auto_haus={rec.get('hausdorff_mm')} "
            f"box_haus={rec.get('box_hausdorff_mm')} "
            f"profile_haus={rec.get('profile_hausdorff_mm')} "
            f"vol_delta={rec.get('volume_delta_pct')}% "
            f"err={rec.get('error')} ({rec.get('duration_s')}s)"
        )
    return records


def save_baseline(records: list[dict[str, Any]]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "mode": "box",
        "subset": "freeform",
        "base_profile_mode": "auto",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "records": {r["file"]: r for r in records},
    }
    BASELINE_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote baseline -> {BASELINE_PATH}")


def compare_to_baseline(records: list[dict[str, Any]]) -> int:
    if not BASELINE_PATH.is_file():
        print(f"no baseline at {BASELINE_PATH}; run with --update first")
        return 2
    base = json.loads(BASELINE_PATH.read_text(encoding="utf-8")).get("records") or {}
    regressions: list[str] = []
    for rec in records:
        f = rec["file"]
        b = base.get(f)
        if b is None:
            print(f"  [new] {f} (not in baseline)")
            continue
        if rec.get("error") and not b.get("error"):
            regressions.append(f"{f}: new error {rec.get('error')}")
            continue
        bm, cm = b.get("match_ratio"), rec.get("match_ratio")
        if isinstance(bm, (int, float)):
            if not isinstance(cm, (int, float)) or bm - cm > MATCH_DROP_TOL:
                regressions.append(f"{f}: match_ratio {bm} -> {cm}")
        bh, ch = b.get("hausdorff_mm"), rec.get("hausdorff_mm")
        if isinstance(bh, (int, float)) and bh > 0:
            if not isinstance(ch, (int, float)):
                regressions.append(f"{f}: hausdorff {bh} -> {ch}")
            elif (ch - bh) / bh > HAUSDORFF_GROW_REL_TOL:
                regressions.append(f"{f}: hausdorff grew {bh} -> {ch}")
    if regressions:
        print("\nREGRESSIONS:")
        for r in regressions:
            print(f"  - {r}")
        return 1
    print("\nOK — no regressions vs baseline")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", nargs=2, metavar=("NAME", "OUT_JSON"))
    ap.add_argument("--update", action="store_true",
                    help="write/overwrite the box_freeform baseline")
    args = ap.parse_args()

    if args.worker:
        return _worker(args.worker[0], args.worker[1])

    records = run_sweep()
    if args.update:
        save_baseline(records)
        return 0
    return compare_to_baseline(records)


if __name__ == "__main__":
    raise SystemExit(main())
