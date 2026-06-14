"""emit_quality_report — atomic, read-only. Pillar report (phase-1, 2026-06-14).

Productized quality-report emitter: take an ``extract_quality_report`` dict
(or run it), fold in the ``dfm_verdict``, and emit a single, versioned
``QualityReportV1`` structure plus a self-contained HTML string.

The JSON is the stable, diffable contract (schema_version pinned). The HTML
is a dependency-free, headless/CI-safe rendering with inline CSS only — NO
external assets, NO 3D render (Phase 2 adds section PNGs). It carries
measured/estimate badges per section and DFM pass/marginal/fail chips per
process, so an analyst can open the file in any browser offline.

QualityReportV1 structure::

    {
      "schema_version": "QualityReportV1",
      "part_id": str,
      "units": {"length": "mm", "angle": "deg", "mass": "g", ...},
      "generated_at_placeholder": "<GENERATED_AT>",   # stable for diffing;
                                                        # caller stamps a time
      "executive_summary": {
        "overall_grade": "measured" | "estimate" | "mixed",
        "overall_dfm": "pass" | "marginal" | "fail" | "n/a",
        "key_metrics": [{"label", "value", "unit", "grade"}, ...],
      },
      "sections": [
        {"id", "title", "grade", "status", "metrics": [...], "notes": [...]},
        ...
      ],
      "dfm": {... the dfm_verdict payload ...},
    }

Why the executive summary is "mixed": if the sections that actually carry
data span both measured and estimate grades the overall grade is reported as
``mixed`` — the report never launders an estimate into a blanket measured
headline.

Phase-1 note on the report-write skill (d:/report-skill): that skill is a
server-backed ReportArchive publisher (requires ``report-skill ping`` to a
live backend + network, and targets an org board / personal workspace). It
is the wrong tool for a dependency-free, offline, CI/headless CAD quality
report. We therefore keep the self-contained HTML here. (If org publishing is
later wanted, this QualityReportV1 JSON is a clean source for a report-write
draft.)

Read-only — body unchanged (post ``body_present``).
"""
from __future__ import annotations

import html as _html
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


SCHEMA_VERSION = "QualityReportV1"
GENERATED_AT_PLACEHOLDER = "<GENERATED_AT>"


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _round(v: Any, n: int = 4) -> Any:
    return round(v, n) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


# ──────────────────────────────────────────────────────────────────────────────
# Section extractors — quality_report section -> compact display metrics.
# Each returns (status, metrics) where status is 'ok'|'guarded'|'error'|'empty'.


def _section_envelope(report: dict, name: str) -> tuple[Any, str, str]:
    """(data, grade, status) for a quality_report section."""
    sec = report.get(name)
    if not isinstance(sec, dict):
        return None, "measured", "empty"
    grade = sec.get("grade", "measured")
    if sec.get("error"):
        return sec.get("data"), grade, "error"
    if sec.get("guarded"):
        return sec.get("data"), grade, "guarded"
    data = sec.get("data")
    if data is None:
        return None, grade, "empty"
    if isinstance(data, dict) and (data.get("guarded") or data.get("skipped")):
        return data, grade, "guarded"
    return data, grade, "ok"


def _m(label: str, value: Any, unit: str = "") -> dict[str, Any]:
    return {"label": label, "value": _round(value), "unit": unit}


