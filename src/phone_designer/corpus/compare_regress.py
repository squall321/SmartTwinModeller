"""Tracked COMPARE-pairs regression harness (Pillar HARNESS+GATES,
phase-3, 2026-06-14).

The sibling of :mod:`phone_designer.corpus.regress`. Where ``regress`` gates
the RE round-trip (import -> plan -> regen -> fidelity), this gates the
COMPARE stack: ``compare_parts(A, B)`` over a SMALL committed manifest of
part pairs, each with an EXPECTED family-class label.

Why a separate gate: a sign-flip or frame bug in ``register_bodies`` /
``_part_similarity`` can silently flip a pair's classification (e.g. a scaled
variant mislabelled ``unrelated``, or a part-vs-itself mislabelled
``design_change``) WITHOUT moving any RE round-trip number. The corpus-regress
sweep would stay green. This harness catches exactly that class of regression.

Design (mirrors regress.py — proven patterns reused, not reinvented):

  * Each pair runs in its OWN SUBPROCESS with a hard timeout (watchdog) —
    one hung OCCT registration cannot kill the gate. Clone of
    ``regress.run_one``.
  * Reproducible pairs WITHOUT committing big STEPs: a pair entry may carry
    ``scale_a`` / ``scale_b``. The worker loads the named corpus part, applies
    a uniform scale via OCCT ``BRepBuilderAPI_Transform`` + writes a temp STEP
    with ``STEPControl_Writer`` (``_scale_export``), then runs
    ``compare_parts`` on the resulting paths. So ``screw vs screw@1.5x`` is
    generated on the fly from the single committed ``occt__screw.step``.
  * A pair whose source corpus file is ABSENT is SKIPPED (requires_oem-style)
    — the gate degrades on machines without the full corpus rather than
    failing.
  * Per-pair record: ``{similarity, classification, scale_factor,
    registration_rmsd, hausdorff, ...}``.
  * Baseline lives in ``corpus/baselines/compare_pairs.json`` (committed).
  * REGRESSION (any of):
      - ``classification`` flips to a DIFFERENT FAMILY. The labels collapse
        into two coarse families for this test:
            "same"      = {identical, parametric_variant, same_family_resized}
            "different" = {design_change, unrelated}
        An ADJACENT move WITHIN the same coarse family (e.g. identical <->
        parametric_variant, or parametric_variant <-> same_family_resized) is
        NOT a regression — those are heuristic-threshold neighbours. A cross-
        family flip (same <-> different) IS a regression.
      - ``similarity_score`` drops by more than ``SIM_DROP_TOL`` (0.05).
      - ``registration_rmsd`` rises by more than ``RMSD_RISE_TOL``.

CLI entry: ``phone-designer compare-regress`` (see cli.py).
Worker entry: ``python -m phone_designer.corpus.compare_regress --worker
<pair_json> <out_json>``.
"""
from __future__ import annotations

import json
import math
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

#: per-pair subprocess hard timeout (seconds). compare_parts is heavier than a
#: single RE round-trip (two imports + registration + deviation) so a touch
#: more generous than regress.PER_FILE_TIMEOUT_S.
PER_PAIR_TIMEOUT_S = 600

#: a baseline similarity_score may drop by at most this before the pair is a
#: regression.
SIM_DROP_TOL = 0.05

#: registration_rmsd may rise by at most this (mm) before the pair is a
#: regression. Generous — rmsd is scale-dependent and only a gross blow-up
#: (a wrong orientation) should trip it.
RMSD_RISE_TOL = 0.5

#: classification labels grouped into two coarse FAMILIES. A flip ACROSS
#: families is a regression; a move within a family is not.
_SAME_FAMILY = frozenset(
    {"identical", "parametric_variant", "same_family_resized"}
)
_DIFFERENT_FAMILY = frozenset({"design_change", "unrelated"})


def _coarse_family(label: str | None) -> str:
    """Collapse a fine classification label into 'same' / 'different' /
    'unknown'."""
    if label in _SAME_FAMILY:
        return "same"
    if label in _DIFFERENT_FAMILY:
        return "different"
    return "unknown"


def _repo_root() -> Path:
    """Repository root — 3 levels up (corpus → phone_designer → src → repo)."""
    return Path(__file__).resolve().parents[3]


