"""_report_pdf — print-ready / PDF deliverable layer for emit_quality_report.

Pillar report (phase-4, 2026-06-15). HONEST about the dependency reality.

The dependency spike (run on this Windows + OCP env, 2026-06-15)
---------------------------------------------------------------
A true rasterized ``.pdf`` from HTML needs a heavy native engine that is **not**
installed here and not trivial to add:

  * ``weasyprint``  — NOT installed; pulls native Cairo/Pango/GObject — a heavy,
    fragile native install on Windows. We did NOT add it.
  * ``reportlab``   — NOT installed; pure-Python but lower fidelity (it cannot
    consume our self-contained HTML/CSS — it would need a hand-built layout).
  * ``xhtml2pdf`` / ``pdfkit`` / ``fpdf`` — NOT installed either.

So the HONEST, dependency-free PDF deliverable shipped here is a
**print-optimized self-contained HTML**: the existing ``render_html`` output
plus a ``@media print`` stylesheet (page margins, page-break hints, fixed
print-safe colours via ``print-color-adjust:exact``, hidden chrome). Opened in
any browser it prints / "Save as PDF" cleanly to an A4/Letter PDF — ZERO new
deps, works on every platform. THIS is the deliverable.

If — and only if — ``reportlab`` is ever installed, ``to_pdf_bytes`` also offers
a true ``.pdf`` (low-fidelity: title + executive-summary text + DFM verdict
lines + embedded PNG views from ``_report_render`` when present). It is strictly
opt-in and never required. When reportlab is absent we report
``pdf_available=False`` and point at the print-HTML path — we do NOT claim a PDF
capability we don't have.

Public API
----------
``pdf_capabilities() -> dict``
    {"reportlab": bool, "engine": "reportlab" | None,
     "print_html": True, "note": str}  — what this env can actually do.

``build_pdf_deliverable(report_v1, views=None, *, prefer="auto") -> dict``
    The single entry point ``emit_quality_report`` calls when ``print_ready`` is
    requested. Always returns the print-ready HTML; adds ``pdf_bytes`` ONLY when
    a real engine is present. Shape::

        {
          "format": "html" | "pdf",          # what the *primary* deliverable is
          "print_html": "<...self-contained, @media-print-enhanced HTML...>",
          "pdf_available": bool,              # honest: True only with reportlab
          "pdf_engine": "reportlab" | None,
          "pdf_bytes": bytes | None,          # present only when pdf_available
          "save_as_pdf_hint": "<one-line instruction for the browser path>",
          "note": "<honest description of what was produced + why>",
        }
"""
from __future__ import annotations

import importlib.util
from typing import Any

from phone_designer.skills.inspect._report_html import render_html

