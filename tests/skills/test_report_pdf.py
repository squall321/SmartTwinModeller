"""report PDF / print-ready deliverable — phase-4, 2026-06-15.

The HONEST PDF deliverable for emit_quality_report is a print-optimized
self-contained HTML (``@media print`` page breaks / margins / print-safe
colours): weasyprint and reportlab are NOT installed in this env (heavy native
Cairo/Pango / low-fidelity respectively), so the dependency-free path is open
the HTML in any browser and "Save as PDF".

This module gates THREE things:

  1. ``print_ready=True`` HTML carries the ``@media print`` rules + page-break
     hints and stays self-contained + valid (no external assets).
  2. the DEFAULT path is byte-identical to the non-print render — the print CSS
     lives entirely inside the print media query, so screen output (and the
     report-snapshot golden) are unchanged.
  3. the reportlab true-.pdf path is exercised ONLY when reportlab is importable
     (``pytest.mark.skipif`` otherwise); when absent we assert the descriptor is
     HONEST (pdf_available=False, the print-HTML fallback + browser hint).

Direct module imports — passes without manifest registration. Reuses the
synthetic QualityReportV1 from the report-snapshot test so there is no geometry
/ OCCT / pyvista dependency here.
"""
from __future__ import annotations

import importlib.util

import pytest

from phone_designer.skills.inspect._report_html import _PRINT_CSS, render_html
from phone_designer.skills.inspect._report_pdf import (
    SAVE_AS_PDF_HINT,
    build_pdf_deliverable,
    pdf_capabilities,
)
from tests.skills.test_report_html_snapshot import _synthetic_report_v1

_HAS_REPORTLAB = importlib.util.find_spec("reportlab") is not None


# ──────────────────────────────────────────────────────────────────────────────
# 1. print_ready HTML has the @media print rules + page-break hints


def test_print_ready_html_has_media_print_and_page_breaks():
    rv = _synthetic_report_v1()
    html = render_html(rv, views=None, print_ready=True)

    # the @media print block is present
    assert "@media print{" in html
    assert "@page{" in html
    # page-break hints (modern + legacy property names, for cross-browser save)
    assert "page-break-inside:avoid" in html
    assert "break-inside:avoid" in html
    assert "page-break-before:always" in html
    assert "break-before:page" in html
    # print-safe colours so the dark header + verdict chips keep their hue
    assert "print-color-adjust:exact" in html
    # the whole print stylesheet appears verbatim
    assert _PRINT_CSS in html


def test_print_ready_html_is_still_self_contained_and_valid():
    rv = _synthetic_report_v1()
    html = render_html(rv, views=None, print_ready=True)

    assert html.lstrip().lower().startswith("<!doctype html")
    assert html.rstrip().endswith("</html>")
    assert "<style>" in html and "</style>" in html
    assert html.count("<html") == 1 and html.count("</html>") == 1
    assert html.count("<body") == 1 and html.count("</body>") == 1
    # NO external assets even on the print path.
    for forbidden in ("http://", "https://", "<link", "<script", " src="):
        assert forbidden not in html, f"external dependency leaked: {forbidden}"
    # no images on the no-view path
    assert "data:image/png;base64," not in html


# ──────────────────────────────────────────────────────────────────────────────
# 2. default path unchanged — print CSS lives only inside @media print


def test_default_render_unchanged_by_print_ready_feature():
    rv = _synthetic_report_v1()
    default_html = render_html(rv, views=None)
    # the default (print_ready=False) output must NOT carry any print CSS
    assert "@media print" not in default_html
    assert _PRINT_CSS not in default_html


def test_print_ready_is_superset_of_default_screen_markup():
    """The print path only ADDS a media-scoped stylesheet — the document body
    (everything after </style>) is identical to the default render."""
    rv = _synthetic_report_v1()
    default_html = render_html(rv, views=None)
    print_html = render_html(rv, views=None, print_ready=True)

    assert print_html != default_html  # they differ (print CSS added)
    # body after the closing </style> is byte-identical
    default_body = default_html.split("</style>", 1)[1]
    print_body = print_html.split("</style>", 1)[1]
    assert print_body == default_body
    # and the print HTML's style block is exactly the default's style + print CSS
    default_style = default_html.split("<style>", 1)[1].split("</style>", 1)[0]
    print_style = print_html.split("<style>", 1)[1].split("</style>", 1)[0]
    assert print_style == default_style + _PRINT_CSS