def _corpus_dir() -> Path:
    return _repo_root() / "corpus" / "oem"


def manifest_path() -> Path:
    return _repo_root() / "corpus" / "oem" / "compare_pairs.json"


def baseline_path() -> Path:
    return _repo_root() / "corpus" / "baselines" / "compare_pairs.json"


# ──────────────────────────────────────────────────────────────────────────────
# Manifest

#: The committed pair set. Built so it exercises every family boundary from a
#: SMALL set of committed corpus parts (no big STEPs committed — scaled
#: variants are generated on the fly via _scale_export):
#:
#:   * a part vs ITSELF                  -> identical          (coarse: same)
#:   * a part vs its 1.5x uniform scale  -> parametric_variant (coarse: same)
#:   * two unrelated parts               -> unrelated          (coarse: diff)
_DEFAULT_PAIRS: list[dict[str, Any]] = [
    {
        "id": "facerec_self_identical",
        "file_a": "pythonocc__face_recognition_sample_part.step",
        "file_b": "pythonocc__face_recognition_sample_part.step",
        "expected_label": "identical",
    },
    {
        "id": "facerec_1.5x_variant",
        "file_a": "pythonocc__face_recognition_sample_part.step",
        "file_b": "pythonocc__face_recognition_sample_part.step",
        "scale_b": 1.5,
        "expected_label": "parametric_variant",
        "expected_scale": 1.5,
    },
    {
        "id": "facerec_vs_splinecage_unrelated",
        "file_a": "pythonocc__face_recognition_sample_part.step",
        "file_b": "pythonocc__splinecage.step",
        "expected_label": "unrelated",
    },
]


def write_default_manifest() -> Path:
    """(Re)write corpus/oem/compare_pairs.json with the default pair set."""
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "comment": (
            "COMPARE-pairs gate manifest. Scaled variants are generated on "
            "the fly from the committed corpus parts (see _scale_export) so "
            "no large STEPs are committed. expected_label is informational "
            "(the seeded baseline records the actual label); the gate keys on "
            "coarse-family flips vs the baseline."
        ),
        "pairs": _DEFAULT_PAIRS,
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_manifest() -> list[dict[str, Any]]:
    """Load the committed pair manifest. Returns the list of pair dicts."""
    path = manifest_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"compare-pairs manifest not found at {path} — run "
            f"compare-regress --init-manifest first"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    pairs = data.get("pairs")
    if not isinstance(pairs, list):
        raise ValueError(f"malformed manifest {path}: 'pairs' must be a list")
    return pairs


def _pair_source_present(pair: dict[str, Any]) -> bool:
    """True iff BOTH source corpus files exist on disk (requires_oem-style
    skip)."""
    corpus = _corpus_dir()
    fa = corpus / str(pair.get("file_a", ""))
    fb = corpus / str(pair.get("file_b", ""))
    return fa.is_file() and fb.is_file()


# ──────────────────────────────────────────────────────────────────────────────
# Worker side — runs INSIDE the per-pair subprocess


def _force_register_all() -> int:
    """Import every skill module so the registry is complete (clone of
    regress._force_register_all)."""
    import importlib
    import pkgutil

    import phone_designer.skills as skills_pkg

    n = 0
    for mod in pkgutil.walk_packages(
        skills_pkg.__path__, skills_pkg.__name__ + "."
    ):
        try:
            importlib.import_module(mod.name)
            n += 1
        except Exception:
            pass
    return n


