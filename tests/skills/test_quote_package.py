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
  * PROMOTED Phase-2 drawing (include_drawing=True, the default): the zip
    carries drawing/part_drawing.html + >=4 per-view DXFs and
    manifest['drawing'] = {html, dxf_views, grade='draft',
    label='DRAFT FOR REVIEW', status='ok'};
  * include_drawing=False -> the OLD contract exactly (same zip file set,
    no 'drawing' manifest key, no drawing/ dir on disk);
  * FAILURE ISOLATION: DrawingSheet.apply raising (monkeypatched) does NOT
    kill the quote — manifest['drawing']={'status':'failed','error':...} and
    costs/step/dxf still ship;
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
    # manifest completeness IS the contract ('drawing' since the 2-1 → 1-5
    # promotion — include_drawing defaults to True).
    for section in ("part", "costs", "process_recommendation",
                    "quality_summary", "dxf", "drawing", "artifacts",
                    "cost_model"):
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

    # exact file set: the classic three PLUS the promoted drawing artifacts
    # (include_drawing defaults to True).
    drawing = manifest["drawing"]
    expected = ({"part.step", "section.dxf", "manifest.json",
                 drawing["html"]} | set(drawing["dxf_views"]))
    with zipfile.ZipFile(out_dir / "quote_package.zip") as zf:
        names = set(zf.namelist())
        assert names == expected
        inner = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert inner == manifest  # the zip carries the SAME manifest
    assert manifest["artifacts"] == (
        ["part.step", "section.dxf", drawing["html"]]
        + drawing["dxf_views"] + ["manifest.json"])


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
# Phase-2 drawing promotion (plan 2-1 → 1-5)


@pytest.mark.slow
def test_drawing_promoted_into_zip_and_manifest(quote):
    """Default include_drawing=True: sheet HTML + >=4 view DXFs in the zip,
    manifest['drawing'] labeled grade='draft' / DRAFT FOR REVIEW."""
    res, out_dir = quote
    drawing = res.extras["manifest"]["drawing"]
    assert drawing["status"] == "ok"
    assert drawing["grade"] == "draft"
    assert drawing["label"] == "DRAFT FOR REVIEW"
    assert drawing["html"] == "drawing/part_drawing.html"
    # third-angle FRONT/TOP/RIGHT + ISO -> at least 4 per-view DXFs.
    assert len(drawing["dxf_views"]) >= 4
    assert all(v.startswith("drawing/") and v.endswith(".dxf")
               for v in drawing["dxf_views"])

    with zipfile.ZipFile(out_dir / "quote_package.zip") as zf:
        names = set(zf.namelist())
        assert drawing["html"] in names
        assert set(drawing["dxf_views"]) <= names
        # the sheet itself carries the DRAFT label (baked into the artifact).
        html = zf.read(drawing["html"]).decode("utf-8")
        assert "DRAFT FOR REVIEW" in html
        for view_arc in drawing["dxf_views"]:
            assert len(zf.read(view_arc)) > 0


@pytest.mark.slow
def test_include_drawing_false_is_the_old_contract(box_body, tmp_path):
    """include_drawing=False -> the pre-promotion zip file set exactly, no
    'drawing' manifest key, no drawing/ dir on disk."""
    from phone_designer.skills.inspect.quote_package import QuotePackage

    res = QuotePackage().apply(box_body, {"out_dir": str(tmp_path),
                                          "include_drawing": False})
    manifest = res.extras["manifest"]
    assert "drawing" not in manifest
    assert manifest["artifacts"] == ["part.step", "section.dxf",
                                     "manifest.json"]
    with zipfile.ZipFile(tmp_path / "quote_package.zip") as zf:
        assert set(zf.namelist()) == {"part.step", "section.dxf",
                                      "manifest.json"}
    assert not (tmp_path / "drawing").exists()


@pytest.mark.slow
def test_drawing_failure_never_kills_the_quote(box_body, tmp_path, monkeypatch):
    """FAILURE ISOLATION: DrawingSheet.apply raising is recorded honestly as
    manifest['drawing']={'status':'failed','error':...} — the quote still
    returns ok and the zip still ships costs + step + dxf."""
    from phone_designer.skills.inspect import drawing_sheet as ds_mod
    from phone_designer.skills.inspect.quote_package import QuotePackage

    def _boom(self, body, args):  # noqa: ANN001, ARG002
        raise RuntimeError("forced drawing failure (test)")

    monkeypatch.setattr(ds_mod.DrawingSheet, "apply", _boom)
    res = QuotePackage().apply(box_body, {"out_dir": str(tmp_path)})

    manifest = res.extras["manifest"]
    drawing = manifest["drawing"]
    assert drawing["status"] == "failed"
    assert "RuntimeError" in drawing["error"]
    assert "forced drawing failure" in drawing["error"]
    assert drawing["html"] is None
    assert drawing["dxf_views"] == []
    # the rest of the package still shipped, grade labels intact.
    assert manifest["costs"]
    assert all(c["grade"] == "estimate" for c in manifest["costs"])
    assert manifest["cost_model"]["grade"] == "estimate"
    assert manifest["artifacts"] == ["part.step", "section.dxf",
                                     "manifest.json"]
    with zipfile.ZipFile(tmp_path / "quote_package.zip") as zf:
        assert set(zf.namelist()) == {"part.step", "section.dxf",
                                      "manifest.json"}
    json.dumps(res.extras, allow_nan=False)  # still strict-JSON-safe


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
