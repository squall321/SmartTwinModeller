"""batch_analyze — batch single-part front-door harness (2026-06-18).

Builds 2-3 tiny synthetic STEP bodies (box + hole, ALGEBRA mode), runs the
batch over them, and pins the combined deliverable:

  * index.json carries the right n_files / n_ok / n_failed,
  * each ok part entry carries feature_counts + key_dimensions + a per-part
    report file was actually written under out_dir,
  * a deliberately-bad path in the list is recorded as FAILED (not a crash),
  * index.html is self-contained and links the per-part reports.

The batch isolates each file in a subprocess; these tests run it serially
(workers=1) so they are deterministic and fast.
"""
from __future__ import annotations

import json
from pathlib import Path

from phone_designer.corpus import batch_analyze


def _synth_step(path: Path, lx: float, ly: float, lz: float) -> Path:
    """A box + Ø4 through hole written to ``path`` — exercises holes + topology
    + mass + key dimensions. ALGEBRA mode (no ``with Pos():`` context managers,
    which this build123d does not support)."""
    from build123d import Box, Cylinder
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    part = Box(lx, ly, lz) - Cylinder(2.0, lz + 0.1)
    shape = part.wrapped
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    w.Write(str(path))
    return path


def test_batch_over_synthetic_steps_full_deliverable(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    a = _synth_step(in_dir / "blockA.step", 20, 16, 8)
    b = _synth_step(in_dir / "blockB.step", 30, 12, 6)
    out_dir = tmp_path / "out"

    files = batch_analyze.resolve_inputs([str(a), str(b)])
    assert len(files) == 2

    index = batch_analyze.run_batch(
        files, out_dir=out_dir, fmt="html", workers=1, log=lambda *_: None
    )

    # combined summary
    assert index["n_files"] == 2
    assert index["n_ok"] == 2
    assert index["n_failed"] == 0
    assert len(index["parts"]) == 2

    # index.json + index.html actually written, self-contained
    index_json = json.loads((out_dir / "index.json").read_text(encoding="utf-8"))
    assert index_json["n_ok"] == 2
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "<html" in html.lower()
    assert "blockA" in html and "blockB" in html
    # dependency-free: no external <script src> / <link href>
    assert "src=" not in html.lower()
    assert "<link" not in html.lower()

    by_id = {r["part_id"]: r for r in index["parts"]}
    for pid in ("blockA", "blockB"):
        rec = by_id[pid]
        assert rec["ok"] is True
        assert rec["error"] is None
        # feature_counts present and the box+hole gives >=1 hole
        assert isinstance(rec["feature_counts"], dict)
        assert rec["feature_counts"].get("holes", 0) >= 1
        # key_dimensions present, named + valued
        kds = rec["key_dimensions"]
        assert isinstance(kds, list) and len(kds) >= 1
        assert all("name" in d and "value_mm" in d for d in kds)
        # a per-part HTML report was written under out_dir
        assert rec["report_html"] == f"{pid}.html"
        assert (out_dir / f"{pid}.html").is_file()
        assert (out_dir / f"{pid}.html").stat().st_size > 500
        # bbox recovered
        assert isinstance(rec["bbox_mm"], list) and len(rec["bbox_mm"]) == 6
        assert rec["duration_s"] is not None


def test_batch_records_bad_path_as_failed_not_crash(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    good = _synth_step(in_dir / "good.step", 18, 14, 7)
    bad = in_dir / "does_not_exist.step"  # never created
    out_dir = tmp_path / "out"

    # resolve_inputs drops the non-existent path, so feed run_batch directly
    # with the bad Path to prove ONE bad file is recorded as failed, not fatal.
    index = batch_analyze.run_batch(
        [good, bad], out_dir=out_dir, fmt="json", workers=1, log=lambda *_: None
    )

    assert index["n_files"] == 2
    assert index["n_ok"] == 1
    assert index["n_failed"] == 1
    by_id = {r["part_id"]: r for r in index["parts"]}

    assert by_id["good"]["ok"] is True
    assert by_id["good"]["report_json"] == "good.json"
    assert (out_dir / "good.json").is_file()

    bad_rec = by_id["does_not_exist"]
    assert bad_rec["ok"] is False
    assert bad_rec["error"]  # an honest error string, not a traceback
    # the batch still produced its combined index
    assert (out_dir / "index.json").is_file()
    assert (out_dir / "index.html").is_file()


def test_batch_format_both_writes_html_and_json(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    c = _synth_step(in_dir / "blockC.step", 22, 18, 9)
    out_dir = tmp_path / "out"

    index = batch_analyze.run_batch(
        [c], out_dir=out_dir, fmt="both", workers=1, log=lambda *_: None
    )
    assert index["n_ok"] == 1
    rec = index["parts"][0]
    assert rec["report_html"] == "blockC.html"
    assert rec["report_json"] == "blockC.json"
    assert (out_dir / "blockC.html").is_file()
    assert (out_dir / "blockC.json").is_file()
    # per-part JSON drops the bulky html string but keeps the analysis
    pj = json.loads((out_dir / "blockC.json").read_text(encoding="utf-8"))
    assert "report_html" not in pj
    assert pj["part_id"] == "blockC"
    assert pj["feature_catalog"]["counts"]["holes"] >= 1


def test_resolve_inputs_dir_and_glob(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    _synth_step(in_dir / "p1.step", 12, 10, 5)
    _synth_step(in_dir / "p2.step", 14, 11, 6)
    (in_dir / "notcad.txt").write_text("nope", encoding="utf-8")

    # directory → all CAD files inside, the .txt ignored
    via_dir = batch_analyze.resolve_inputs([str(in_dir)])
    assert {p.name for p in via_dir} == {"p1.step", "p2.step"}

    # glob pattern
    via_glob = batch_analyze.resolve_inputs(None, glob=str(in_dir / "*.step"))
    assert {p.name for p in via_glob} == {"p1.step", "p2.step"}

    # de-dup: same file via two tokens collapses
    dup = batch_analyze.resolve_inputs(
        [str(in_dir / "p1.step"), str(in_dir)]
    )
    assert sum(1 for p in dup if p.name == "p1.step") == 1
