"""Batch single-part analysis harness (2026-06-18).

Runs the already-built, already-verified ``analyze_part`` front-door skill over
MANY CAD files at once and produces a combined deliverable. Pure COMPOSITION +
IO — it touches NONE of the corpus-gated detector/planner code; every file goes
through the exact same ``AnalyzePart`` macro the single-part CLI ``analyze``
uses, so there is nothing new to gate here.

Isolation, borrowed from ``corpus.regress.run_one``: each file is analyzed in
its OWN subprocess with a hard ``--timeout-s`` watchdog, so a hung OCCT call (a
700 s+ assembly) costs at most ``timeout_s`` of the batch instead of wedging the
whole run. Like ``corpus-regress``:

  * ``workers=1`` (default) is the serial path — one subprocess at a time.
  * ``workers>1`` fans the per-file subprocesses across a ``ProcessPoolExecutor``
    (capped at ``min(cpu-2, n_files)``). HONEST CAVEAT — the per-file timeout is
    a wall-clock watchdog and N concurrent workers share CPU, so a file sitting
    near the timeout boundary serially can spuriously TIME OUT under contention
    (same caveat as ``corpus.regress._effective_workers``). The authoritative
    run is therefore ``workers=1``; use ``workers>1`` for fast iteration on the
    small-file bulk and raise ``timeout_s`` proportionally if a boundary file
    flags a false timeout.

Deliverable written under ``out_dir``:

  * ``<part_id>.html`` and/or ``<part_id>.json`` per part (the analyze_part
    report), governed by ``fmt`` ("html" | "json" | "both").
  * ``index.json`` — ``{generated_at_placeholder, n_files, n_ok, n_failed,
    parts:[...]}``; each part entry carries ``{part_id, ok, error, duration_s,
    bbox_mm, feature_counts, key_dimensions, reconstruction, report paths}``.
  * ``index.html`` — a self-contained, dependency-free sortable-ish table linking
    each per-part report (inline CSS, reusing the ``_report_html`` palette).

The per-file analysis itself runs in a subprocess that invokes
``python -m phone_designer.corpus.batch_analyze --worker <file> <fmt>
<reconstruct> <processes_csv> <out_dir> <out_json>``; the worker writes the
per-part report(s) AND a compact summary record that the parent collects.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from html import escape
from pathlib import Path
from typing import Any, Callable

#: per-file subprocess hard timeout (seconds) — the analyze_part front door is
#: heavier than the RE round-trip, so default a touch higher than corpus-regress.
PER_FILE_TIMEOUT_S = 300

#: CAD suffixes accepted when a directory or bare glob is expanded.
_CAD_SUFFIXES = (".step", ".stp", ".iges", ".igs", ".brep")


def _repo_root() -> Path:
    """Repository root — 3 levels up (corpus → phone_designer → src → repo)."""
    return Path(__file__).resolve().parents[3]


# ──────────────────────────────────────────────────────────────────────────────
# Input resolution — paths / globs / directories → a flat file list


def resolve_inputs(
    paths: list[str] | None = None,
    glob: str | None = None,
) -> list[Path]:
    """Resolve the batch inputs to a de-duplicated, sorted list of CAD files.

    * Each entry in ``paths`` may be a file, a directory (→ all CAD files
      directly inside, non-recursive, matching ``_CAD_SUFFIXES``), or a glob
      pattern (e.g. ``corpus/oem/kicad__*.step``).
    * ``glob`` is an additional convenience pattern, resolved the same way.

    Sorted smallest-file-first (fast feedback), like ``regress.discover_files``;
    duplicates (same resolved path) are collapsed.
    """
    out: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        if not p.is_file() or p.suffix.lower() not in _CAD_SUFFIXES:
            return
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            out.append(p)

    def _expand(token: str) -> None:
        p = Path(token)
        if p.is_dir():
            for child in sorted(p.iterdir()):
                _add(child)
            return
        if p.is_file():
            _add(p)
            return
        # treat as a glob — relative patterns resolve from cwd, like a shell
        anchor = Path(p.anchor) if p.anchor else Path.cwd()
        pattern = str(p) if p.anchor else str(p)
        if p.anchor:
            # absolute pattern: split anchor from the rest for Path.glob
            rest = str(p.relative_to(p.anchor)).replace("\\", "/")
            for match in sorted(anchor.glob(rest)):
                _add(match)
        else:
            for match in sorted(Path.cwd().glob(pattern.replace("\\", "/"))):
                _add(match)

    for token in paths or []:
        _expand(token)
    if glob:
        _expand(glob)

    out.sort(key=lambda p: (p.stat().st_size if p.exists() else 0, p.name))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Per-file summary record


def _new_record(part_id: str, source: str) -> dict[str, Any]:
    return {
        "part_id": part_id,
        "source": source,
        "ok": False,
        "error": None,
        "duration_s": None,
        "bbox_mm": None,
        "feature_counts": None,
        "key_dimensions": None,
        "reconstruction": None,
        "report_html": None,
        "report_json": None,
    }


def _summarize_analysis(
    pa: dict[str, Any], part_id: str, source: str
) -> dict[str, Any]:
    """Distil an ``extras['part_analysis']`` dict into the compact batch record
    (names + values only — never the bulky report_html / raw pdf bytes)."""
    rec = _new_record(part_id, source)
    rec["part_id"] = pa.get("part_id") or part_id
    rec["bbox_mm"] = pa.get("bbox_mm")

    cat = pa.get("feature_catalog")
    if isinstance(cat, dict):
        rec["feature_counts"] = {
            k: v for k, v in (cat.get("counts") or {}).items() if v
        }

    kds = pa.get("key_dimensions")
    if isinstance(kds, list):
        rec["key_dimensions"] = [
            {"name": d.get("name"), "value_mm": d.get("value_mm")}
            for d in kds
            if isinstance(d, dict)
        ]

    recon = pa.get("reconstruction")
    if isinstance(recon, dict):
        rec["reconstruction"] = {
            "strategy": recon.get("strategy"),
            "match_ratio": recon.get("match_ratio"),
            "hausdorff_mm": recon.get("hausdorff_mm"),
            "base_mechanism": recon.get("base_mechanism"),
            "plan_steps": recon.get("plan_steps"),
        }

    # an import-stage failure leaves report None — surface it honestly
    if pa.get("report") is None and not rec["feature_counts"]:
        err = pa.get("error")
        stages = pa.get("_stages") or {}
        imp = stages.get("import") or {}
        rec["error"] = err or imp.get("error") or "analysis produced no report"
        rec["ok"] = False
    else:
        rec["ok"] = True
    return rec


# ──────────────────────────────────────────────────────────────────────────────
# Worker side — runs INSIDE the per-file subprocess


def _analyze_one_inprocess(
    part_path: str,
    fmt: str,
    reconstruct: bool,
    processes: list[str],
    out_dir: str,
) -> dict[str, Any]:
    """Analyze ONE file with ``AnalyzePart``, write its per-part report(s) into
    ``out_dir``, and return the compact summary record. Never raises — failures
    land in ``record['error']`` so one bad file can't abort the batch."""
    t0 = time.perf_counter()
    part_id = Path(part_path).stem
    rec = _new_record(part_id, part_path)
    try:
        from phone_designer.skills.reverse_engineer.analyze_part import (
            AnalyzePart,
        )

        want_html = fmt in ("html", "both")
        want_json = fmt in ("json", "both")
        res = AnalyzePart().apply(
            None,
            {
                "part_path": part_path,
                "processes": list(processes),
                "include_html": want_html,
                "reconstruct": bool(reconstruct),
            },
        )
        pa = res.extras["part_analysis"]
        part_id = pa.get("part_id") or part_id
        rec = _summarize_analysis(pa, part_id, part_path)

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        if want_html:
            html = pa.get("report_html") or ""
            if html:
                hp = out / f"{part_id}.html"
                hp.write_text(html, encoding="utf-8")
                rec["report_html"] = hp.name
        if want_json:
            # the per-part JSON is the full analysis minus bulky html/pdf bytes
            dump = dict(pa)
            dump.pop("report_html", None)
            if isinstance(dump.get("report_pdf"), dict):
                rp = dict(dump["report_pdf"])
                rp.pop("pdf_bytes", None)
                dump["report_pdf"] = rp
            jp = out / f"{part_id}.json"
            jp.write_text(
                json.dumps(dump, indent=1, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
            rec["report_json"] = jp.name
    except Exception as exc:  # noqa: BLE001 — one bad file must not kill the batch
        rec["ok"] = False
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        rec["duration_s"] = round(time.perf_counter() - t0, 2)
    return rec


def _worker_main(argv: list[str]) -> int:
    """``python -m phone_designer.corpus.batch_analyze --worker <file> <fmt>
    <reconstruct 0|1> <processes_csv> <out_dir> <out_json>``."""
    part_path, fmt, reconstruct_s, processes_csv, out_dir, out_json = argv[:6]
    processes = [p for p in processes_csv.split(",") if p.strip()]
    rec = _analyze_one_inprocess(
        part_path,
        fmt,
        reconstruct_s in ("1", "true", "True"),
        processes,
        out_dir,
    )
    Path(out_json).write_text(
        json.dumps(rec, ensure_ascii=False), encoding="utf-8"
    )
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Parent side — subprocess isolation + watchdog (mirrors regress.run_one)


def run_one(
    path: Path | str,
    fmt: str,
    reconstruct: bool,
    processes: list[str],
    out_dir: str,
    timeout_s: int = PER_FILE_TIMEOUT_S,
) -> dict[str, Any]:
    """Analyze one file in an isolated subprocess. ``subprocess.run(timeout=...)``
    kills the child on expiry, so a hung OCCT call costs at most ``timeout_s``.
    Mirrors ``corpus.regress.run_one``."""
    repo = _repo_root()
    src = repo / "src"
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else str(src)

    part_id = Path(path).stem
    fd, out_json = tempfile.mkstemp(prefix="batch_analyze_", suffix=".json")
    os.close(fd)
    t0 = time.perf_counter()
    try:
        cmd = [
            sys.executable,
            "-m",
            "phone_designer.corpus.batch_analyze",
            "--worker",
            str(path),
            fmt,
            "1" if reconstruct else "0",
            ",".join(processes),
            str(out_dir),
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
            rec = _new_record(part_id, str(path))
            rec["error"] = f"TIMEOUT after {timeout_s}s"
            rec["duration_s"] = round(time.perf_counter() - t0, 2)
            return rec

        try:
            rec = json.loads(Path(out_json).read_text(encoding="utf-8"))
        except Exception:
            rec = _new_record(part_id, str(path))
            tail = (proc.stderr or "").strip().splitlines()[-3:]
            rec["error"] = (
                f"worker crashed (exit={proc.returncode}): "
                + " | ".join(tail)[:300]
            )
        rec["duration_s"] = round(time.perf_counter() - t0, 2)
        return rec
    finally:
        try:
            os.unlink(out_json)
        except OSError:
            pass


def _effective_workers(requested: int, n_files: int) -> int:
    """Resolve the worker count actually used — capped at ``min(cpu-2,
    n_files)`` and never below 1 (same policy as ``regress._effective_workers``;
    see this module's docstring for the timeout-boundary honesty caveat)."""
    if requested <= 1 or n_files <= 1:
        return 1
    cpu = os.cpu_count() or 1
    cap = max(1, min(cpu - 2, n_files))
    return max(1, min(requested, cap))


def _parallel_worker(
    args: tuple[int, str, str, str, bool, tuple[str, ...], str, int]
) -> tuple[int, dict[str, Any]]:
    idx, part_id, path, fmt, reconstruct, processes, out_dir, timeout_s = args
    rec = run_one(
        path, fmt, reconstruct, list(processes), out_dir, timeout_s=timeout_s
    )
    return idx, rec


def run_batch(
    files: list[Path],
    out_dir: Path | str,
    fmt: str = "html",
    reconstruct: bool = False,
    processes: list[str] | None = None,
    timeout_s: int = PER_FILE_TIMEOUT_S,
    workers: int = 1,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Analyze ``files`` and write the combined deliverable under ``out_dir``.

    Returns the ``index.json`` payload (also written to disk together with
    ``index.html`` and the per-part reports). Results are kept in INPUT order
    regardless of worker count."""
    if fmt not in ("html", "json", "both"):
        raise ValueError(f"unknown fmt: {fmt!r} (expected html|json|both)")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    procs = list(processes or ["cnc_milling", "injection_molding"])
    eff_workers = _effective_workers(workers, len(files))

    records: list[dict[str, Any]]
    if eff_workers <= 1:
        log(
            f"[analyze-batch] {len(files)} file(s), serial, "
            f"{timeout_s}s/file cap, fmt={fmt}, reconstruct={reconstruct}"
        )
        records = []
        for i, p in enumerate(files, 1):
            log(f"[{i:3d}/{len(files)}] {p.name} ...")
            rec = run_one(
                p, fmt, reconstruct, procs, str(out), timeout_s=timeout_s
            )
            records.append(rec)
            log(
                f"          -> ok={rec.get('ok')} "
                f"err={(rec.get('error') or '')[:60] or None} "
                f"({rec.get('duration_s')}s)"
            )
    else:
        records = _run_batch_parallel(
            files, out, fmt, reconstruct, procs, timeout_s, eff_workers, log
        )

    n_ok = sum(1 for r in records if r.get("ok"))
    index = {
        "generated_at_placeholder": "GENERATED_AT",
        "n_files": len(records),
        "n_ok": n_ok,
        "n_failed": len(records) - n_ok,
        "parts": records,
    }
    (out / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out / "index.html").write_text(_render_index_html(index), encoding="utf-8")
    log(
        f">>> index: {out / 'index.json'}  "
        f"({n_ok}/{len(records)} ok)"
    )
    return index


def _run_batch_parallel(
    files: list[Path],
    out: Path,
    fmt: str,
    reconstruct: bool,
    procs: list[str],
    timeout_s: int,
    workers: int,
    log: Callable[[str], None],
) -> list[dict[str, Any]]:
    from concurrent.futures import ProcessPoolExecutor, as_completed

    log(
        f"[analyze-batch] {len(files)} file(s), {workers} workers "
        f"(cap min(cpu-2, n_files)), {timeout_s}s/file cap, fmt={fmt}"
    )
    tasks = [
        (idx, p.stem, str(p), fmt, reconstruct, tuple(procs), str(out), timeout_s)
        for idx, p in enumerate(files)
    ]
    results: dict[int, dict[str, Any]] = {}
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_parallel_worker, t): t[0] for t in tasks}
        for fut in as_completed(futs):
            idx, rec = fut.result()
            results[idx] = rec
            done += 1
            log(
                f"[{done:3d}/{len(files)}] {rec.get('part_id')} "
                f"-> ok={rec.get('ok')} ({rec.get('duration_s')}s)"
            )
    return [results[i] for i in range(len(files))]


# ──────────────────────────────────────────────────────────────────────────────
# Combined index.html — self-contained, dependency-free (reuses _report_html
# palette conventions). The tiny inline sort script is pure vanilla JS.

_INDEX_CSS = """
*{box-sizing:border-box}
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
color:#1f2328;background:#f6f8fa;margin:0;padding:24px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:#656d76;font-size:13px;margin:0 0 18px}
.kpi{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 18px}
.card{border:1px solid #d0d7de;border-radius:8px;padding:8px 14px;
min-width:90px;background:#fff}
.card .n{font-size:20px;font-weight:700}
.card .l{font-size:11px;color:#656d76;text-transform:uppercase;letter-spacing:.4px}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #d0d7de;
border-radius:8px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #eaeef2;font-size:13px;
vertical-align:top}
th{color:#656d76;font-weight:600;background:#f6f8fa;cursor:pointer;user-select:none}
th:hover{color:#0969da}
tr:last-child td{border-bottom:none}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.ok{color:#1a7f37;font-weight:600}.bad{color:#cf222e;font-weight:600}
a{color:#0969da;text-decoration:none}a:hover{text-decoration:underline}
.dims{color:#656d76;font-size:12px}
.err{color:#cf222e;font-size:12px}
footer{color:#8c959f;font-size:11px;margin-top:14px}
"""

_INDEX_SORT_JS = """
function sortTable(n){
 var t=document.getElementById('parts'),rows=Array.prototype.slice.call(
  t.tBodies[0].rows),asc=t.getAttribute('data-asc')!=String(n);
 rows.sort(function(a,b){
  var x=a.cells[n].getAttribute('data-v')||a.cells[n].innerText,
      y=b.cells[n].getAttribute('data-v')||b.cells[n].innerText,
      nx=parseFloat(x),ny=parseFloat(y);
  if(!isNaN(nx)&&!isNaN(ny)){return asc?nx-ny:ny-nx;}
  return asc?String(x).localeCompare(y):String(y).localeCompare(x);});
 rows.forEach(function(r){t.tBodies[0].appendChild(r);});
 t.setAttribute('data-asc',asc?String(n):'');}
"""


def _fmt_bbox(bbox: list[float] | None) -> str:
    if not bbox or len(bbox) < 6:
        return "—"
    dx = round(bbox[3] - bbox[0], 1)
    dy = round(bbox[4] - bbox[1], 1)
    dz = round(bbox[5] - bbox[2], 1)
    return f"{dx} × {dy} × {dz}"


def _bbox_diag(bbox: list[float] | None) -> float:
    if not bbox or len(bbox) < 6:
        return 0.0
    dx = bbox[3] - bbox[0]
    dy = bbox[4] - bbox[1]
    dz = bbox[5] - bbox[2]
    return round((dx * dx + dy * dy + dz * dz) ** 0.5, 2)


def _render_index_html(index: dict[str, Any]) -> str:
    parts = index.get("parts") or []
    rows: list[str] = []
    for r in parts:
        pid = escape(str(r.get("part_id") or "?"))
        ok = bool(r.get("ok"))
        status = (
            "<span class='ok'>ok</span>" if ok else "<span class='bad'>FAIL</span>"
        )

        # report link
        link = "—"
        if r.get("report_html"):
            link = f"<a href='{escape(r['report_html'])}'>html</a>"
        if r.get("report_json"):
            jl = f"<a href='{escape(r['report_json'])}'>json</a>"
            link = f"{link} {jl}" if link != "—" else jl

        bbox = r.get("bbox_mm")
        bbox_str = escape(_fmt_bbox(bbox))
        diag = _bbox_diag(bbox)

        counts = r.get("feature_counts") or {}
        feat_str = (
            escape(", ".join(f"{k}={v}" for k, v in counts.items()))
            if counts
            else "—"
        )
        n_feat = sum(int(v) for v in counts.values()) if counts else 0

        kds = r.get("key_dimensions") or []
        dim_bits = [
            f"{escape(str(d.get('name')))}={d.get('value_mm')}"
            for d in kds[:4]
            if isinstance(d, dict)
        ]
        dim_str = (
            "<span class='dims'>" + escape("; ".join(dim_bits)) + "</span>"
            if dim_bits
            else "—"
        )

        recon = r.get("reconstruction") or {}
        haus = recon.get("hausdorff_mm")
        if haus is None:
            haus_str = "—"
        else:
            # surface WHICH strategy produced this hausdorff — a sparse-assembly
            # part routed to preserve (152mm) vs a box failure (1009mm) read very
            # differently, and the strategy was being dropped from the record.
            strat = recon.get("strategy")
            strat_lbl = (
                "" if strat in (None, "box")
                else " <span class='dims'>preserve</span>"
                if "preserve" in str(strat)
                else f" <span class='dims'>{escape(str(strat))}</span>"
            )
            haus_str = f"{haus}{strat_lbl}"

        err = r.get("error")
        if err:
            feat_str = f"<span class='err'>{escape(str(err)[:90])}</span>"

        dur = r.get("duration_s")
        rows.append(
            "<tr>"
            f"<td data-v='{pid}'>{pid}</td>"
            f"<td data-v='{'1' if ok else '0'}'>{status}</td>"
            f"<td class='num' data-v='{diag}'>{bbox_str}</td>"
            f"<td class='num' data-v='{n_feat}'>{feat_str}</td>"
            f"<td data-v='{escape(str(dim_str))}'>{dim_str}</td>"
            f"<td class='num' data-v='{haus if haus is not None else 1e9}'>{haus_str}</td>"
            f"<td class='num' data-v='{dur if dur is not None else 0}'>{dur}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )

    head = (
        "<tr>"
        "<th onclick='sortTable(0)'>part</th>"
        "<th onclick='sortTable(1)'>status</th>"
        "<th onclick='sortTable(2)'>bbox (mm)</th>"
        "<th onclick='sortTable(3)'>features</th>"
        "<th onclick='sortTable(4)'>key dims</th>"
        "<th onclick='sortTable(5)'>recon haus (mm)</th>"
        "<th onclick='sortTable(6)'>dur (s)</th>"
        "<th>report</th>"
        "</tr>"
    )

    n_files = index.get("n_files", len(parts))
    n_ok = index.get("n_ok", 0)
    n_failed = index.get("n_failed", 0)

    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Batch part analysis</title>"
        f"<style>{_INDEX_CSS}</style></head><body>"
        "<h1>Batch part analysis</h1>"
        "<p class='sub'>analyze_part front-door over a CAD batch — click a "
        "header to sort.</p>"
        "<div class='kpi'>"
        f"<div class='card'><div class='n'>{n_files}</div>"
        "<div class='l'>files</div></div>"
        f"<div class='card'><div class='n ok'>{n_ok}</div>"
        "<div class='l'>ok</div></div>"
        f"<div class='card'><div class='n bad'>{n_failed}</div>"
        "<div class='l'>failed</div></div>"
        "</div>"
        "<table id='parts' data-asc=''>"
        f"<thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"
        "<footer>generated_at_placeholder = GENERATED_AT · "
        "self-contained · dependency-free</footer>"
        f"<script>{_INDEX_SORT_JS}</script>"
        "</body></html>"
    )


if __name__ == "__main__":  # worker entry
    if len(sys.argv) >= 8 and sys.argv[1] == "--worker":
        sys.exit(_worker_main(sys.argv[2:]))
    print(
        "usage: python -m phone_designer.corpus.batch_analyze --worker "
        "<file> <fmt> <reconstruct 0|1> <processes_csv> <out_dir> <out_json>",
        file=sys.stderr,
    )
    sys.exit(2)