def _metrics_for(name: str, data: Any) -> list[dict[str, Any]]:
    """Compact, display-oriented metrics per known section. Unknown sections
    fall through to a single 'raw' note via the caller."""
    if not isinstance(data, dict) and not isinstance(data, list):
        return []
    if name == "topology":
        return [
            _m("valid", data.get("is_valid")),
            _m("free edges", data.get("free_edge_count")),
            _m("non-manifold edges", data.get("non_manifold_edge_count")),
            _m("sliver faces", len(data.get("sliver_faces") or [])),
            _m("self-intersecting", data.get("self_intersecting")),
            _m("face count", data.get("face_count")),
        ]
    if name == "wall_thickness":
        return [
            _m("min wall", data.get("global_min"), "mm"),
            _m("mean wall", data.get("mean"), "mm"),
            _m("max wall", data.get("global_max"), "mm"),
            _m("thin threshold", data.get("thin_threshold_mm"), "mm"),
            _m("thin regions", len(data.get("thin_regions") or [])),
        ]
    if name == "draft":
        counts = data.get("counts") or {}
        return [
            _m("min draft target", data.get("min_draft_deg"), "deg"),
            _m("walls ok", counts.get("ok")),
            _m("below min draft", counts.get("below_min_draft")),
            _m("undercuts", counts.get("undercut")),
        ]
    if name == "edge_blends":
        blends = data if isinstance(data, list) else []
        fillets = [b for b in blends if b.get("kind") == "fillet"]
        chamfers = [b for b in blends if b.get("kind") == "chamfer"]
        radii = [b["radius_mm"] for b in fillets
                 if isinstance(b.get("radius_mm"), (int, float))]
        out = [_m("fillets", len(fillets)), _m("chamfers", len(chamfers))]
        if radii:
            out.append(_m("min fillet R", min(radii), "mm"))
            out.append(_m("max fillet R", max(radii), "mm"))
        return out
    if name == "mass_properties":
        return [
            _m("volume", data.get("volume_mm3"), "mm^3"),
            _m("mass", data.get("mass_g"), "g"),
            _m("density", data.get("density_g_per_cm3"), "g/cm^3"),
            _m("centroid", data.get("centroid")),
        ]
    if name == "principal_axes":
        axes = data.get("principal_axes")
        return [_m("principal axes", len(axes) if isinstance(axes, list) else None)]
    if name == "mesh_quality":
        return [
            _m("triangles", data.get("triangle_count")),
            _m("vertices", data.get("vertex_count")),
            _m("degenerate tris", data.get("degenerate_triangle_count")),
            _m("min edge", data.get("min_edge_length_mm"), "mm"),
            _m("max edge", data.get("max_edge_length_mm"), "mm"),
        ]
    if name == "gdt_summary":
        return [
            _m("dimensions", data.get("dimensions")),
            _m("datums", len(data.get("datums") or [])
               if isinstance(data.get("datums"), list) else data.get("datums")),
            _m("FCFs", data.get("fcfs")),
            _m("textures", data.get("textures")),
            _m("welds", data.get("welds")),
        ]
    return []


_SECTION_TITLES = {
    "topology": "Topology Health",
    "wall_thickness": "Wall Thickness",
    "draft": "Draft Angles",
    "edge_blends": "Edge Blends",
    "mass_properties": "Mass Properties",
    "principal_axes": "Principal Axes",
    "mesh_quality": "Mesh Quality",
    "gdt_summary": "GD&T / PMI Summary",
}

#: Section order for the report — quality_report section order is preserved
#: but driven from this list so the JSON is deterministic.
_SECTION_ORDER = (
    "topology", "wall_thickness", "draft", "edge_blends", "mass_properties",
    "principal_axes", "mesh_quality", "gdt_summary",
)


# ──────────────────────────────────────────────────────────────────────────────
# QualityReportV1 build


def _build_sections(report: dict) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    present = [n for n in _SECTION_ORDER if n in report]
    # Keep any non-standard sections too (forward-compatible).
    present += [n for n in report
                if not n.startswith("_") and n not in present]
    for name in present:
        data, grade, status = _section_envelope(report, name)
        metrics = _metrics_for(name, data) if status == "ok" else []
        notes: list[str] = []
        if status == "guarded":
            notes.append("section guarded (face-count budget) — not measured")
        elif status == "error":
            err = report.get(name, {}).get("error")
            notes.append(f"section error: {err}")
        elif status == "empty":
            notes.append("section unavailable")
        sections.append({
            "id": name,
            "title": _SECTION_TITLES.get(name, name.replace("_", " ").title()),
            "grade": grade,
            "status": status,
            "metrics": metrics,
            "notes": notes,
        })
    return sections


