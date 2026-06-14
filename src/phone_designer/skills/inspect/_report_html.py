"""_report_html — production HTML renderer for emit_quality_report (phase-3, 2026-06-13).

Self-contained, dependency-free HTML rendering of a ``QualityReportV1`` dict
(``emit_quality_report.build_report_v1`` output). Inline CSS only — NO external
assets (no <link>, no <script>, no external src), NO jinja2 (jinja2 is not a
project dependency — see pyproject; we hand-roll with ``str`` templates to keep
the renderer importable in headless/CI with nothing but the stdlib).

Why a separate module
---------------------
The Phase-1/2 renderer lived inline in ``emit_quality_report``. Lifting it here
(a) keeps the skill module focused on the JSON contract + skill wiring, and
(b) gives the production renderer + the report-snapshot regression a single,
testable surface (``render_html``) without dragging in OCCT / pyvista.

Public API
----------
``render_html(report_v1, views=None) -> str``

When ``views`` is ``None`` (the ``render_views=False`` default) the HTML has no
images, no render CSS, and no <img> markup — a stable, byte-identical-in-spirit
text document. When ``views`` is a ``_report_render.build_views`` dict with
``render_status='ok'`` the base64 PNGs are embedded inline as ``data:`` URIs so
the report stays a single self-contained file. A non-'ok' render_status embeds a
short skip note instead of images. Measured/estimate badges and DFM
pass/marginal/fail chips are unaffected by the render path.

Determinism: ``render_html`` reads ONLY the report dict — no clocks, no env, no
randomness. ``report_v1['generated_at_placeholder']`` is whatever the caller put
there (``emit_quality_report`` pins ``<GENERATED_AT>`` for diffable output); the
report-snapshot test relies on this to compare against a committed golden.
"""
from __future__ import annotations

import html as _html
from typing import Any


# ──────────────────────────────────────────────────────────────────────────────
# Inline styles. Two blocks: the always-present base stylesheet, and a render
# stylesheet appended ONLY when images are embedded so the no-image HTML never
# carries unused render rules (keeps the render_views=False output minimal +
# stable).

_CSS = """
:root{--ok:#1a7f37;--warn:#9a6700;--fail:#cf222e;--ink:#1f2328;
--muted:#656d76;--line:#d0d7de;--bg:#ffffff;--soft:#f6f8fa;--accent:#0969da;
--head:#0d1117}
*{box-sizing:border-box}
body{font:14px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
color:var(--ink);background:var(--bg);margin:0;padding:0}
.wrap{max-width:980px;margin:0 auto;padding:28px 24px 48px}
header.report{background:var(--head);color:#fff;margin:-28px -24px 20px;
padding:22px 24px 18px;border-bottom:3px solid var(--accent)}
header.report h1{font-size:23px;margin:0 0 6px;color:#fff;font-weight:650}
header.report .meta{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;
font-size:12px;color:#c9d1d9}
header.report .meta .k{color:#8b949e}
h2{font-size:16px;margin:26px 0 8px;border-bottom:1px solid var(--line);
padding-bottom:5px;color:var(--ink)}
h3{margin:16px 0 4px;font-size:14px;font-weight:600}
.sub{color:var(--muted);font-size:12px;margin:0 0 14px}
.units{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--muted);
background:var(--soft);border:1px solid var(--line);border-radius:8px;
padding:8px 12px;margin:0 0 6px}
.units b{color:var(--ink);font-weight:600}
.badge{display:inline-block;font-size:11px;font-weight:600;padding:1px 7px;
border-radius:10px;border:1px solid var(--line);vertical-align:middle}
.badge.measured{background:#ddf4ff;color:#0969da;border-color:#54aeff}
.badge.estimate{background:#fff8c5;color:#7d4e00;border-color:#eac54f}
.badge.mixed{background:#f3e8ff;color:#6e40c9;border-color:#c297ff}
.chip{display:inline-block;font-size:12px;font-weight:700;padding:2px 10px;
border-radius:12px;color:#fff;letter-spacing:.2px}
.chip.pass{background:var(--ok)}.chip.marginal{background:var(--warn)}
.chip.fail{background:var(--fail)}.chip.na,.chip.skipped{background:#8c959f}
table{border-collapse:collapse;width:100%;margin:6px 0 4px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);
font-size:13px}th{color:var(--muted);font-weight:600;background:var(--soft)}
.kpi{display:flex;flex-wrap:wrap;gap:12px;margin:10px 0 6px}
.kpi .card{border:1px solid var(--line);border-radius:8px;padding:10px 14px;
min-width:130px;background:var(--soft)}
.kpi .card .v{font-size:19px;font-weight:700;line-height:1.2}
.kpi .card .l{font-size:11px;color:var(--muted);margin-top:3px}
.note{color:var(--muted);font-size:12px;margin:2px 0}
.reasons{margin:4px 0 4px 4px;padding-left:18px}
.reasons li{font-size:12px;color:var(--ink);margin:1px 0}
.proc{border:1px solid var(--line);border-radius:8px;padding:10px 12px;
margin:8px 0;background:var(--bg)}
.proc .hdr{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.proc .hdr strong{font-size:13px}
.status-guarded,.status-error,.status-empty{color:var(--warn);font-size:12px;
margin:2px 0}
.foot{margin-top:30px;color:var(--muted);font-size:11px;line-height:1.6;
border-top:1px solid var(--line);padding-top:10px}
"""

