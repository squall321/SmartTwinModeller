"""regen_report — human-readable RE round-trip report for two STEP files.

Pipeline:

    ImportStep(orig) ──┐
                       ├──> ExtractFeatureCatalog ──┐
    ImportStep(regen) ─┘                            │
                                                    ├──> FeatureFidelityDiff
                                                    │
                                                    └──> printable report

Surfaces the rich per-kind/per-dim ``drift_breakdown`` that
``feature_fidelity_diff`` started emitting on 2026-06-09 but which is
currently buried under ``extras["feature_fidelity"]["drift_breakdown"]``.

Usage (from repo root)::

    venv/Scripts/python.exe tools/regen_report.py <orig.step> <regen.step>
    venv/Scripts/python.exe tools/regen_report.py orig.step regen.step --json out.json
    venv/Scripts/python.exe tools/regen_report.py orig.step regen.step --threshold 0.7

Exit codes:

    0  overall_match_ratio >= threshold (default 0.5) — CI passes
    1  overall_match_ratio <  threshold               — CI / regression gate

The threshold default matches the corpus_complexity_audit_v3 acceptance
line ("54/55 files match >= 0.5") so this tool's exit semantics line up
with the existing audit headline.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any


# ── import-path bootstrap (so `python tools/regen_report.py` works
#    without pip install -e .) ────────────────────────────────────────
def _ensure_import_path() -> None:
    here = pathlib.Path(__file__).resolve().parent
    src = here.parent / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


# ── pipeline ────────────────────────────────────────────────────────
def _import_step(path: str) -> Any:
    from phone_designer.skills.create.import_step import ImportStep
    res = ImportStep().apply(None, {"path": path})
    return res.body


def _extract_catalog(body: Any) -> dict:
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    res = ExtractFeatureCatalog().apply(body, {})
    return res.extras.get("feature_catalog", {}) or {}


def _fidelity_diff(body: Any, cat_a: dict, cat_b: dict) -> dict:
    from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
        FeatureFidelityDiff,
    )
    # body arg satisfies the body_present post-condition; the skill itself
    # only compares the two catalog dicts.
    res = FeatureFidelityDiff().apply(
        body, {"catalog_a": cat_a, "catalog_b": cat_b},
    )
    return res.extras.get("feature_fidelity", {}) or {}


# ── rendering ───────────────────────────────────────────────────────
def _fmt_pct(x: float | None, width: int = 7) -> str:
    if x is None:
        return "n/a".rjust(width)
    return f"{x:>{width - 1}.2f}%"


def _fmt_ratio(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x:.4f}"


def _render_overall(report: dict, orig: str, regen: str) -> str:
    ratio = report.get("overall_match_ratio")
    drift = report.get("avg_dim_drift_pct")
    tol = report.get("xyz_tol_mm")
    return (
        "═══ FEATURE FIDELITY REPORT ═══════════════════════════════\n"
        f"  orig : {orig}\n"
        f"  regen: {regen}\n"
        f"\n"
        f"  overall_match_ratio : {_fmt_ratio(ratio)}\n"
        f"  avg_dim_drift_pct   : {_fmt_pct(drift, width=7).strip()}\n"
        f"  spatial xyz tol (mm): {tol if tol is not None else 'n/a'}\n"
    )


def _render_by_kind(report: dict) -> str:
    by_kind = report.get("by_kind") or {}
    lines = [
        "",
        "── per-kind summary ───────────────────────────────────────",
        f"  {'kind':<20} {'orig':>5} {'regen':>5} {'diff':>5}"
        f" {'match':>7} {'%match':>8}",
    ]
    for kind, row in by_kind.items():
        a = row.get("a", 0)
        b = row.get("b", 0)
        diff = row.get("diff", 0)
        matched = row.get("matched", 0)
        denom = max(a, b)
        pct = (matched / denom * 100.0) if denom else 100.0
        lines.append(
            f"  {kind:<20} {a:>5} {b:>5} {diff:>+5}"
            f" {matched:>7} {pct:>7.1f}%"
        )
    return "\n".join(lines) + "\n"


def _render_drift_table(report: dict) -> str:
    breakdown = report.get("drift_breakdown") or {}
    if not breakdown:
        return (
            "\n── per-kind / per-dim drift ───────────────────────────────\n"
            "  (no matched pairs with comparable *_mm dims)\n"
        )
    lines = [
        "",
        "── per-kind / per-dim drift ───────────────────────────────",
        f"  {'kind':<20} {'dim':<18} {'pairs':>5}"
        f" {'mean %':>8} {'max %':>8}  worst_pair",
    ]
    for kind, per_dim in breakdown.items():
        # sort dims by descending max_pct so the worst offender appears first
        items = sorted(
            per_dim.items(),
            key=lambda kv: kv[1].get("max_pct", 0.0),
            reverse=True,
        )
        for dim, d in items:
            pairs = d.get("pair_count", 0)
            mean = d.get("mean_pct", 0.0)
            mx = d.get("max_pct", 0.0)
            worst = d.get("worst_pair_idx")
            worst_str = f"a={worst[0]:>3} b={worst[1]:>3}" if worst else "—"
            lines.append(
                f"  {kind:<20} {dim:<18} {pairs:>5}"
                f" {mean:>7.2f}% {mx:>7.2f}%  {worst_str}"
            )
    return "\n".join(lines) + "\n"


def _render_worst_pairs(
    report: dict,
    cat_a: dict,
    cat_b: dict,
    top_n: int = 5,
) -> str:
    """Top N (kind, dim, drift_pct) tuples — which feature drifted most."""
    breakdown = report.get("drift_breakdown") or {}
    candidates: list[tuple[float, str, str, tuple[int, int]]] = []
    for kind, per_dim in breakdown.items():
        for dim, d in per_dim.items():
            mx = d.get("max_pct", 0.0)
            worst = d.get("worst_pair_idx")
            if worst is None or mx <= 0:
                continue
            candidates.append((mx, kind, dim, tuple(worst)))
    candidates.sort(reverse=True)
    top = candidates[:top_n]

    lines = [
        "",
        f"── top {top_n} worst-drift pairs ──────────────────────────────",
    ]
    if not top:
        lines.append("  (no measurable drift on any matched pair)")
        return "\n".join(lines) + "\n"

    for rank, (pct, kind, dim, (ai, bi)) in enumerate(top, 1):
        a_entry = (cat_a.get(kind) or [None])[ai] if ai < len(
            cat_a.get(kind) or []
        ) else None
        b_entry = (cat_b.get(kind) or [None])[bi] if bi < len(
            cat_b.get(kind) or []
        ) else None
        av = (a_entry or {}).get(dim) if isinstance(a_entry, dict) else None
        bv = (b_entry or {}).get(dim) if isinstance(b_entry, dict) else None
        lines.append(
            f"  #{rank} {kind}/{dim}: orig[{ai}]={av} → regen[{bi}]={bv}"
            f"  ({pct:.2f}% drift)"
        )
    return "\n".join(lines) + "\n"


def _render_unmatched(report: dict, max_show: int = 10) -> str:
    missing = report.get("missing_in_b") or []
    extra = report.get("extra_in_b") or []
    lines = [
        "",
        "── unmatched features ─────────────────────────────────────",
        f"  missing in regen: {len(missing)}",
        f"  extra in regen  : {len(extra)}",
    ]
    if missing:
        head = ", ".join(f"{k}[{i}]" for k, i in missing[:max_show])
        more = f" (+{len(missing) - max_show} more)" if len(missing) > max_show else ""
        lines.append(f"    missing: {head}{more}")
    if extra:
        head = ", ".join(f"{k}[{i}]" for k, i in extra[:max_show])
        more = f" (+{len(extra) - max_show} more)" if len(extra) > max_show else ""
        lines.append(f"    extra  : {head}{more}")
    return "\n".join(lines) + "\n"


def _render(
    report: dict,
    cat_a: dict,
    cat_b: dict,
    orig: str,
    regen: str,
    top_n: int,
) -> str:
    return (
        _render_overall(report, orig, regen)
        + _render_by_kind(report)
        + _render_drift_table(report)
        + _render_worst_pairs(report, cat_a, cat_b, top_n=top_n)
        + _render_unmatched(report)
    )


# ── main ────────────────────────────────────────────────────────────
def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="regen_report",
        description="Compare two STEP files via the standard "
                    "ImportStep -> ExtractFeatureCatalog -> "
                    "FeatureFidelityDiff pipeline and print a "
                    "human-readable per-kind / per-dim drift report.",
    )
    ap.add_argument("orig", help="path to original STEP file")
    ap.add_argument("regen", help="path to regenerated STEP file")
    ap.add_argument(
        "--threshold", type=float, default=0.5,
        help="exit 1 if overall_match_ratio < threshold (default 0.5)",
    )
    ap.add_argument(
        "--top", type=int, default=5,
        help="how many worst-drift pairs to detail (default 5)",
    )
    ap.add_argument(
        "--json", dest="json_path", default=None,
        help="also write the full raw fidelity report to this JSON path",
    )
    ap.add_argument(
        "--quiet", action="store_true",
        help="suppress the per-kind and drift tables; print only the headline "
             "match ratio (useful for CI log noise reduction)",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _ensure_import_path()
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))

    for label, p in (("orig", args.orig), ("regen", args.regen)):
        if not pathlib.Path(p).exists():
            print(f"ERROR: {label} STEP not found: {p}", file=sys.stderr)
            return 2

    try:
        body_a = _import_step(args.orig)
        body_b = _import_step(args.regen)
        cat_a = _extract_catalog(body_a)
        cat_b = _extract_catalog(body_b)

        # Guard against catalog SKIP (too_big sentinel) — extract_feature_catalog
        # bails on > 16 k faces. The diff would still run but every kind would
        # be 0 vs 0 and overall_match_ratio collapses to a meaningless 1.0.
        for label, cat in (("orig", cat_a), ("regen", cat_b)):
            if isinstance(cat, dict) and cat.get("skipped"):
                print(
                    f"ERROR: {label} catalog SKIPPED ({cat.get('reason')}, "
                    f"face_count={cat.get('face_count')}, "
                    f"limit={cat.get('limit')}). Decimate or simplify first.",
                    file=sys.stderr,
                )
                return 2

        report = _fidelity_diff(body_a, cat_a, cat_b)
    except Exception as e:
        print(f"ERROR: pipeline failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    if args.json_path:
        pathlib.Path(args.json_path).write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8",
        )

    if args.quiet:
        ratio = report.get("overall_match_ratio")
        print(f"overall_match_ratio={_fmt_ratio(ratio)}")
    else:
        print(_render(report, cat_a, cat_b, args.orig, args.regen, args.top))

    ratio = report.get("overall_match_ratio")
    if ratio is None or ratio < args.threshold:
        print(
            f"FAIL: overall_match_ratio={_fmt_ratio(ratio)} "
            f"< threshold={args.threshold}",
            file=sys.stderr,
        )
        return 1
    print(
        f"PASS: overall_match_ratio={_fmt_ratio(ratio)} "
        f">= threshold={args.threshold}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