def _executive_summary(
    sections: list[dict[str, Any]], dfm: dict[str, Any],
) -> dict[str, Any]:
    # Overall grade across sections that actually carry data.
    grades = {s["grade"] for s in sections if s["status"] == "ok"}
    if not grades:
        overall_grade = "measured"
    elif grades == {"estimate"}:
        overall_grade = "estimate"
    elif "estimate" in grades:
        overall_grade = "mixed"
    else:
        overall_grade = "measured"

    # Overall DFM = worst process verdict.
    sev = {"pass": 0, "marginal": 1, "fail": 2, "skipped": -1}
    overall_dfm = "n/a"
    worst = -2
    for proc in (dfm.get("processes") or {}).values():
        v = proc.get("verdict", "skipped")
        if sev.get(v, -1) > worst:
            worst = sev.get(v, -1)
            overall_dfm = v
    if overall_dfm == "skipped":
        overall_dfm = "n/a"

    # Key metrics — a curated handful pulled from the section metrics.
    key: list[dict[str, Any]] = []
    by_id = {s["id"]: s for s in sections}

    def _pull(section_id: str, label: str, target_unit: str | None = None):
        s = by_id.get(section_id)
        if not s or s["status"] != "ok":
            return
        for met in s["metrics"]:
            if met["label"] == label:
                key.append({
                    "label": label, "value": met["value"],
                    "unit": met.get("unit", ""), "grade": s["grade"],
                })
                return

    _pull("wall_thickness", "min wall")
    _pull("draft", "undercuts")
    _pull("mass_properties", "mass")
    _pull("mass_properties", "volume")
    _pull("topology", "valid")
    _pull("edge_blends", "min fillet R")

    return {
        "overall_grade": overall_grade,
        "overall_dfm": overall_dfm,
        "key_metrics": key,
    }