#: Extra CSS for embedded render views — appended to ``_CSS`` ONLY when images
#: are present so the render_views=False HTML never carries unused rules.
_RENDER_CSS = """
.render{max-width:100%;height:auto;display:block;border:1px solid var(--line);
border-radius:6px;background:var(--soft)}
.render-fig{margin:8px 0;max-width:660px}
.render-fig figcaption{font-size:12px;color:var(--muted);margin-top:4px}
.render-grid{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start}
.render-grid .render-fig{max-width:420px;flex:1 1 320px}
"""


# ──────────────────────────────────────────────────────────────────────────────
# small formatting helpers


def _esc(v: Any) -> str:
    return _html.escape("" if v is None else str(v), quote=True)


def _grade_badge(grade: str) -> str:
    g = grade if grade in ("measured", "estimate", "mixed") else "measured"
    return f'<span class="badge {g}">{_esc(g)}</span>'


def _dfm_chip(verdict: str) -> str:
    cls = verdict if verdict in ("pass", "marginal", "fail") else "na"
    label = verdict if verdict not in ("skipped",) else "n/a"
    return f'<span class="chip {cls}">{_esc(label)}</span>'


def _fmt_val(v: Any) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, (list, dict)):
        return _esc(v)
    return _esc(v)


def _img_tag(b64: str, alt: str) -> str:
    """Inline base64 PNG <img> — keeps the HTML self-contained (data: URI, no
    external src). ``b64`` is plain base64 ascii (no data: prefix)."""
    return (
        f"<img alt='{_esc(alt)}' class='render' "
        f"src='data:image/png;base64,{b64}'>"
    )


def _units_line(units: dict[str, Any]) -> str:
    """A units + tolerance presentation strip. Tolerance display falls back to a
    sensible default note when the report carries no explicit tolerance — the
    report never silently implies a tighter tolerance than was measured."""
    pairs = [
        ("length", units.get("length", "mm")),
        ("angle", units.get("angle", "deg")),
        ("mass", units.get("mass", "g")),
        ("volume", units.get("volume", "mm^3")),
        ("density", units.get("density", "g/cm^3")),
    ]
    cells = "".join(
        f"<span><b>{_esc(label)}</b> {_esc(val)}</span>" for label, val in pairs
    )
    tol = units.get("tolerance_note") or (
        "values rounded to 4 dp; geometric tolerance not asserted "
        "(report is descriptive, not a certified inspection)"
    )
    return (
        "<div class='units'>"
        f"<span><b>units</b></span>{cells}"
        f"<span><b>tolerance</b> {_esc(tol)}</span>"
        "</div>"
    )


# ──────────────────────────────────────────────────────────────────────────────
# section renderers


