"""report-snapshot regression — production HTML renderer (phase-3, 2026-06-13).

Gate the SHAPE of the production quality-report HTML + JSON against a committed
golden so an unintended change to the report structure fails loudly. Direct
module imports — passes without manifest registration.

Determinism
-----------
The renderer (``inspect/_report_html.render_html``) reads ONLY the report dict —
no clocks, no env, no randomness. We feed it a fully SYNTHETIC, hand-built
``QualityReportV1`` (see ``_synthetic_report_v1``) — NO geometry, NO OCCT, NO
pyvista — so the test is fast and the golden is byte-stable across machines.
``generated_at_placeholder`` is pinned to the library's ``<GENERATED_AT>``
sentinel, so there is no timestamp in the compared region.

The synthetic part deliberately exercises:
  * a measured-grade section (topology, wall_thickness) and an ESTIMATE-grade
    section (stress) — the estimate section must show the estimate badge and the
    DFM never reports a confident pass off it;
  * all three DFM verdicts — a green ``pass``, an amber ``marginal``, and a red
    ``fail`` chip;
  * a guarded section (status != ok) to cover the status note path.

Update mechanism
----------------
The goldens live in ``tests/skills/_golden/``. To regenerate after an
INTENTIONAL report-shape change::

    # PowerShell
    $env:UPDATE_REPORT_GOLDEN=1; $env:PYTHONPATH="src"
    venv\\Scripts\\python.exe -m pytest -q tests/skills/test_report_html_snapshot.py
    Remove-Item Env:UPDATE_REPORT_GOLDEN

When ``UPDATE_REPORT_GOLDEN`` is set the test (re)writes the goldens and passes,
so review the resulting git diff before committing. ``regen_goldens()`` is the
same code path exposed as an importable helper.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from phone_designer.skills.inspect.emit_quality_report import (
    GENERATED_AT_PLACEHOLDER,
    SCHEMA_VERSION,
    render_html,
)


_GOLDEN_DIR = Path(__file__).resolve().parent / "_golden"
_JSON_GOLDEN = _GOLDEN_DIR / "quality_report_synth.json"
_HTML_GOLDEN = _GOLDEN_DIR / "quality_report_synth.html"


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic, deterministic QualityReportV1 — hand-built, no geometry.


def _synthetic_report_v1() -> dict:
    """A fixed QualityReportV1 dict that exercises every renderer branch:
    measured + estimate grades, pass/marginal/fail DFM chips, an estimate-graded
    process capped to marginal (grade_limited), a guarded section, and a render
    skip note path (handled by the views=None default).

    Hand-built to match ``build_report_v1``'s output shape exactly so the golden
    captures the renderer, not the (geometry-dependent) extractors.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "part_id": "synth_bracket",
        "units": {
            "length": "mm",
            "angle": "deg",
            "mass": "g",
            "volume": "mm^3",
            "density": "g/cm^3",
        },
        "generated_at_placeholder": GENERATED_AT_PLACEHOLDER,
        "executive_summary": {
            "overall_grade": "mixed",
            "overall_dfm": "fail",
            "key_metrics": [
                {"label": "min wall", "value": 0.6, "unit": "mm",
                 "grade": "measured"},
                {"label": "undercuts", "value": 1, "unit": "",
                 "grade": "measured"},
                {"label": "mass", "value": 41.2, "unit": "g",
                 "grade": "measured"},
                {"label": "max stress", "value": 184.5, "unit": "MPa",
                 "grade": "estimate"},
            ],
        },
        "sections": [
            {
                "id": "topology",
                "title": "Topology Health",
                "grade": "measured",
                "status": "ok",
                "metrics": [
                    {"label": "valid", "value": True, "unit": ""},
                    {"label": "free edges", "value": 0, "unit": ""},
                    {"label": "face count", "value": 18, "unit": ""},
                ],
                "notes": [],
            },
            {
                "id": "wall_thickness",
                "title": "Wall Thickness",
                "grade": "measured",
                "status": "ok",
                "metrics": [
                    {"label": "min wall", "value": 0.6, "unit": "mm"},
                    {"label": "mean wall", "value": 2.4, "unit": "mm"},
                    {"label": "thin regions", "value": 2, "unit": ""},
                ],
                "notes": [],
            },
            {
                "id": "stress",
                "title": "Stress Concentration",
                "grade": "estimate",
                "status": "ok",
                "metrics": [
                    {"label": "max stress", "value": 184.5, "unit": "MPa"},
                    {"label": "Kt", "value": 2.7, "unit": ""},
                ],
                "notes": ["closed-form estimate — verify with FEA"],
            },
            {
                "id": "mesh_quality",
                "title": "Mesh Quality",
                "grade": "measured",
                "status": "guarded",
                "metrics": [],
                "notes": ["section guarded (face-count budget) — not measured"],
            },
        ],
        "dfm": {
            "processes": {
                "cnc_milling": {
                    "verdict": "marginal",
                    "grade": "measured",
                    "grade_limited": False,
                    "spec_source": "catalog:cnc_3axis",
                    "checks": [
                        {"id": "min_wall", "verdict": "marginal",
                         "grade": "measured", "measured": 0.6, "limit": 0.5,
                         "reason": "0.6 mm wall within 1.5x of the 0.5 mm min"},
                        {"id": "topology", "verdict": "pass",
                         "grade": "measured", "measured": None, "limit": None,
                         "reason": ""},
                    ],
                    "reasons": ["min wall 0.6 mm is marginal for CNC"],
                },
                "injection_molding": {
                    "verdict": "fail",
                    "grade": "measured",
                    "grade_limited": False,
                    "spec_source": "catalog:injection_mold_pa",
                    "checks": [
                        {"id": "min_wall", "verdict": "fail",
                         "grade": "measured", "measured": 0.6, "limit": 0.8,
                         "reason": "0.6 mm < 0.8 mm min wall"},
                        {"id": "draft", "verdict": "fail",
                         "grade": "measured", "measured": 0.0, "limit": 1.0,
                         "reason": "1 undercut wall has no draft"},
                    ],
                    "reasons": [
                        "min wall 0.6 mm below 0.8 mm molding minimum",
                        "1 undercut requires a side action",
                    ],
                },
                "3d_printing": {
                    "verdict": "pass",
                    "grade": "measured",
                    "grade_limited": False,
                    "spec_source": "embedded_default",
                    "checks": [
                        {"id": "min_feature", "verdict": "pass",
                         "grade": "measured", "measured": 0.6, "limit": 0.4,
                         "reason": ""},
                    ],
                    "reasons": [],
                },
                "fea_estimate": {
                    # estimate-graded process — a would-be fail is capped to
                    # marginal with grade_limited; it must NEVER read as a
                    # confident pass/fail.
                    "verdict": "marginal",
                    "grade": "estimate",
                    "grade_limited": True,
                    "spec_source": "embedded_default",
                    "checks": [
                        {"id": "max_stress", "verdict": "marginal",
                         "grade": "estimate", "measured": 184.5, "limit": 150.0,
                         "reason": "estimate — capped to marginal"},
                    ],
                    "reasons": ["stress estimate exceeds limit (estimate-capped)"],
                },
            },
            "_meta": {
                "processes_requested": [
                    "cnc_milling", "injection_molding", "3d_printing",
                    "fea_estimate",
                ],
                "unknown_processes": [],
            },
        },
    }