# ──────────────────────────────────────────────────────────────────────────────
# 3. build_pdf_deliverable — honest capabilities descriptor


def test_pdf_capabilities_is_honest():
    caps = pdf_capabilities()
    assert caps["print_html"] is True  # always available, dependency-free
    assert caps["reportlab"] is _HAS_REPORTLAB
    assert caps["engine"] == ("reportlab" if _HAS_REPORTLAB else None)
    assert isinstance(caps["note"], str) and caps["note"]


def test_build_pdf_deliverable_always_returns_print_html():
    rv = _synthetic_report_v1()
    out = build_pdf_deliverable(rv, views=None)

    # print-ready HTML is ALWAYS present and is the same as render_html(...)
    assert out["print_html"] == render_html(rv, views=None, print_ready=True)
    assert "@media print{" in out["print_html"]
    # honest browser hint always provided
    assert out["save_as_pdf_hint"] == SAVE_AS_PDF_HINT
    assert "Save as PDF" in out["save_as_pdf_hint"]
    assert isinstance(out["note"], str) and out["note"]
    # pdf_available is honest about the engine
    assert out["pdf_available"] is _HAS_REPORTLAB
    assert out["format"] == ("pdf" if _HAS_REPORTLAB else "html")


def test_prefer_html_forces_html_only_even_if_engine_present():
    rv = _synthetic_report_v1()
    out = build_pdf_deliverable(rv, views=None, prefer="html")
    assert out["pdf_bytes"] is None
    assert out["pdf_available"] is False
    assert out["format"] == "html"
    assert "@media print{" in out["print_html"]


@pytest.mark.skipif(
    _HAS_REPORTLAB,
    reason="reportlab IS installed — the no-engine honesty path is N/A here",
)
def test_no_engine_reports_pdf_unavailable_with_fallback():
    """The honest finding in THIS env: no PDF engine -> pdf_available False,
    pdf_bytes None, and the print-HTML + browser hint as the deliverable."""
    rv = _synthetic_report_v1()
    out = build_pdf_deliverable(rv, views=None)
    assert out["pdf_available"] is False
    assert out["pdf_bytes"] is None
    assert out["pdf_engine"] is None
    assert out["format"] == "html"
    # the note must point at the print-HTML / Save-as-PDF fallback, not lie
    assert "print" in out["note"].lower() or "pdf" in out["note"].lower()
    assert "@media print{" in out["print_html"]


@pytest.mark.skipif(
    not _HAS_REPORTLAB,
    reason="reportlab not installed — true-.pdf path unavailable (HONEST: the "
           "print-optimized HTML is the deliverable). Skipping the pdf_bytes "
           "test rather than faking a PDF capability.",
)
def test_reportlab_pdf_bytes_when_available():
    rv = _synthetic_report_v1()
    out = build_pdf_deliverable(rv, views=None)
    assert out["pdf_available"] is True
    assert out["pdf_engine"] == "reportlab"
    assert isinstance(out["pdf_bytes"], bytes)
    # a real PDF starts with the %PDF- magic
    assert out["pdf_bytes"][:5] == b"%PDF-"
    assert out["format"] == "pdf"
    # print-HTML is STILL produced (the high-fidelity deliverable)
    assert "@media print{" in out["print_html"]


# ──────────────────────────────────────────────────────────────────────────────
# 4. emit_quality_report Arg wiring — additive, default OFF


def test_emit_quality_report_print_ready_arg_default_off():
    """Default extras carry NO print/PDF keys (additive contract). Uses the
    synthetic report through build_report_v1-shaped data — but the skill needs a
    body, so we test the Arg default at the schema level + the render path."""
    from phone_designer.skills.inspect.emit_quality_report import EmitQualityReport

    # the Arg exists, defaults False
    fields = EmitQualityReport.Args.model_fields
    assert "print_ready" in fields
    assert fields["print_ready"].default is False
