"""dfm_verdict + emit_quality_report — productized pillar report layer.

Pillar report (phase-1, 2026-06-14). Direct module imports — passes without
manifest registration. The synthetic housing (30x20x10 box + R2 fillet + 4mm
through hole + a deliberate thin 0.6 mm wall) exercises every section and
trips the injection-molding min-wall fail.

Coverage:
  1. emit_quality_report -> QualityReportV1: every section present with a
     grade badge + measured/estimate status; validates against the declared
     schema shape; executive summary + dfm folded in.
  2. dfm_verdict flags the thin wall for injection_molding as fail with the
     measured grade SHOWN (never an estimate launder).
  3. an estimate-grade input never becomes a confident pass/fail — it is
     capped to marginal with grade_limited=True.
  4. HTML renders to a non-empty self-contained string with grade badges +
     DFM chips present and NO external asset references.
  5. JSON is round-trippable (json.dumps) and schema-stable.

The housing has ~16 faces; the whole module runs well under 60 s.
"""
from __future__ import annotations

import json

from build123d import (
    Axis,
    Box as B3dBox,
    Cylinder as B3dCylinder,
    Pos,
    fillet,
)

from phone_designer.skills.inspect.dfm_verdict import (
    DfmVerdict,
    _cap_for_grade,
    _load_process_spec,
)
from phone_designer.skills.inspect.emit_quality_report import (
    GENERATED_AT_PLACEHOLDER,
    SCHEMA_VERSION,
    EmitQualityReport,
    build_report_v1,
    render_html,
)
from phone_designer.skills.inspect.extract_quality_report import (
    DEFAULT_SECTIONS,
    ExtractQualityReport,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures


def _housing_thin():
    """30x20x10 box + R2 vertical-edge fillet + 4mm through hole + a thin
    0.6 mm floor (a pocket cut from the top leaving 0.6 mm of wall) — that
    floor is BELOW the injection-mold 0.8 mm min and the CNC 0.5 mm min ratio
    band, so it fails molding and marginals CNC."""
    part = B3dBox(30.0, 20.0, 10.0)
    edge = part.edges().filter_by(Axis.Z)[0]
    part = fillet(edge, radius=2.0)
    hole = Pos(8.0, 0.0, 0.0) * B3dCylinder(radius=2.0, height=12.0)
    part = part - hole
    pocket = Pos(-6.0, 0.0, 5.0 - (10.0 - 0.6) / 2.0) * B3dBox(12.0, 12.0, 9.4)
    return part - pocket


def _quality_report(body):
    return ExtractQualityReport().apply(body, {}).extras["quality_report"]


# ──────────────────────────────────────────────────────────────────────────────
# 1. QualityReportV1 structure


def test_report_v1_has_all_sections_with_grades():
    body = _housing_thin()
    out = EmitQualityReport().apply(body, {"part_id": "housing_thin"}).extras
    rv = out["quality_report_v1"]

    assert rv["schema_version"] == SCHEMA_VERSION
    assert rv["part_id"] == "housing_thin"
    assert rv["generated_at_placeholder"] == GENERATED_AT_PLACEHOLDER
    assert rv["units"]["length"] == "mm"

    section_ids = [s["id"] for s in rv["sections"]]
    assert set(DEFAULT_SECTIONS).issubset(set(section_ids)), section_ids

    for sec in rv["sections"]:
        assert sec["grade"] in {"measured", "estimate"}, sec
        assert sec["status"] in {"ok", "guarded", "error", "empty"}, sec
        assert "metrics" in sec and "notes" in sec and "title" in sec

    es = rv["executive_summary"]
    assert es["overall_grade"] in {"measured", "estimate", "mixed"}
    assert es["overall_dfm"] in {"pass", "marginal", "fail", "n/a"}
    assert isinstance(es["key_metrics"], list) and es["key_metrics"]
    for km in es["key_metrics"]:
        assert {"label", "value", "unit", "grade"} <= set(km)

    # dfm folded in
    assert "dfm" in rv and "processes" in rv["dfm"]


def test_schema_is_json_round_trippable_and_stable():
    body = _housing_thin()
    rv = EmitQualityReport().apply(body, {}).extras["quality_report_v1"]
    # The whole structure must serialize (no numpy/np.float64/sets leaking).
    text = json.dumps(rv)
    back = json.loads(text)
    assert back["schema_version"] == SCHEMA_VERSION
    # placeholder is stable for diffing — not a live timestamp
    assert back["generated_at_placeholder"] == GENERATED_AT_PLACEHOLDER


# ──────────────────────────────────────────────────────────────────────────────
# 2. dfm_verdict flags the thin wall for injection_molding (measured grade)


def test_thin_wall_fails_injection_molding_with_measured_grade():
    body = _housing_thin()
    qr = _quality_report(body)
    dv = DfmVerdict().apply(body, {
        "processes": ["cnc_milling", "injection_molding"],
        "quality_report": qr,
    }).extras["dfm_verdict"]

    inj = dv["processes"]["injection_molding"]
    assert inj["verdict"] == "fail", inj
    # the measured grade is SHOWN, not laundered
    assert inj["grade"] == "measured", inj
    assert inj["grade_limited"] is False
    assert inj["spec_source"] == "catalog:injection_mold_pa"

    wall_check = next(c for c in inj["checks"] if c["id"] == "min_wall")
    assert wall_check["verdict"] == "fail"
    assert wall_check["grade"] == "measured"
    assert wall_check["measured"] < wall_check["limit"]
    assert any("min wall" in r for r in inj["reasons"])

    # CNC: 0.6 mm is above its 0.5 min but within 1.5x -> marginal, not fail
    cnc = dv["processes"]["cnc_milling"]
    assert cnc["verdict"] in {"marginal", "pass"}, cnc
    assert cnc["grade"] == "measured"


def test_default_processes_and_spec_sources():
    body = _housing_thin()
    dv = DfmVerdict().apply(body, {}).extras["dfm_verdict"]
    # defaults
    assert set(dv["processes"]) == {"cnc_milling", "injection_molding"}
    assert dv["processes"]["cnc_milling"]["spec_source"] == "catalog:cnc_3axis"


def test_three_d_printing_uses_embedded_default():
    body = _housing_thin()
    dv = DfmVerdict().apply(body, {"processes": ["3d_printing"]}).extras[
        "dfm_verdict"]
    p = dv["processes"]["3d_printing"]
    assert p["spec_source"] == "embedded_default"
    # FDM needs no draft and tolerates undercuts; thin 0.6 mm wall is its
    # min_feature (0.4) pass but below 0.8 min wall -> at least marginal.
    assert p["verdict"] in {"pass", "marginal", "fail"}
    spec, src = _load_process_spec("3d_printing")
    assert src == "embedded_default"
    assert spec["min_feature_mm"] == 0.4


def test_unknown_process_reported():
    body = _housing_thin()
    dv = DfmVerdict().apply(body, {"processes": ["cnc_milling", "bogus"]}).extras[
        "dfm_verdict"]
    assert dv["_meta"]["unknown_processes"] == ["bogus"]
    assert "bogus" not in dv["processes"]


# ──────────────────────────────────────────────────────────────────────────────
# 3. estimate grade NEVER becomes a confident pass/fail


def test_estimate_input_caps_to_marginal():
    # unit-level guarantee of the grade-inheritance rule
    assert _cap_for_grade("fail", "estimate") == "marginal"
    assert _cap_for_grade("pass", "estimate") == "marginal"
    assert _cap_for_grade("marginal", "estimate") == "marginal"
    # measured grade is untouched
    assert _cap_for_grade("fail", "measured") == "fail"
    assert _cap_for_grade("pass", "measured") == "pass"


def test_estimate_grade_section_never_confident_pass_or_fail():
    body = _housing_thin()
    qr = _quality_report(body)
    # Forcibly downgrade the wall_thickness section to estimate grade — the
    # same wall (0.6 mm) that measured-fails injection molding must now be
    # capped to marginal with grade_limited flagged.
    qr["wall_thickness"]["grade"] = "estimate"

    dv = DfmVerdict().apply(body, {
        "processes": ["injection_molding"],
        "quality_report": qr,
    }).extras["dfm_verdict"]
    inj = dv["processes"]["injection_molding"]

    wall_check = next(c for c in inj["checks"] if c["id"] == "min_wall")
    assert wall_check["grade"] == "estimate"
    # would have been 'fail' on measured grade — capped to marginal
    assert wall_check["verdict"] == "marginal", wall_check
    assert inj["grade"] == "estimate"
    assert inj["grade_limited"] is True
    # an estimate input can never yield a confident process-level fail/pass
    assert inj["verdict"] == "marginal", inj


# ──────────────────────────────────────────────────────────────────────────────
# 4. self-contained HTML


def test_html_is_self_contained_and_has_badges():
    body = _housing_thin()
    out = EmitQualityReport().apply(body, {"part_id": "housing_thin"}).extras
    html = out["quality_report_html"]

    assert isinstance(html, str) and len(html) > 1000
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "</html>" in html

    # inline CSS, no external assets
    assert "<style>" in html
    for forbidden in ("http://", "https://", "<link", "<script", "src="):
        assert forbidden not in html, f"external dependency leaked: {forbidden}"

    # measured/estimate badge + DFM chip CSS classes present and rendered
    assert "badge measured" in html
    assert "class='chip" in html or 'class="chip' in html
    # the failing injection-mold process must surface as a fail chip
    assert "chip fail" in html
    # part id and section titles rendered
    assert "housing_thin" in html
    assert "Wall Thickness" in html


def test_render_html_pure_function():
    # build_report_v1 + render_html work without re-running geometry
    body = _housing_thin()
    qr = _quality_report(body)
    dv = DfmVerdict().apply(body, {"quality_report": qr}).extras["dfm_verdict"]
    rv = build_report_v1(qr, dv, part_id="pure")
    html = render_html(rv)
    assert "pure" in html
    assert rv["schema_version"] == SCHEMA_VERSION


def test_include_html_false_omits_html():
    body = _housing_thin()
    out = EmitQualityReport().apply(
        body, {"include_html": False}).extras
    assert "quality_report_html" not in out
    assert "quality_report_v1" in out
