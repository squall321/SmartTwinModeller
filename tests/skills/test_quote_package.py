"""quote_package — the one-call RFQ bundle (Phase-1 track 1-5).

Corpus-independent: runs once on a generated 40x30x20 solid box (module-scope
fixture — the macro runs recommend_process, so it is priced once and every
assertion reads the same result). Verified contract:

  * out_dir/quote_package.zip exists and contains part.step + section.dxf +
    manifest.json;
  * manifest lists ALL sections (part / costs / process_recommendation /
    quality_summary / dxf / artifacts);
  * EVERY lot size has a cost row and per-unit cost is NON-INCREASING as the
    lot grows (unit = base + T/L — monotonicity sanity);
  * every cost artifact is labeled grade='estimate' INSIDE manifest.json (the
    cost rows, the recommendation block, and the cost_model header);
  * the quality summary SAYS which headless method was chosen
    (mass_properties + key_dimensions, not emit_quality_report) and its
    volume matches the box;
  * the DXF entry exists (centroid section, >=1 polyline);
  * a body with no volume (an open face) is REFUSED with fm.no_solid_body;
  * Args typos are refused with fm.invalid_args;
  * extras is strict-JSON-safe (json.dumps allow_nan=False).
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest


def _register_all():
    from phone_designer.corpus.regress import _force_register_all
    _force_register_all()


@pytest.fixture(scope="module")
def box_body():
    _register_all()
    from phone_designer.skills.create.box import Box
    return Box().apply(None, {"length_mm": 40.0, "width_mm": 30.0,
                              "height_mm": 20.0}).body


@pytest.fixture(scope="module")
def quote(box_body, tmp_path_factory):
    """Run the macro ONCE (it prices + recommends) and share the result."""
    from phone_designer.skills.inspect.quote_package import QuotePackage

    out_dir = tmp_path_factory.mktemp("quote_pkg")
    res = QuotePackage().apply(box_body, {"out_dir": str(out_dir)})
    return res, out_dir


# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.slow
def test_zip_and_manifest_sections_exist(quote):
    res, out_dir = quote
    zip_path = Path(res.extras["zip_path"])
    assert zip_path.is_file()
    assert zip_path == out_dir / "quote_package.zip"

    manifest = res.extras["manifest"]
    # manifest completeness IS the contract.
    for section in ("part", "costs", "process_recommendation",
                    "quality_summary", "dxf", "artifacts", "cost_model"):
        assert section in manifest, f"manifest missing section '{section}'"
    assert manifest["kind"] == "quote_package"
    assert manifest["part"]["step_path"] == "part.step"
    assert manifest["part"]["file_size_bytes"] > 0
    assert manifest["part"]["n_solids"] == 1

    # the manifest on disk == the manifest in extras (same bytes source).
    on_disk = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk == manifest


@pytest.mark.slow
def test_every_lot_size_has_a_cost(quote):
    res, _ = quote
    manifest = res.extras["manifest"]
    lots = manifest["lot_sizes"]
    assert lots == [1, 100, 1000]  # the defaults, sorted
    for proc in manifest["processes_costed"]:
        for lot in lots:
            row = [c for c in manifest["costs"]
                   if c["process"] == proc and c["lot_size"] == lot]
            assert len(row) == 1, f"no cost row for {proc}@{lot}"
            assert isinstance(row[0]["unit_cost_usd"], (int, float))
            assert row[0]["unit_cost_usd"] > 0


@pytest.mark.slow
def test_per_unit_cost_non_increasing_with_lot(quote):
    res, _ = quote
    manifest = res.extras["manifest"]
    for proc in manifest["processes_costed"]:
        rows = sorted((c for c in manifest["costs"] if c["process"] == proc),
                      key=lambda c: c["lot_size"])
        costs = [r["unit_cost_usd"] for r in rows]
        assert all(costs[i + 1] <= costs[i] + 1e-9
                   for i in range(len(costs) - 1)), (proc, costs)
        # amortisation is REAL on this model: lot 1 strictly dearer than 1000.
        assert costs[0] > costs[-1]


@pytest.mark.slow
def test_grade_estimate_labeled_inside_manifest(quote):
    res, _ = quote
    manifest = res.extras["manifest"]
    # every cost row.
    assert manifest["costs"], "no cost rows at all"
    for c in manifest["costs"]:
        assert c["grade"] == "estimate", c
    # the recommendation block and the cost-model header.
    assert manifest["process_recommendation"]["grade"] == "estimate"
    assert manifest["cost_model"]["grade"] == "estimate"
    # excluded-process reasons ride along (a box is not sheet metal, so the
    # sheet candidates are excluded with a reason — when all candidates ran).
    rec = manifest["process_recommendation"]
    assert "excluded" in rec and "recommendation" in rec
    for e in rec["excluded"]:
        assert e.get("reason"), e


@pytest.mark.slow
def test_quality_summary_says_method_and_measures(quote):
    res, _ = quote
    qs = res.extras["manifest"]["quality_summary"]
    assert qs["method"] == "mass_properties+key_dimensions"
    assert "emit_quality_report" in qs["method_note"]  # says what was NOT run
    assert qs["grade"] == "measured"
    # 40 x 30 x 20 box -> 24000 mm3.
    assert qs["mass_properties"]["volume_mm3"] == pytest.approx(24000.0, rel=1e-6)
    names = [k["name"] for k in qs["key_dimensions"]]
    assert "housing_length" in names


@pytest.mark.slow
def test_dxf_entry_exists_and_zip_contents(quote):
    res, out_dir = quote
    manifest = res.extras["manifest"]
    dxf = manifest["dxf"]
    assert dxf["path"] == "section.dxf"
    assert dxf["source"] == "section"
    assert dxf["n_polylines"] >= 1
    assert dxf["error"] is None
    # the section plane passes through the measured centroid.
    assert dxf["plane_origin"] == pytest.approx(
        res.extras["manifest"]["quality_summary"]["mass_properties"]["centroid"])

    with zipfile.ZipFile(out_dir / "quote_package.zip") as zf:
        names = set(zf.namelist())
        assert names == {"part.step", "section.dxf", "manifest.json"}
        inner = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert inner == manifest  # the zip carries the SAME manifest
    assert manifest["artifacts"] == ["part.step", "section.dxf", "manifest.json"]


@pytest.mark.slow
def test_extras_strict_json_safe_and_body_unchanged(quote, box_body):
    res, _ = quote
    # extras contract: zip_path + manifest, strict-JSON-safe.
    assert set(res.extras.keys()) >= {"zip_path", "manifest"}
    json.dumps({"zip_path": res.extras["zip_path"],
                "manifest": res.extras["manifest"]}, allow_nan=False)
    # read-only: the returned body IS the input.
    assert res.body is box_body


# ──────────────────────────────────────────────────────────────────────────────
# refusals (each declared failure path is reachable)


def test_open_face_body_refused(box_body, tmp_path):
    """A face has no TopAbs_SOLID and no honest volume -> fm.no_solid_body."""
    from phone_designer.skills.inspect.quote_package import QuotePackage

    face = box_body.faces()[0]
    with pytest.raises(ValueError, match="fm.no_solid_body"):
        QuotePackage().apply(face, {"out_dir": str(tmp_path)})

    with pytest.raises(ValueError, match="fm.no_solid_body"):
        QuotePackage().apply(None, {"out_dir": str(tmp_path)})


def test_invalid_args_refused(tmp_path):
    from phone_designer.skills.inspect.quote_package import QuotePackage

    with pytest.raises(Exception, match="fm.invalid_args"):
        QuotePackage.Args(out_dir=str(tmp_path), lot_sizes=[])
    with pytest.raises(Exception, match="fm.invalid_args"):
        QuotePackage.Args(out_dir=str(tmp_path), lot_sizes=[0, 10])
    with pytest.raises(Exception, match="fm.invalid_args"):
        QuotePackage.Args(out_dir=str(tmp_path), processes=["cnc4axis"])
    with pytest.raises(Exception, match="fm.invalid_args"):
        QuotePackage.Args(out_dir=str(tmp_path), processes=[])
