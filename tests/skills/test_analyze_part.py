"""analyze_part — single-part front-door macro (2026-06-18).

Builds a tiny synthetic STEP (a box + a hole) on the fly so the test needs no
corpus, then asserts the one-call deliverable carries every stage: quality
report (+ HTML), feature catalog counts, editable key dimensions, and — with
reconstruct=True — a box-mode reconstruction scored by Hausdorff. Each stage is
failure-isolated, so the test also pins that a missing file degrades to an
honest stub rather than raising.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from phone_designer.skills.reverse_engineer.analyze_part import AnalyzePart


def _synth_step(tmp: Path) -> str:
    """A 20x16x8 box with a Ø4 through hole — enough to exercise holes +
    topology + mass + key dimensions."""
    from build123d import Box, Cylinder
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    # algebra mode — version-robust (no context managers)
    part = Box(20, 16, 8) - Cylinder(2.0, 8.1)
    shape = part.wrapped
    out = str(tmp / "synth_part.step")
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    w.Write(out)
    return out


def test_analyze_part_full_deliverable():
    with tempfile.TemporaryDirectory() as d:
        path = _synth_step(Path(d))
        res = AnalyzePart().apply(None, {
            "part_path": path,
            "include_html": True,
            "reconstruct": True,
        })
        pa = res.extras["part_analysis"]

        # every stage ran and succeeded
        for stage in ("import", "quality_report", "feature_catalog",
                      "key_dimensions", "reconstruction"):
            assert stage in pa["_stages"], f"missing stage {stage}"
            assert pa["_stages"][stage]["ok"], (stage, pa["_stages"][stage])

        # quality report + self-contained HTML
        assert isinstance(pa["report"], dict)
        assert isinstance(pa["report_html"], str) and len(pa["report_html"]) > 500
        assert "<html" in pa["report_html"].lower()

        # feature catalog inventory — the box+hole gives >=1 hole
        counts = pa["feature_catalog"]["counts"]
        assert counts["holes"] >= 1

        # editable key dimensions present + named + valued
        kd = pa["key_dimensions"]
        assert isinstance(kd, list) and len(kd) >= 1
        assert all("name" in d and "value_mm" in d and "dotted_key" in d for d in kd)
        # the overall envelope should be among the drivers
        names = {d["name"] for d in kd}
        assert any("housing" in n or "length" in n or "width" in n or "height" in n
                   for n in names), names

        # reconstruction scored by geometry (Hausdorff is the anti-fake gate)
        rec = pa["reconstruction"]
        assert isinstance(rec, dict)
        assert rec["plan_steps"] >= 1
        assert rec["base_mechanism"]
        assert rec["hausdorff_mm"] is not None

        # bbox recovered
        assert isinstance(pa["bbox_mm"], list) and len(pa["bbox_mm"]) == 6


def test_analyze_part_missing_file_degrades_honestly():
    res = AnalyzePart().apply(None, {"part_path": "does/not/exist.step"})
    pa = res.extras["part_analysis"]
    # honest stub, not an exception
    assert pa["report"] is None
    assert pa["feature_catalog"] is None
    assert pa["_stages"]["import"]["ok"] is False


def test_analyze_part_args_reject_unknown_key():
    with pytest.raises(Exception):
        AnalyzePart().apply(None, {"part_path": "x.step", "bogus_arg": 1})


def test_analyze_part_no_reconstruct_skips_the_expensive_stage():
    with tempfile.TemporaryDirectory() as d:
        path = _synth_step(Path(d))
        res = AnalyzePart().apply(None, {"part_path": path, "reconstruct": False})
        pa = res.extras["part_analysis"]
        assert pa["reconstruction"] is None
        assert "reconstruction" not in pa["_stages"]
        # the cheap stages still ran
        assert pa["_stages"]["feature_catalog"]["ok"]


def test_analyze_part_emit_script_includes_editable_model():
    with tempfile.TemporaryDirectory() as d:
        path = _synth_step(Path(d))
        res = AnalyzePart().apply(None, {"part_path": path, "emit_script": True})
        ps = res.extras["part_analysis"]["parametric_script"]
        assert ps and ps.get("ok") and "from build123d import" in ps.get("script", "")
        assert res.extras["part_analysis"]["_stages"]["parametric_script"]["ok"]


def test_analyze_part_emit_script_off_by_default():
    with tempfile.TemporaryDirectory() as d:
        path = _synth_step(Path(d))
        res = AnalyzePart().apply(None, {"part_path": path})
        assert res.extras["part_analysis"]["parametric_script"] is None
        assert "parametric_script" not in res.extras["part_analysis"]["_stages"]


def test_analyze_part_estimate_cost_in_report():
    with tempfile.TemporaryDirectory() as d:
        path = _synth_step(Path(d))
        res = AnalyzePart().apply(None, {
            "part_path": path, "estimate_cost": True,
            "cost_process": "cnc_3axis", "cost_material": "aluminum"})
        ce = res.extras["part_analysis"]["cost_estimate"]
        assert ce and ce["grade"] == "estimate"
        assert ce["unit_cost_usd"] > 0 and ce["cycle_time_s"] > 0
        assert res.extras["part_analysis"]["_stages"]["estimate_cost"]["ok"]


def test_analyze_part_recognize_fits_in_report():
    with tempfile.TemporaryDirectory() as d:
        path = _synth_step(Path(d))
        res = AnalyzePart().apply(None, {
            "part_path": path, "recognize_fits": True, "fit_role": "press"})
        fa = res.extras["part_analysis"]["fit_analysis"]
        assert fa and fa["standard"] == "ISO 286" and fa["grade"] == "estimate"
        assert fa["role"] == "press"
        assert res.extras["part_analysis"]["_stages"]["recognize_fits"]["ok"]


def test_analyze_part_cost_and_fits_off_by_default():
    with tempfile.TemporaryDirectory() as d:
        path = _synth_step(Path(d))
        pa = AnalyzePart().apply(None, {"part_path": path}).extras["part_analysis"]
        assert pa["cost_estimate"] is None and pa["fit_analysis"] is None
        assert pa["sheet_metal"] is None
        assert "estimate_cost" not in pa["_stages"]
        assert "recognize_fits" not in pa["_stages"]
        assert "sheet_metal" not in pa["_stages"]


def test_analyze_part_sheet_metal_stage_runs():
    # the box+hole synth is NOT sheet metal, but the stage must run and report
    with tempfile.TemporaryDirectory() as d:
        path = _synth_step(Path(d))
        pa = AnalyzePart().apply(None, {
            "part_path": path, "sheet_metal": True}).extras["part_analysis"]
        sm = pa["sheet_metal"]
        assert sm and sm["grade"] == "estimate"
        assert sm["is_sheet_metal"] is False  # a thick box, not a sheet
        assert pa["_stages"]["sheet_metal"]["ok"]