def build_report_v1(
    quality_report: dict[str, Any],
    dfm_verdict: dict[str, Any],
    *,
    part_id: str,
) -> dict[str, Any]:
    """Pure function — assemble the versioned QualityReportV1 dict. Exposed
    for direct testing without going through the skill wrapper."""
    sections = _build_sections(quality_report)
    summary = _executive_summary(sections, dfm_verdict)
    return {
        "schema_version": SCHEMA_VERSION,
        "part_id": part_id,
        "units": {
            "length": "mm", "angle": "deg", "mass": "g",
            "volume": "mm^3", "density": "g/cm^3",
        },
        "generated_at_placeholder": GENERATED_AT_PLACEHOLDER,
        "executive_summary": summary,
        "sections": sections,
        "dfm": dfm_verdict,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Self-contained HTML rendering (inline CSS, no external deps)

_CSS = """
:root{--ok:#1a7f37;--warn:#9a6700;--fail:#cf222e;--ink:#1f2328;
--muted:#656d76;--line:#d0d7de;--bg:#ffffff;--soft:#f6f8fa}
*{box-sizing:border-box}
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
color:var(--ink);background:var(--bg);margin:0;padding:24px}
h1{font-size:22px;margin:0 0 2px}h2{font-size:16px;margin:24px 0 8px;
border-bottom:1px solid var(--line);padding-bottom:4px}
.sub{color:var(--muted);font-size:12px;margin-bottom:16px}
.badge{display:inline-block;font-size:11px;font-weight:600;padding:1px 7px;
border-radius:10px;border:1px solid var(--line);vertical-align:middle}
.badge.measured{background:#ddf4ff;color:#0969da;border-color:#54aeff}
.badge.estimate{background:#fff8c5;color:#7d4e00;border-color:#eac54f}
.badge.mixed{background:#f3e8ff;color:#6e40c9;border-color:#c297ff}
.chip{display:inline-block;font-size:12px;font-weight:700;padding:2px 10px;
border-radius:12px;color:#fff}
.chip.pass{background:var(--ok)}.chip.marginal{background:var(--warn)}
.chip.fail{background:var(--fail)}.chip.na,.chip.skipped{background:#8c959f}
table{border-collapse:collapse;width:100%;margin:6px 0 4px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);
font-size:13px}th{color:var(--muted);font-weight:600;background:var(--soft)}
.kpi{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 4px}
.kpi .card{border:1px solid var(--line);border-radius:8px;padding:8px 12px;
min-width:120px;background:var(--soft)}
.kpi .card .v{font-size:18px;font-weight:700}
.kpi .card .l{font-size:11px;color:var(--muted)}
.note{color:var(--muted);font-size:12px;margin:2px 0}
.reasons li{font-size:12px;color:var(--ink)}
.proc{border:1px solid var(--line);border-radius:8px;padding:10px 12px;
margin:8px 0;background:var(--bg)}
.status-guarded,.status-error,.status-empty{color:var(--warn);font-size:12px}
.foot{margin-top:28px;color:var(--muted);font-size:11px;
border-top:1px solid var(--line);padding-top:8px}
"""

#: Extra CSS for embedded render views — appended to ``_CSS`` ONLY when images
#: are present (phase-2). Kept out of the default style block so the
#: render_views=False HTML is byte-identical to Phase-1.
_RENDER_CSS = """
.render{max-width:100%;height:auto;display:block;border:1px solid var(--line);
border-radius:6px;background:var(--soft)}
.render-fig{margin:8px 0;max-width:660px}
.render-fig figcaption{font-size:12px;color:var(--muted);margin-top:4px}
.render-grid{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start}
.render-grid .render-fig{max-width:420px;flex:1 1 320px}
"""


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


def render_html(
    report_v1: dict[str, Any],
    views: dict[str, Any] | None = None,
) -> str:
    """Self-contained HTML for a QualityReportV1 dict. No external assets.

    ``views`` is the optional ``_report_render.build_views`` output. When
    ``None`` (the Phase-1 default) the HTML is byte-identical to Phase-1 — no
    images, no extra markup, no perf cost. When supplied with
    ``render_status='ok'`` the base64 PNGs are embedded inline (iso view in the
    exec summary, section / heatmap views in their own block) as ``data:`` URIs,
    so the report stays a single dependency-free file. Measured/estimate badges
    are untouched. A non-'ok' render_status embeds a short skip note instead.
    """
    es = report_v1["executive_summary"]
    images = (views or {}).get("images") or {}
    view_meta = (views or {}).get("views") or []
    render_status = (views or {}).get("render_status")
    parts: list[str] = []
    parts.append("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    parts.append("<meta name='viewport' content='width=device-width,"
                 "initial-scale=1'>")
    parts.append(f"<title>Quality Report — {_esc(report_v1['part_id'])}"
                 "</title>")
    # Extra render CSS is appended ONLY when views are embedded, so the
    # default (render_views=False) HTML stays byte-identical to Phase-1.
    style = _CSS + (_RENDER_CSS if images else "")
    parts.append(f"<style>{style}</style></head><body>")

    # Header
    parts.append(f"<h1>Quality Report &mdash; {_esc(report_v1['part_id'])}"
                 "</h1>")
    parts.append(
        f"<div class='sub'>schema {_esc(report_v1['schema_version'])} "
        f"&middot; generated {_esc(report_v1['generated_at_placeholder'])} "
        f"&middot; overall grade {_grade_badge(es['overall_grade'])} "
        f"&middot; DFM {_dfm_chip(es['overall_dfm'])}</div>"
    )

    # Executive summary KPI cards
    parts.append("<h2>Executive Summary</h2><div class='kpi'>")
    for km in es.get("key_metrics", []):
        unit = f" {_esc(km['unit'])}" if km.get("unit") else ""
        parts.append(
            "<div class='card'><div class='v'>"
            f"{_fmt_val(km['value'])}{unit}</div>"
            f"<div class='l'>{_esc(km['label'])} "
            f"{_grade_badge(km.get('grade', 'measured'))}</div></div>"
        )
    parts.append("</div>")

    # Isometric render in the exec summary (when render_views produced one).
    if images.get("iso"):
        parts.append(
            "<figure class='render-fig'>"
            + _img_tag(images["iso"], "isometric view")
            + "<figcaption>Isometric view "
            + _grade_badge("measured")
            + "</figcaption></figure>"
        )
    elif render_status and render_status != "ok":
        parts.append(
            f"<div class='note'>3D render skipped: {_esc(render_status)}</div>"
        )

    # DFM verdicts
    parts.append("<h2>Manufacturability (DFM)</h2>")
    procs = (report_v1.get("dfm") or {}).get("processes") or {}
    if not procs:
        parts.append("<div class='note'>no processes evaluated</div>")
    for pname, proc in procs.items():
        gl = " &middot; <span class='status-guarded'>estimate-limited</span>" \
            if proc.get("grade_limited") else ""
        parts.append(
            "<div class='proc'><div>"
            f"<strong>{_esc(pname)}</strong> "
            f"{_dfm_chip(proc.get('verdict', 'skipped'))} "
            f"{_grade_badge(proc.get('grade', 'measured'))} "
            f"<span class='note'>{_esc(proc.get('spec_source', ''))}</span>"
            f"{gl}</div>"
        )
        reasons = proc.get("reasons") or []
        if reasons:
            parts.append("<ul class='reasons'>")
            for r in reasons:
                parts.append(f"<li>{_esc(r)}</li>")
            parts.append("</ul>")
        # per-check table
        checks = proc.get("checks") or []
        if checks:
            parts.append("<table><tr><th>check</th><th>verdict</th>"
                         "<th>grade</th><th>measured</th><th>limit</th>"
                         "<th>reason</th></tr>")
            for c in checks:
                parts.append(
                    "<tr>"
                    f"<td>{_esc(c.get('id'))}</td>"
                    f"<td>{_dfm_chip(c.get('verdict', 'skipped'))}</td>"
                    f"<td>{_grade_badge(c.get('grade', 'measured'))}</td>"
                    f"<td>{_fmt_val(c.get('measured'))}</td>"
                    f"<td>{_fmt_val(c.get('limit'))}</td>"
                    f"<td>{_esc(c.get('reason'))}</td></tr>"
                )
            parts.append("</table>")
        parts.append("</div>")

    # Rendered section / heatmap views (when render_views produced any).
    render_blocks = [
        v for v in view_meta
        if v.get("kind") in ("section", "heatmap") and images.get(v.get("id"))
    ]
    if render_blocks:
        parts.append("<h2>Rendered Views</h2><div class='render-grid'>")
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
                extra = f" &middot; {_esc(v.get('property', ''))} " + _grade_badge("estimate")
            parts.append(
                "<figure class='render-fig'>"
                + _img_tag(images[v["id"]], cap)
                + f"<figcaption>{cap}{extra}</figcaption></figure>"
            )
        parts.append("</div>")

    # Sections
    parts.append("<h2>Inspection Sections</h2>")
    for sec in report_v1.get("sections", []):
        parts.append(
            f"<h3 style='margin:14px 0 4px;font-size:14px'>{_esc(sec['title'])} "
            f"{_grade_badge(sec['grade'])}</h3>"
        )
        if sec["status"] != "ok":
            parts.append(
                f"<div class='status-{_esc(sec['status'])}'>"
                f"status: {_esc(sec['status'])}</div>"
            )
        metrics = sec.get("metrics") or []
        if metrics:
            parts.append("<table><tr><th>metric</th><th>value</th></tr>")
            for met in metrics:
                unit = f" {_esc(met['unit'])}" if met.get("unit") else ""
                parts.append(
                    f"<tr><td>{_esc(met['label'])}</td>"
                    f"<td>{_fmt_val(met['value'])}{unit}</td></tr>"
                )
            parts.append("</table>")
        for note in sec.get("notes") or []:
            parts.append(f"<div class='note'>{_esc(note)}</div>")

    render_note = (
        "Rendered views embedded inline (data: URIs — still self-contained)."
        if images else
        "No 3D render in this phase (headless/CI-safe)."
    )
    parts.append(
        "<div class='foot'>Generated by emit_quality_report "
        f"({_esc(report_v1['schema_version'])}). Measured = direct geometric "
        "measurement; estimate = heuristic/closed-form approximation. "
        f"{render_note}</div>"
    )
    parts.append("</body></html>")
    return "".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="emit_quality_report",
    category="inspect",
    level="atomic",
    summary="Productized quality-report emitter: take an "
            "extract_quality_report dict (or run it), fold in dfm_verdict, "
            "and produce a versioned QualityReportV1 structure "
            "(executive_summary + graded sections + DFM) as JSON, plus a "
            "self-contained dependency-free HTML string (inline CSS, "
            "measured/estimate badges, DFM pass/marginal/fail chips, "
            "per-section tables). No 3D render this phase — headless/CI-safe. "
            "Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["quality_report_v1"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.6,
    post_conditions=[PostCondition(kind="body_present")],
)
class EmitQualityReport(SkillBase):
    class Args(BaseModel):
        part_id: str = Field(
            default="part",
            description="Identifier stamped into the report header / part_id.",
        )
        processes: list[str] = Field(
            default_factory=lambda: ["cnc_milling", "injection_molding"],
            description="Processes for the embedded dfm_verdict.",
        )
        pull_direction: list[float] = Field(
            default=[0.0, 0.0, 1.0], min_length=3, max_length=3,
            description="Mold opening / tool approach direction for the draft "
                        "+ DFM analysis.",
        )
        quality_report: dict | None = Field(
            default=None,
            description="Pre-computed extract_quality_report dict. None -> "
                        "run extract_quality_report on the body.",
        )
        include_html: bool = Field(
            default=True,
            description="Emit the self-contained HTML string alongside JSON.",
        )
        render_views: bool = Field(
            default=False,
            description="Render headless iso + cross-section PNG views and embed "
                        "them inline (base64 data: URIs) in the HTML; JSON gains "
                        "a 'views' metadata block + render_status. Default OFF so "
                        "existing tests + headless CI stay byte-identical and fast. "
                        "Degrades to render_status='skipped_no_gl' with no images "
                        "when there is no GL context (never crashes).",
        )
        max_section_views: int = Field(
            default=3, ge=0, le=3,
            description="Cap on rendered mid-plane cross-section views.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("emit_quality_report: body is None")

        from phone_designer.skills.inspect.dfm_verdict import DfmVerdict

        report = args.quality_report
        if not isinstance(report, dict):
            from phone_designer.skills.inspect.extract_quality_report import (
                ExtractQualityReport,
            )
            report = ExtractQualityReport().apply(
                body,
                {"pull_direction": [float(c) for c in args.pull_direction]},
            ).extras["quality_report"]

        dfm = DfmVerdict().apply(body, {
            "processes": list(args.processes),
            "pull_direction": [float(c) for c in args.pull_direction],
            "quality_report": report,
        }).extras["dfm_verdict"]

        report_v1 = build_report_v1(report, dfm, part_id=args.part_id)

        # Optional headless render — additive. When OFF, the HTML render path is
        # called with views=None → byte-identical to Phase-1 and no perf cost.
        views: dict[str, Any] | None = None
        if args.render_views:
            from phone_designer.skills.inspect._report_render import build_views

            try:
                views = build_views(
                    body, max_section_views=int(args.max_section_views),
                )
            except Exception as exc:  # render must never break the report
                views = {
                    "render_status": "skipped_no_gl",
                    "images": {},
                    "views": [],
                    "face_count": 0,
                    "note": f"render error: {type(exc).__name__}: {exc}",
                }
            # JSON carries metadata only — NEVER the base64 bytes.
            report_v1["views"] = {
                "render_status": views.get("render_status"),
                "views": views.get("views", []),
                "face_count": views.get("face_count", 0),
                "note": views.get("note", ""),
            }
            report_v1["render_status"] = views.get("render_status")

        extras: dict[str, Any] = {"quality_report_v1": report_v1}
        if args.include_html:
            extras["quality_report_html"] = render_html(report_v1, views)

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
