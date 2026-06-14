"""Single-command tracked corpus regression harness (plan item V2).

Replaces the ad-hoc inline sweeps used across passes 1-23
(``run_logs/_tmp/run_root_corpus_re.py`` / ``run_corpus_re.py``) with a
baseline-tracked runner:

  * Each file is processed in its OWN SUBPROCESS with a hard 300 s timeout —
    one hung OCCT call cannot kill the sweep (proven watchdog pattern
    ported from ``run_corpus_re.py``).
  * The sweep is SERIAL by design: ``PlanFromFeatureCatalog`` always
    writes ``plans/reconstructed_plan.yaml`` (no output-path arg), so the
    plan file is shared mutable state and parallel workers would race.
  * Per-file pipeline (the canonical RE round-trip):
      ImportStep → ExtractFeatureCatalog → PlanFromFeatureCatalog
      (base_step_kind=mode) → load plans/reconstructed_plan.yaml →
      PlanExecutor (initial_body=body for preserve_brep, none for box) →
      ExtractFeatureCatalog on regen → FeatureFidelityDiff.
  * BOX-MODE FRAME FIX — the regen body of a placeholder-box build lives
    in box-local coords. Before diffing we stash
    ``regen_cat['frame_translation_mm'] = [-cx, -cy, -zmin]`` computed
    from the ORIGINAL catalog's ``initial_bbox_mm`` (the world→box
    shift; feature_fidelity_diff negates it to map b back into world).
    tools/auto_re.py wired this from the wrong catalog — done correctly
    here.
  * Baselines live in ``corpus/baselines/<mode>_<subset>.json`` and are
    committed. Compare mode: any file whose match_ratio drops by more
    than ``MATCH_DROP_TOL`` vs baseline, or which gains a NEW error, is
    a regression (exit 1). New files (absent from the baseline) are
    reported as info only.

CLI entry: ``phone-designer corpus-regress`` (see cli.py).
Worker entry: ``python -m phone_designer.corpus.regress --worker
<step_path> <mode> <out_json>``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# ──────────────────────────────────────────────────────────────────────────────
# Constants

MODES = ("preserve_brep", "box")
SUBSETS = ("root", "complex", "industrial", "revolved", "all")

#: per-file subprocess hard timeout (seconds).
PER_FILE_TIMEOUT_S = 300

#: a baseline match_ratio may drop by at most this much before the file
#: is flagged as a regression.
MATCH_DROP_TOL = 0.005

_STEP_SUFFIXES = (".step", ".stp")


def _repo_root() -> Path:
    """Repository root — 3 levels up from this file
    (corpus → phone_designer → src → repo)."""
    return Path(__file__).resolve().parents[3]


def _corpus_dir() -> Path:
    return _repo_root() / "corpus" / "oem"


def baseline_path(mode: str, subset: str) -> Path:
    return _repo_root() / "corpus" / "baselines" / f"{mode}_{subset}.json"


# ──────────────────────────────────────────────────────────────────────────────
# File discovery


def _is_step(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in _STEP_SUFFIXES


def discover_files(subset: str) -> list[tuple[str, Path]]:
    """Return ``[(rel_key, abs_path), ...]`` for a subset, smallest file
    first (fast feedback — same ordering as the proven ad-hoc runners).

    ``rel_key`` is the path relative to corpus/oem with forward slashes;
    it is the stable per-file key used in records and baselines.
    """
    corpus = _corpus_dir()
    if subset == "root":
        files = [p for p in corpus.iterdir() if _is_step(p)]
    elif subset in ("complex", "industrial", "revolved"):
        sub = corpus / subset
        files = [p for p in sub.iterdir() if _is_step(p)] if sub.is_dir() else []
    elif subset == "all":
        seen: set[str] = set()
        files = []
        for s in ("root", "complex", "industrial", "revolved"):
            for _rel, p in discover_files(s):
                key = str(p.resolve()).lower()
                if key not in seen:
                    seen.add(key)
                    files.append(p)
    else:
        raise ValueError(f"unknown subset: {subset!r} (expected one of {SUBSETS})")

    files.sort(key=lambda p: (p.stat().st_size, p.name))
    return [(p.relative_to(corpus).as_posix(), p) for p in files]


# ──────────────────────────────────────────────────────────────────────────────
# Worker side — runs INSIDE the per-file subprocess


def _force_register_all() -> int:
    """Import every skill module so the executor's registry is complete
    (ported from run_root_corpus_re.py)."""
    import importlib
    import pkgutil

    import phone_designer.skills as skills_pkg

    n = 0
    for mod in pkgutil.walk_packages(skills_pkg.__path__, skills_pkg.__name__ + "."):
        try:
            importlib.import_module(mod.name)
            n += 1
        except Exception:
            pass
    return n


def _volume_mm3(body: Any) -> float | None:
    """Exact solid volume via BRepGProp (matches scenarios/runner.py)."""
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        shape = body.wrapped if hasattr(body, "wrapped") else body
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        return float(props.Mass())
    except Exception:
        return None


def _geometry_deviation_inprocess(
    regen_body: Any,
    orig_body: Any,
    frame_translation_mm: tuple[float, float, float] | None,
) -> dict[str, float]:
    """phase-0 (2026-06-13): hausdorff / rms / p95 (mm) between the regen
    body and the ORIGINAL imported body, using the in-process OCCT shapes
    (no STEP round-trip).

    ``frame_translation_mm`` (box mode) is the world->box shift; we apply it
    to the ORIGINAL (world-frame) body so both shapes live in the box frame
    before tessellation. For preserve_brep both share the frame -> pass None.

    Reuses geometry_deviation's own tessellation + bidirectional directed
    deviation so the metric is identical to the standalone skill.
    """
    import numpy as np

    from phone_designer.skills.inspect import geometry_deviation as _gd

    regen_shape = regen_body.wrapped if hasattr(regen_body, "wrapped") else regen_body
    orig_shape = orig_body.wrapped if hasattr(orig_body, "wrapped") else orig_body
    if frame_translation_mm is not None:
        dx, dy, dz = frame_translation_mm
        if dx or dy or dz:
            orig_shape = _gd._translate(orig_shape, dx, dy, dz)

    deflection = 0.2
    _gd._tessellate(regen_shape, deflection)
    _gd._tessellate(orig_shape, deflection)
    regen_verts, regen_tris = _gd._triangle_soup(regen_shape)
    orig_verts, orig_tris = _gd._triangle_soup(orig_shape)

    max_pts = 50000
    d_r2o, _ = _gd._directed_deviation(
        regen_verts, _gd._TriGrid(orig_tris), orig_verts, max_pts)
    d_o2r, _ = _gd._directed_deviation(
        orig_verts, _gd._TriGrid(regen_tris), regen_verts, max_pts)
    all_d = np.concatenate([d_r2o, d_o2r])
    return {
        "hausdorff_mm": round(float(all_d.max()), 6),
        "rms_mm": round(float(np.sqrt(np.mean(all_d * all_d))), 6),
        "p95_mm": round(float(np.percentile(all_d, 95.0)), 6),
    }


def _new_record(file: str, mode: str) -> dict[str, Any]:
    return {
        "file": file,
        "mode": mode,
        "match_ratio": None,
        "per_kind": None,
        "volume_delta_pct": None,
        # phase-0 (2026-06-13): geometric ground-truth deviation between the
        # regen body and the ORIGINAL imported body. Info-only / recorded —
        # NOT yet a regression trigger (a later phase makes hausdorff a gate).
        "hausdorff_mm": None,
        "rms_mm": None,
        "p95_mm": None,
        "error": None,
        "duration_s": None,
    }


def _worker_pipeline(step_path: str, mode: str) -> dict[str, Any]:
    """Full RE round-trip on one STEP file. Never raises — failures land
    in ``record['error']``."""
    t0 = time.perf_counter()
    record = _new_record(Path(step_path).name, mode)
    try:
        _force_register_all()

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

        body = ImportStep().apply(None, {"path": step_path}).body
        cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
        if cat.get("skipped"):
            # Not an error — the catalog declined the body (matches the
            # ad-hoc runner semantics). match_ratio stays None.
            record["skipped"] = cat.get("reason")
            return record

        PlanFromFeatureCatalog().apply(
            body, {"catalog": cat, "base_step_kind": mode}
        )
        plan = load_plan(_repo_root() / "plans" / "reconstructed_plan.yaml")
        record["plan_steps"] = len(plan.steps)

        initial = body if mode == "preserve_brep" else None
        result = PlanExecutor(plan).run(initial_body=initial)
        record["executor_outcome"] = result.outcome
        regen = result.final_body
        if regen is None:
            record["match_ratio"] = 0.0
            return record

        regen_cat = ExtractFeatureCatalog().apply(regen, {}).extras[
            "feature_catalog"
        ]
        # phase-0 (2026-06-13): world→box shift, reused below for the
        # geometry_deviation frame fix. None in preserve_brep (shared frame).
        frame_shift: tuple[float, float, float] | None = None
        if mode == "box":
            # Map the box-local regen catalog back into world coords —
            # the world→box shift comes from the ORIGINAL catalog's
            # pre-detector bbox (xmin, ymin, zmin, xmax, ymax, zmax).
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
        record["match_ratio"] = fid.get("overall_match_ratio")
        by_kind = fid.get("by_kind") or {}
        record["per_kind"] = {
            k: {
                "a": v.get("a", 0),
                "b": v.get("b", 0),
                "matched": v.get("matched", 0),
            }
            for k, v in by_kind.items()
            if (v.get("a") or v.get("b"))
        }

        vol_orig = _volume_mm3(body)
        vol_regen = _volume_mm3(regen)
        if vol_orig and vol_orig > 0.0 and vol_regen is not None:
            record["volume_delta_pct"] = round(
                (vol_regen - vol_orig) / vol_orig * 100.0, 3
            )

        # phase-0 (2026-06-13): geometric ground-truth deviation between the
        # regen body and the ORIGINAL imported body. Wrapped independently so
        # a single OCCT/tessellation hiccup records null deviation rather than
        # failing the whole sweep. Info-only — NOT a regression trigger yet.
        try:
            dev = _geometry_deviation_inprocess(regen, body, frame_shift)
            record["hausdorff_mm"] = dev["hausdorff_mm"]
            record["rms_mm"] = dev["rms_mm"]
            record["p95_mm"] = dev["p95_mm"]
        except Exception:
            record["hausdorff_mm"] = None
            record["rms_mm"] = None
            record["p95_mm"] = None
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        record["duration_s"] = round(time.perf_counter() - t0, 1)
    return record


def _worker_main(argv: list[str]) -> int:
    """``python -m phone_designer.corpus.regress --worker <step> <mode> <out>``."""
    step_path, mode, out_json = argv
    record = _worker_pipeline(step_path, mode)
    Path(out_json).write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Parent side — subprocess isolation + watchdog


def run_one(
    path: Path | str,
    mode: str,
    timeout_s: int = PER_FILE_TIMEOUT_S,
) -> dict[str, Any]:
    """Run the per-file pipeline in an isolated subprocess.

    ``subprocess.run(timeout=...)`` kills the child on expiry, so a hung
    OCCT call costs at most ``timeout_s`` seconds of the sweep.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode!r} (expected one of {MODES})")
    repo = _repo_root()
    src = repo / "src"
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{src}{os.pathsep}{existing}" if existing else str(src)
    )

    fd, out_json = tempfile.mkstemp(prefix="corpus_regress_", suffix=".json")
    os.close(fd)
    t0 = time.perf_counter()
    record: dict[str, Any] | None = None
    try:
        cmd = [
            sys.executable,
            "-m",
            "phone_designer.corpus.regress",
            "--worker",
            str(path),
            mode,
            out_json,
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(repo),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            record = _new_record(Path(path).name, mode)
            record["error"] = f"TIMEOUT after {timeout_s}s"
            record["duration_s"] = round(time.perf_counter() - t0, 1)
            return record

        try:
            record = json.loads(Path(out_json).read_text(encoding="utf-8"))
        except Exception:
            record = _new_record(Path(path).name, mode)
            tail = (proc.stderr or "").strip().splitlines()[-3:]
            record["error"] = (
                f"worker crashed (exit={proc.returncode}): "
                + " | ".join(tail)[:300]
            )
        record["duration_s"] = round(time.perf_counter() - t0, 1)
        return record
    finally:
        try:
            os.unlink(out_json)
        except OSError:
            pass


def run_sweep(
    mode: str,
    subset: str,
    timeout_s: int = PER_FILE_TIMEOUT_S,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Serial sweep over a subset. Returns the per-file records (the
    ``file`` key is the corpus-relative path)."""
    files = discover_files(subset)
    log(
        f"[corpus-regress] mode={mode} subset={subset}: {len(files)} file(s), "
        f"serial, {timeout_s}s/file cap"
    )
    records: list[dict[str, Any]] = []
    for i, (rel, p) in enumerate(files, 1):
        log(f"[{i:3d}/{len(files)}] {rel} ...")
        rec = run_one(p, mode, timeout_s=timeout_s)
        rec["file"] = rel
        records.append(rec)
        err = rec.get("error")
        log(
            f"          -> match={rec.get('match_ratio')} "
            f"vol_delta={rec.get('volume_delta_pct')}% "
            f"err={err[:80] if err else None} ({rec.get('duration_s')}s)"
        )
    return records


# ──────────────────────────────────────────────────────────────────────────────
# Baseline IO + comparison


def save_baseline(
    mode: str, subset: str, records: list[dict[str, Any]]
) -> Path:
    path = baseline_path(mode, subset)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "mode": mode,
        "subset": subset,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "records": {r["file"]: r for r in records},
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_baseline(mode: str, subset: str) -> dict[str, Any] | None:
    path = baseline_path(mode, subset)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def compare_to_baseline(
    records: list[dict[str, Any]],
    baseline: dict[str, Any],
    match_drop_tol: float = MATCH_DROP_TOL,
) -> dict[str, Any]:
    """Compare a fresh sweep against a baseline.

    Regression =
      * a file present in the baseline gains a NEW error, or
      * its match_ratio drops by more than ``match_drop_tol`` (a numeric
        baseline match degrading to None also counts as a drop).
    New files (not in baseline) and files missing from the corpus are
    informational only.

    phase-0 (2026-06-13): hausdorff_mm / rms_mm / p95_mm are RECORDED on
    each per-file record but are deliberately NOT read here — they are
    info-only and not yet a regression trigger (a later phase promotes
    hausdorff to a gate). This keeps old baselines that lack those columns
    from spuriously flagging.
    """
    base_records: dict[str, dict] = baseline.get("records") or {}
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    new_files: list[str] = []

    for rec in records:
        f = rec["file"]
        base = base_records.get(f)
        if base is None:
            new_files.append(f)
            continue
        cur_err = rec.get("error")
        base_err = base.get("error")
        if cur_err and not base_err:
            regressions.append(
                {"file": f, "reason": f"new error: {cur_err}"}
            )
            continue
        bm = base.get("match_ratio")
        cm = rec.get("match_ratio")
        if isinstance(bm, (int, float)):
            if not isinstance(cm, (int, float)):
                regressions.append(
                    {
                        "file": f,
                        "reason": f"match_ratio {bm} -> None",
                        "baseline_match": bm,
                        "current_match": None,
                    }
                )
            elif bm - cm > match_drop_tol:
                regressions.append(
                    {
                        "file": f,
                        "reason": (
                            f"match_ratio dropped {bm} -> {cm} "
                            f"(-{round(bm - cm, 4)})"
                        ),
                        "baseline_match": bm,
                        "current_match": cm,
                    }
                )
            elif cm - bm > match_drop_tol:
                improvements.append(
                    {"file": f, "baseline_match": bm, "current_match": cm}
                )

    run_files = {r["file"] for r in records}
    missing_files = sorted(set(base_records) - run_files)
    return {
        "regressions": regressions,
        "improvements": improvements,
        "new_files": new_files,
        "missing_files": missing_files,
        "ok": not regressions,
    }


if __name__ == "__main__":  # worker entry
    if len(sys.argv) == 5 and sys.argv[1] == "--worker":
        sys.exit(_worker_main(sys.argv[2:]))
    print(
        "usage: python -m phone_designer.corpus.regress "
        "--worker <step_path> <mode> <out_json>",
        file=sys.stderr,
    )
    sys.exit(2)