#: One-line, user-facing instruction for the always-available browser path.
SAVE_AS_PDF_HINT = (
    "Open this HTML in any browser and choose Print -> 'Save as PDF' "
    "(or Ctrl+P -> destination 'Save as PDF'); the @media print stylesheet "
    "applies page margins, page breaks and print-safe colours automatically."
)


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def pdf_capabilities() -> dict[str, Any]:
    """What this environment can ACTUALLY produce — no aspiration, no lying.

    ``print_html`` is always True (dependency-free). ``reportlab`` reflects a
    live import probe; ``weasyprint`` is reported too so the caller can be
    explicit that the high-fidelity engine is absent.
    """
    has_reportlab = _has("reportlab")
    has_weasyprint = _has("weasyprint")
    if has_reportlab:
        note = (
            "reportlab present — an opt-in low-fidelity .pdf is available "
            "(text + embedded PNG views). The print-optimized HTML remains the "
            "high-fidelity deliverable."
        )
    else:
        note = (
            "No PDF engine installed (weasyprint/reportlab absent). The PDF "
            "deliverable is the print-optimized self-contained HTML — print / "
            "'Save as PDF' from any browser. No native deps required."
        )
    return {
        "reportlab": has_reportlab,
        "weasyprint": has_weasyprint,
        "engine": "reportlab" if has_reportlab else None,
        "print_html": True,
        "note": note,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Optional reportlab path — imported lazily, NEVER at module import time, so a
# missing reportlab can never break importing this module or the report skill.


def _reportlab_pdf_bytes(report_v1: dict[str, Any], views: dict | None) -> bytes:
    """Low-fidelity true-PDF rendering via reportlab. Text + DFM verdict lines +
    embedded PNG views (when ``views`` carries base64 images). Imported lazily;
    callers MUST gate on ``pdf_capabilities()['reportlab']`` first."""
    import base64
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    left = 18 * mm
    y = height - 20 * mm

    def line(text: str, *, size: int = 10, dy: float = 5.2 * mm,
             font: str = "Helvetica") -> None:
        nonlocal y
        if y < 22 * mm:
            c.showPage()
            y = height - 20 * mm
        c.setFont(font, size)
        c.drawString(left, y, text[:110])
        y -= dy

    es = report_v1.get("executive_summary", {})
    line(f"Quality Report - {report_v1.get('part_id', 'part')}",
         size=16, dy=8 * mm, font="Helvetica-Bold")
    line(f"schema: {report_v1.get('schema_version', '')}   "
         f"overall grade: {es.get('overall_grade', '')}   "
         f"DFM: {es.get('overall_dfm', '')}", size=9)
    y -= 2 * mm

    line("Executive Summary", size=12, font="Helvetica-Bold")
    for km in es.get("key_metrics", []):
        unit = f" {km.get('unit')}" if km.get("unit") else ""
        line(f"  - {km.get('label')}: {km.get('value')}{unit} "
             f"[{km.get('grade', 'measured')}]", size=9)
    y -= 2 * mm

    line("Manufacturability (DFM)", size=12, font="Helvetica-Bold")
    procs = (report_v1.get("dfm") or {}).get("processes") or {}
    for pname, proc in procs.items():
        line(f"  - {pname}: {proc.get('verdict', 'n/a')} "
             f"[{proc.get('grade', 'measured')}]"
             + ("  (estimate-limited)" if proc.get("grade_limited") else ""),
             size=9)
    y -= 2 * mm

    line("Inspection Sections", size=12, font="Helvetica-Bold")
    for sec in report_v1.get("sections", []):
        line(f"  - {sec.get('title')} [{sec.get('grade')}] "
             f"({sec.get('status')})", size=9)

    # Embedded PNG views (iso + sections) when a successful render is present.
    images = (views or {}).get("images") or {}
    for vid in ("iso",) + tuple(k for k in images if k != "iso"):
        b64 = images.get(vid)
        if not b64:
            continue
        try:
            img = ImageReader(io.BytesIO(base64.b64decode(b64)))
        except Exception:
            continue
        c.showPage()
        y = height - 20 * mm
        c.setFont("Helvetica-Bold", 11)
        c.drawString(left, y, f"View: {vid}")
        c.drawImage(
            img, left, 30 * mm, width=width - 2 * left,
            preserveAspectRatio=True, anchor="n", mask="auto",
        )

    c.showPage()
    c.save()
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point


def build_pdf_deliverable(
    report_v1: dict[str, Any],
    views: dict[str, Any] | None = None,
    *,
    prefer: str = "auto",
) -> dict[str, Any]:
    """Build the print-ready deliverable for ``emit_quality_report``.

    ALWAYS returns the print-optimized self-contained HTML (``print_html``).
    Adds a true ``pdf_bytes`` ONLY when reportlab is importable AND
    ``prefer != "html"``. We never fabricate a PDF — ``pdf_available`` is the
    honest source of truth.

    ``prefer``: ``"auto"`` (default) → use reportlab if present; ``"html"`` →
    force the HTML-only deliverable even if reportlab is present;
    ``"pdf"`` → request the reportlab path (still degrades to HTML honestly when
    reportlab is absent).
    """
    caps = pdf_capabilities()
    print_html = render_html(report_v1, views, print_ready=True)

    want_pdf = prefer in ("auto", "pdf") and caps["reportlab"]
    pdf_bytes: bytes | None = None
    pdf_engine: str | None = None
    if want_pdf:
        try:
            pdf_bytes = _reportlab_pdf_bytes(report_v1, views)
            pdf_engine = "reportlab"
        except Exception as exc:  # a real engine misbehaving must not break us
            pdf_bytes = None
            pdf_engine = None
            caps_note = (
                f"reportlab present but PDF render failed "
                f"({type(exc).__name__}: {exc}); falling back to print-HTML."
            )
            caps = {**caps, "note": caps_note}

    pdf_available = pdf_bytes is not None
    return {
        "format": "pdf" if pdf_available else "html",
        "print_html": print_html,
        "pdf_available": pdf_available,
        "pdf_engine": pdf_engine,
        "pdf_bytes": pdf_bytes,
        "save_as_pdf_hint": SAVE_AS_PDF_HINT,
        "note": caps["note"],
    }