def _scale_export(
    src_step: str,
    scale: float,
    out_step: str,
    translate: tuple[float, float, float] | None = None,
) -> str:
    """Load ``src_step``, apply a UNIFORM ``scale`` about the origin (and an
    optional ``translate``) via OCCT ``BRepBuilderAPI_Transform``, and write
    ``out_step`` with ``STEPControl_Writer``.

    Reproducible scaled variant generation — the key to committing only the
    base corpus parts while still gating ``part vs 1.5x-part`` pairs. Mirrors
    assembly/_compound.load_step_shape + apply_transform_shape and
    io/step_export_v2._write_plain so the OCCT call sequence matches the rest
    of the codebase.

    ``translate`` (optional) shifts the scaled body into a DIFFERENT frame so
    the comparison must rely on ``register_bodies`` rather than the same-frame
    identity fallback — used by the self-proof to make registration
    load-bearing.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
    from OCP.STEPControl import (
        STEPControl_Reader,
        STEPControl_StepModelType,
        STEPControl_Writer,
    )

    reader = STEPControl_Reader()
    if reader.ReadFile(src_step) != IFSelect_RetDone:
        raise RuntimeError(f"_scale_export: STEP read failed -> {src_step}")
    reader.TransferRoots()
    shape = reader.OneShape()

    scale_trsf = gp_Trsf()
    scale_trsf.SetScaleFactor(float(scale))  # uniform scale about the origin
    trsf = scale_trsf
    if translate is not None:
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(*(float(c) for c in translate)))
        trsf = move.Multiplied(scale_trsf)  # translate ∘ scale
    scaled = BRepBuilderAPI_Transform(shape, trsf, True).Shape()

    writer = STEPControl_Writer()
    status = writer.Transfer(scaled, STEPControl_StepModelType.STEPControl_AsIs)
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(
            f"_scale_export: Transfer failed (status={status})"
        )
    if writer.Write(out_step) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(f"_scale_export: Write failed -> {out_step}")
    return out_step


def _new_pair_record(pair: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": pair.get("id"),
        "file_a": pair.get("file_a"),
        "file_b": pair.get("file_b"),
        "scale_a": pair.get("scale_a"),
        "scale_b": pair.get("scale_b"),
        "expected_label": pair.get("expected_label"),
        "expected_scale": pair.get("expected_scale"),
        "similarity": None,
        "classification": None,
        "coarse_family": None,
        "scale_factor": None,
        "registration_rmsd": None,
        "hausdorff": None,
        "rms": None,
        "p95": None,
        "error": None,
        "duration_s": None,
    }


def _resolve_pair_paths(pair: dict[str, Any], tmpdir: str) -> tuple[str, str]:
    """Resolve A/B input STEP paths, generating scaled variants into
    ``tmpdir`` as needed (the caller's TemporaryDirectory cleans them up).
    Returns (path_a, path_b)."""
    corpus = _corpus_dir()
    raw_a = str(corpus / str(pair["file_a"]))
    raw_b = str(corpus / str(pair["file_b"]))

    scale_a = pair.get("scale_a")
    scale_b = pair.get("scale_b")
    trans_a = pair.get("translate_a")
    trans_b = pair.get("translate_b")

    def _needs(scale, trans) -> bool:
        return (scale is not None and float(scale) != 1.0) or trans is not None

    if _needs(scale_a, trans_a):
        path_a = str(Path(tmpdir) / f"a_scaled_{pair.get('id','x')}.step")
        _scale_export(
            raw_a, float(scale_a) if scale_a is not None else 1.0, path_a,
            translate=tuple(trans_a) if trans_a is not None else None,
        )
    else:
        path_a = raw_a

    if _needs(scale_b, trans_b):
        path_b = str(Path(tmpdir) / f"b_scaled_{pair.get('id','x')}.step")
        _scale_export(
            raw_b, float(scale_b) if scale_b is not None else 1.0, path_b,
            translate=tuple(trans_b) if trans_b is not None else None,
        )
    else:
        path_b = raw_b

    return path_a, path_b


def _worker_pipeline(pair: dict[str, Any]) -> dict[str, Any]:
    """Run compare_parts on one pair. Never raises — failures land in
    ``record['error']``."""
    t0 = time.perf_counter()
    record = _new_pair_record(pair)
    try:
        _force_register_all()
        from phone_designer.skills.reverse_engineer.compare_parts import (
            CompareParts,
        )

        with tempfile.TemporaryDirectory(prefix="compare_regress_") as tmpdir:
            path_a, path_b = _resolve_pair_paths(pair, tmpdir)
            res = CompareParts().apply(
                None,
                {"part_a_path": path_a, "part_b_path": path_b},
            )
            cmp = res.extras.get("part_comparison") or {}

        record["similarity"] = cmp.get("similarity_score")
        record["classification"] = cmp.get("classification")
        record["coarse_family"] = _coarse_family(cmp.get("classification"))
        record["scale_factor"] = cmp.get("scale_factor")
        reg = cmp.get("registration")
        if isinstance(reg, dict):
            record["registration_rmsd"] = reg.get("rmsd_mm")
        gd = cmp.get("geometry_deviation")
        if isinstance(gd, dict):
            record["hausdorff"] = gd.get("hausdorff_mm")
            record["rms"] = gd.get("rms_mm")
            record["p95"] = gd.get("p95_mm")
    except Exception as exc:  # noqa: BLE001 — never let one pair kill the gate
        record["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        record["duration_s"] = round(time.perf_counter() - t0, 1)
    return record


def _worker_main(argv: list[str]) -> int:
    """``python -m phone_designer.corpus.compare_regress --worker <pair_json>
    <out_json>``."""
    pair_json, out_json = argv
    pair = json.loads(Path(pair_json).read_text(encoding="utf-8"))
    record = _worker_pipeline(pair)
    Path(out_json).write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Parent side — subprocess isolation + watchdog (clone of regress.run_one)


def run_one(
    pair: dict[str, Any],
    timeout_s: int = PER_PAIR_TIMEOUT_S,
) -> dict[str, Any]:
    """Run one pair's compare_parts in an isolated subprocess with a hard
    timeout watchdog."""
    repo = _repo_root()
    src = repo / "src"
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else str(src)

    pf = tempfile.NamedTemporaryFile(
        mode="w", prefix="compare_pair_", suffix=".json",
        delete=False, encoding="utf-8",
    )
    pf.write(json.dumps(pair, ensure_ascii=False))
    pf.close()
    pair_json = pf.name

    fd, out_json = tempfile.mkstemp(prefix="compare_regress_", suffix=".json")
    os.close(fd)

    t0 = time.perf_counter()
    try:
        cmd = [
            sys.executable,
            "-m",
            "phone_designer.corpus.compare_regress",
            "--worker",
            pair_json,
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
            record = _new_pair_record(pair)
            record["error"] = f"TIMEOUT after {timeout_s}s"
            record["duration_s"] = round(time.perf_counter() - t0, 1)
            return record

        try:
            record = json.loads(Path(out_json).read_text(encoding="utf-8"))
        except Exception:
            record = _new_pair_record(pair)
            tail = (proc.stderr or "").strip().splitlines()[-3:]
            record["error"] = (
                f"worker crashed (exit={proc.returncode}): "
                + " | ".join(tail)[:300]
            )
        record["duration_s"] = round(time.perf_counter() - t0, 1)
        return record
    finally:
        for p in (pair_json, out_json):
            try:
                os.unlink(p)
            except OSError:
                pass


def run_sweep(
    timeout_s: int = PER_PAIR_TIMEOUT_S,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Serial sweep over the committed pair manifest. Pairs whose source
    corpus files are absent are SKIPPED (requires_oem-style). Returns the
    per-pair records in manifest order."""
    pairs = load_manifest()
    log(
        f"[compare-regress] {len(pairs)} pair(s) in manifest, serial, "
        f"{timeout_s}s/pair cap"
    )
    records: list[dict[str, Any]] = []
    for i, pair in enumerate(pairs, 1):
        pid = pair.get("id") or f"pair{i}"
        if not _pair_source_present(pair):
            log(f"[{i:3d}/{len(pairs)}] {pid} ... SKIP (source file absent)")
            rec = _new_pair_record(pair)
            rec["skipped"] = "source file absent"
            records.append(rec)
            continue
        log(f"[{i:3d}/{len(pairs)}] {pid} ...")
        rec = run_one(pair, timeout_s=timeout_s)
        records.append(rec)
        err = rec.get("error")
        log(
            f"          -> class={rec.get('classification')} "
            f"sim={rec.get('similarity')} "
            f"scale={rec.get('scale_factor')} "
            f"rmsd={rec.get('registration_rmsd')} "
            f"err={err[:80] if err else None} ({rec.get('duration_s')}s)"
        )
    return records


# ──────────────────────────────────────────────────────────────────────────────
# Baseline IO + comparison


def save_baseline(records: list[dict[str, Any]]) -> Path:
    """Write corpus/baselines/compare_pairs.json. Skipped pairs are excluded
    from the baseline records (they carry no signal)."""
    path = baseline_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = {
        r["id"]: r for r in records
        if r.get("id") and not r.get("skipped")
    }
    payload = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "sim_drop_tol": SIM_DROP_TOL,
        "rmsd_rise_tol": RMSD_RISE_TOL,
        "records": keep,
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_baseline() -> dict[str, Any] | None:
    path = baseline_path()
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _num(v: Any) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def compare_to_baseline(
    records: list[dict[str, Any]],
    baseline: dict[str, Any],
    sim_drop_tol: float = SIM_DROP_TOL,
    rmsd_rise_tol: float = RMSD_RISE_TOL,
) -> dict[str, Any]:
    """Compare a fresh sweep against a baseline.

    REGRESSION for a pair present in the baseline =
      * the COARSE FAMILY (same/different) of its classification flips, OR
      * a NEW error appears, OR
      * similarity_score drops by more than ``sim_drop_tol``, OR
      * registration_rmsd rises by more than ``rmsd_rise_tol``.

    An adjacent label move within the same coarse family (e.g. identical <->
    parametric_variant) is NOT a regression. New pairs (not in baseline) and
    skipped pairs are informational only.
    """
    base_records: dict[str, dict] = baseline.get("records") or {}
    regressions: list[dict[str, Any]] = []
    new_pairs: list[str] = []
    skipped: list[str] = []

    for rec in records:
        pid = rec.get("id")
        if rec.get("skipped"):
            skipped.append(pid)
            continue
        base = base_records.get(pid)
        if base is None:
            new_pairs.append(pid)
            continue

        cur_err = rec.get("error")
        base_err = base.get("error")
        if cur_err and not base_err:
            regressions.append(
                {"id": pid, "reason": f"new error: {cur_err}"}
            )
            continue

        # ── coarse-family flip ──────────────────────────────────────────────
        base_fam = base.get("coarse_family") or _coarse_family(
            base.get("classification")
        )
        cur_fam = rec.get("coarse_family") or _coarse_family(
            rec.get("classification")
        )
        if (
            base_fam in ("same", "different")
            and cur_fam in ("same", "different")
            and base_fam != cur_fam
        ):
            regressions.append({
                "id": pid,
                "reason": (
                    f"classification family flip "
                    f"{base.get('classification')} ({base_fam}) -> "
                    f"{rec.get('classification')} ({cur_fam})"
                ),
                "baseline_class": base.get("classification"),
                "current_class": rec.get("classification"),
            })
            continue

        # ── similarity drop ────────────────────────────────────────────────
        bs = _num(base.get("similarity"))
        cs = _num(rec.get("similarity"))
        if bs is not None:
            if cs is None:
                regressions.append({
                    "id": pid,
                    "reason": f"similarity {bs} -> None",
                })
                continue
            if bs - cs > sim_drop_tol:
                regressions.append({
                    "id": pid,
                    "reason": (
                        f"similarity dropped {bs} -> {cs} "
                        f"(-{round(bs - cs, 4)})"
                    ),
                    "baseline_similarity": bs,
                    "current_similarity": cs,
                })
                continue

        # ── rmsd rise ──────────────────────────────────────────────────────
        br = _num(base.get("registration_rmsd"))
        cr = _num(rec.get("registration_rmsd"))
        if br is not None and cr is not None and cr - br > rmsd_rise_tol:
            regressions.append({
                "id": pid,
                "reason": (
                    f"registration_rmsd rose {br} -> {cr} "
                    f"(+{round(cr - br, 4)})"
                ),
                "baseline_rmsd": br,
                "current_rmsd": cr,
            })

    run_ids = {r.get("id") for r in records}
    missing = sorted(set(base_records) - run_ids)
    return {
        "regressions": regressions,
        "new_pairs": new_pairs,
        "skipped": skipped,
        "missing_pairs": missing,
        "ok": not regressions,
    }


if __name__ == "__main__":  # worker entry
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        sys.exit(_worker_main(sys.argv[2:]))
    print(
        "usage: python -m phone_designer.corpus.compare_regress "
        "--worker <pair_json> <out_json>",
        file=sys.stderr,
    )
    sys.exit(2)