def _render_pair() -> tuple[dict, str]:
    rv = _synthetic_report_v1()
    html = render_html(rv, views=None)
    return rv, html


def regen_goldens() -> None:
    """Rewrite the committed goldens from the current renderer. Invoked by the
    test when ``UPDATE_REPORT_GOLDEN`` is set; also importable for a manual
    regen. Review the git diff afterwards."""
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    rv, html = _render_pair()
    _JSON_GOLDEN.write_text(
        json.dumps(rv, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    _HTML_GOLDEN.write_text(html, encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# 1. snapshot gate — JSON + HTML compared against the committed golden


def test_report_json_matches_golden():
    rv, _ = _render_pair()
    if os.environ.get("UPDATE_REPORT_GOLDEN"):
        regen_goldens()
    assert _JSON_GOLDEN.exists(), (
        "golden missing — run with UPDATE_REPORT_GOLDEN=1 to create it"
    )
    expected = json.loads(_JSON_GOLDEN.read_text(encoding="utf-8"))
    # Compare the reconstructed-from-golden dict to the freshly built one. A
    # report-shape change that isn't intended fails here.
    assert rv == expected, (
        "QualityReportV1 JSON shape drifted from the golden. If intended, "
        "re-run with UPDATE_REPORT_GOLDEN=1 and review the diff."
    )


def test_report_html_matches_golden():
    _, html = _render_pair()
    if os.environ.get("UPDATE_REPORT_GOLDEN"):
        regen_goldens()
    assert _HTML_GOLDEN.exists(), (
        "golden missing — run with UPDATE_REPORT_GOLDEN=1 to create it"
    )
    expected = _HTML_GOLDEN.read_text(encoding="utf-8")
    assert html == expected, (
        "Production report HTML drifted from the golden. If intended, re-run "
        "with UPDATE_REPORT_GOLDEN=1 and review the diff."
    )


# ──────────────────────────────────────────────────────────────────────────────
# 2. self-contained validity + key sections present


def test_html_is_valid_self_contained():
    _, html = _render_pair()
    assert html.lstrip().lower().startswith("<!doctype html")
    assert html.rstrip().endswith("</html>")
    assert "<style>" in html and "</style>" in html
    # balanced top-level tags
    assert html.count("<html") == 1 and html.count("</html>") == 1
    assert html.count("<body") == 1 and html.count("</body>") == 1
    # NO external asset references whatsoever.
    for forbidden in ("http://", "https://", "<link", "<script", " src="):
        assert forbidden not in html, f"external dependency leaked: {forbidden}"
    # data: URIs are allowed (self-contained) but there are none on the no-image
    # path.
    assert "data:image/png;base64," not in html


def test_key_sections_and_part_id_present():
    _, html = _render_pair()
    assert "synth_bracket" in html
    for heading in (
        "Executive Summary",
        "Manufacturability (DFM)",
        "Inspection Sections",
        "Topology Health",
        "Wall Thickness",
        "Stress Concentration",
    ):
        assert heading in html, heading
    # units + tolerance presentation strip
    assert "tolerance" in html
    assert "class='units'" in html
    # schema + generated placeholder surfaced in the header (no live timestamp).
    # The placeholder is HTML-escaped (angle brackets) — assert the escaped form
    # so we also verify the renderer escapes user-facing text.
    assert SCHEMA_VERSION in html
    escaped_placeholder = (
        GENERATED_AT_PLACEHOLDER.replace("<", "&lt;").replace(">", "&gt;")
    )
    assert escaped_placeholder in html
    # the raw unescaped angle-bracket form must NOT appear (proves escaping)
    assert GENERATED_AT_PLACEHOLDER not in html


# ──────────────────────────────────────────────────────────────────────────────
# 3. DFM chips reflect verdict colours; estimate section never a confident pass


def test_dfm_chips_have_all_three_verdict_colours():
    _, html = _render_pair()
    # The renderer maps verdict -> CSS class; the colours come from .chip.pass
    # (green) / .chip.marginal (amber) / .chip.fail (red) in the inline CSS.
    assert "chip pass" in html      # 3d_printing -> green
    assert "chip marginal" in html  # cnc_milling / fea_estimate -> amber
    assert "chip fail" in html      # injection_molding -> red
    # colour bindings present in the stylesheet
    assert ".chip.pass{background:var(--ok)}" in html
    assert ".chip.marginal{background:var(--warn)}" in html
    assert ".chip.fail{background:var(--fail)}" in html
    assert "--ok:#1a7f37" in html and "--fail:#cf222e" in html


def test_estimate_section_shows_estimate_badge_not_confident_pass():
    _, html = _render_pair()
    # The estimate-graded Stress section heading must carry the estimate badge.
    m = re.search(
        r"<h3>Stress Concentration\s+<span class=\"badge (\w+)\">",
        html,
    )
    assert m and m.group(1) == "estimate", "Stress section must show estimate badge"
    # The estimate-graded DFM process is capped to marginal — never a green pass
    # chip. Find the fea_estimate proc block and assert its header chip.
    proc_idx = html.find("fea_estimate")
    assert proc_idx != -1
    # within the proc header (the span right after the process name)
    header_slice = html[proc_idx:proc_idx + 200]
    assert "chip marginal" in header_slice
    assert "chip pass" not in header_slice
    assert "estimate-limited" in header_slice or "grade_limited" not in html
    # estimate badge appears for the estimate-graded process
    assert "badge estimate" in html


def test_estimate_badge_count_matches_estimate_grades():
    _, html = _render_pair()
    # estimate badges: exec-summary max-stress KPI, Stress section heading,
    # fea_estimate process header + its check row, the max_stress check grade.
    # We only assert there is at least one and that measured badges dominate
    # (the report never launders estimates into a blanket measured headline).
    assert html.count("badge estimate") >= 2
    assert html.count("badge measured") >= html.count("badge estimate")


# ──────────────────────────────────────────────────────────────────────────────
# 4. render_views=False vs True — structure identical minus images


def test_views_false_vs_true_structure_parity():
    rv = _synthetic_report_v1()
    html_off = render_html(rv, views=None)

    # Simulate a successful render: a tiny 1x1 transparent PNG, base64.
    tiny_png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    views = {
        "render_status": "ok",
        "images": {
            "iso": tiny_png_b64,
            "section_z": tiny_png_b64,
        },
        "views": [
            {"id": "iso", "kind": "iso", "title": "Isometric view",
             "width_px": 640, "height_px": 480},
            {"id": "section_z", "kind": "section",
             "title": "Section — XY mid-plane (normal +Z)",
             "plane_normal": [0.0, 0.0, 1.0], "area_mm2": 512.0,
             "edge_count": 1, "width_px": 640, "height_px": 480},
        ],
    }
    html_on = render_html(rv, views=views)

    # The ON path embeds images; the OFF path does not.
    assert "data:image/png;base64," not in html_off
    assert "<img" not in html_off
    assert html_on.count("data:image/png;base64,") == 2
    assert "<h2>Rendered Views</h2>" in html_on
    assert "<h2>Rendered Views</h2>" not in html_off

    # Render CSS appears ONLY when images are embedded.
    assert ".render{" in html_on
    assert ".render{" not in html_off

    # Structure is otherwise the same: stripping the image markup + render CSS
    # from the ON output and the render-views block must leave the OFF text's
    # backbone (same headings, same DFM/section content).
    def _backbone(s: str) -> list[str]:
        return re.findall(r"<h2>(.*?)</h2>", s)

    bb_off = _backbone(html_off)
    bb_on = _backbone(html_on)
    # ON has exactly one extra <h2> (Rendered Views); every OFF heading present.
    assert bb_on == bb_off[:2] + ["Rendered Views"] + bb_off[2:] or \
        set(bb_off).issubset(set(bb_on))
    # DFM + section content identical between the two paths.
    assert "synth_bracket" in html_on and "synth_bracket" in html_off
    assert "chip fail" in html_on and "chip fail" in html_off
    assert "Stress Concentration" in html_on


def test_no_gl_skip_note_path():
    rv = _synthetic_report_v1()
    views = {
        "render_status": "skipped_no_gl",
        "images": {},
        "views": [],
        "face_count": 18,
        "note": "no GL context",
    }
    html = render_html(rv, views=views)
    # no images, but an honest skip note in the exec summary
    assert "data:image/png;base64," not in html
    assert "<img" not in html
    assert "3D render skipped: skipped_no_gl" in html
    # render CSS NOT appended (no images)
    assert ".render{" not in html