def _render_header(report_v1: dict[str, Any], es: dict[str, Any]) -> list[str]:
    out: list[str] = []
    out.append("<header class='report'>")
    out.append(
        f"<h1>Quality Report &mdash; {_esc(report_v1['part_id'])}</h1>"
    )
    out.append(
        "<div class='meta'>"
        f"<span class='k'>schema</span> {_esc(report_v1['schema_version'])}"
        f"<span class='k'>generated</span> "
        f"{_esc(report_v1['generated_at_placeholder'])}"
        f"<span class='k'>overall grade</span> "
        f"{_grade_badge(es['overall_grade'])}"
        f"<span class='k'>DFM</span> {_dfm_chip(es['overall_dfm'])}"
        "</div>"
    )
    out.append("</header>")
    return out


def _render_exec_summary(
    es: dict[str, Any], images: dict[str, Any], render_status: Any,
) -> list[str]:
    out: list[str] = []
    out.append("<h2>Executive Summary</h2>")
    out.append(_units_line(es.get("_units", {})))
    out.append("<div class='kpi'>")
    if not es.get("key_metrics"):
        out.append(
            "<div class='card'><div class='v'>&mdash;</div>"
            "<div class='l'>no headline metrics available</div></div>"
        )
    for km in es.get("key_metrics", []):
        unit = f" {_esc(km['unit'])}" if km.get("unit") else ""
        out.append(
            "<div class='card'><div class='v'>"
            f"{_fmt_val(km['value'])}{unit}</div>"
            f"<div class='l'>{_esc(km['label'])} "
            f"{_grade_badge(km.get('grade', 'measured'))}</div></div>"
        )
    out.append("</div>")

    # Isometric render in the exec summary (when render_views produced one).
    if images.get("iso"):
        out.append(
            "<figure class='render-fig'>"
            + _img_tag(images["iso"], "isometric view")
            + "<figcaption>Isometric view "
            + _grade_badge("measured")
            + "</figcaption></figure>"
        )
    elif render_status and render_status != "ok":
        out.append(
            f"<div class='note'>3D render skipped: {_esc(render_status)}</div>"
        )
    return out


def _render_dfm(report_v1: dict[str, Any]) -> list[str]:
    out: list[str] = []
    out.append("<h2>Manufacturability (DFM)</h2>")
    procs = (report_v1.get("dfm") or {}).get("processes") or {}
    if not procs:
        out.append("<div class='note'>no processes evaluated</div>")
    for pname, proc in procs.items():
        gl = (
            " &middot; <span class='status-guarded'>estimate-limited</span>"
            if proc.get("grade_limited") else ""
        )
        out.append(
            "<div class='proc'><div class='hdr'>"
            f"<strong>{_esc(pname)}</strong> "
            f"{_dfm_chip(proc.get('verdict', 'skipped'))} "
            f"{_grade_badge(proc.get('grade', 'measured'))} "
            f"<span class='note'>{_esc(proc.get('spec_source', ''))}</span>"
            f"{gl}</div>"
        )
        reasons = proc.get("reasons") or []
        if reasons:
            out.append("<ul class='reasons'>")
            for r in reasons:
                out.append(f"<li>{_esc(r)}</li>")
            out.append("</ul>")
        checks = proc.get("checks") or []
        if checks:
            out.append(
                "<table><tr><th>check</th><th>verdict</th>"
                "<th>grade</th><th>measured</th><th>limit</th>"
                "<th>reason</th></tr>"
            )
            for c in checks:
                out.append(
                    "<tr>"
                    f"<td>{_esc(c.get('id'))}</td>"
                    f"<td>{_dfm_chip(c.get('verdict', 'skipped'))}</td>"
                    f"<td>{_grade_badge(c.get('grade', 'measured'))}</td>"
                    f"<td>{_fmt_val(c.get('measured'))}</td>"
                    f"<td>{_fmt_val(c.get('limit'))}</td>"
                    f"<td>{_esc(c.get('reason'))}</td></tr>"
                )
            out.append("</table>")
        out.append("</div>")
    return out


def _render_view_blocks(
    view_meta: list[dict[str, Any]], images: dict[str, Any],
) -> list[str]:
    out: list[str] = []
    render_blocks = [
        v for v in view_meta
        if v.get("kind") in ("section", "heatmap") and images.get(v.get("id"))
    ]
    if not render_blocks:
        return out
    out.append("<h2>Rendered Views</h2><div class='render-grid'>")
    for v in render_blocks:
        cap = _esc(v.get("title", v.get("id", "view")))
        extra = ""
        if v.get("kind") == "section" and v.get("area_mm2") is not None:
            extra = (
                f" &middot; section area {_fmt_val(v['area_mm2'])} mm&sup2; "
                f"&middot; {_fmt_val(v.get('edge_count'))} loops "
                + _grade_badge("measured")
            )
        elif v.get("kind") == "heatmap":
            extra = (
                f" &middot; {_esc(v.get('property', ''))} "
                + _grade_badge("estimate")
            )
        out.append(
            "<figure class='render-fig'>"
            + _img_tag(images[v["id"]], cap)
            + f"<figcaption>{cap}{extra}</figcaption></figure>"
        )
    out.append("</div>")
    return out


def _render_sections(report_v1: dict[str, Any]) -> list[str]:
    out: list[str] = []
    out.append("<h2>Inspection Sections</h2>")
    for sec in report_v1.get("sections", []):
        out.append(
            f"<h3>{_esc(sec['title'])} {_grade_badge(sec['grade'])}</h3>"
        )
        if sec["status"] != "ok":
            out.append(
                f"<div class='status-{_esc(sec['status'])}'>"
                f"status: {_esc(sec['status'])}</div>"
            )
        metrics = sec.get("metrics") or []
        if metrics:
            out.append("<table><tr><th>metric</th><th>value</th></tr>")
            for met in metrics:
                unit = f" {_esc(met['unit'])}" if met.get("unit") else ""
                out.append(
                    f"<tr><td>{_esc(met['label'])}</td>"
                    f"<td>{_fmt_val(met['value'])}{unit}</td></tr>"
                )
            out.append("</table>")
        for note in sec.get("notes") or []:
            out.append(f"<div class='note'>{_esc(note)}</div>")
    return out


# ──────────────────────────────────────────────────────────────────────────────
# public entry point


def render_html(
    report_v1: dict[str, Any],
    views: dict[str, Any] | None = None,
) -> str:
    """Production self-contained HTML for a QualityReportV1 dict.

    No external assets. ``views`` is the optional ``_report_render.build_views``
    output: when ``None`` (render_views=False) there are no images and no render
    CSS; when supplied with ``render_status='ok'`` the base64 PNGs embed inline
    as ``data:`` URIs (iso view in the exec summary, section/heatmap views in
    their own block). A non-'ok' render_status embeds a short skip note. The
    measured/estimate badges + DFM chips are render-path independent.
    """
    es = dict(report_v1["executive_summary"])
    # Thread the report-level units into the exec-summary renderer without
    # mutating the source dict (snapshot determinism).
    es["_units"] = report_v1.get("units", {})
    images = (views or {}).get("images") or {}
    view_meta = (views or {}).get("views") or []
    render_status = (views or {}).get("render_status")

    parts: list[str] = []
    parts.append("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    parts.append(
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    )
    parts.append(
        f"<title>Quality Report — {_esc(report_v1['part_id'])}</title>"
    )
    # Render CSS appended ONLY when images are embedded.
    style = _CSS + (_RENDER_CSS if images else "")
    parts.append(f"<style>{style}</style></head><body><div class='wrap'>")

    parts += _render_header(report_v1, es)
    parts += _render_exec_summary(es, images, render_status)
    parts += _render_dfm(report_v1)
    parts += _render_view_blocks(view_meta, images)
    parts += _render_sections(report_v1)

    render_note = (
        "Rendered views embedded inline (data: URIs — still self-contained)."
        if images else
        "No 3D render in this report (headless/CI-safe)."
    )
    parts.append(
        "<div class='foot'>Generated by emit_quality_report "
        f"({_esc(report_v1['schema_version'])}). Measured = direct geometric "
        "measurement; estimate = heuristic/closed-form approximation &mdash; an "
        "estimate-graded section never reports a confident manufacturability "
        f"pass. {render_note}</div>"
    )
    parts.append("</div></body></html>")
    return "".join(parts)
