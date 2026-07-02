"""extract_feature_catalog determinism (roadmap 2-4a) + corpus profile CLI (2-4b).

THE PIN: ExtractFeatureCatalog with ``parallel=True`` (the default every corpus
consumer uses) produces a BYTE-IDENTICAL catalog JSON across 5 runs, each on a
FRESHLY built body (fresh triangulation state — the honest way to exercise the
race; reusing one body would share the mesh and trivially pass).

THE DIAGNOSED MECHANISM (fixed 2026-07-02 in extract_feature_catalog):
detect_mirror_symmetry is the only pooled detector that tessellates the shape
(BRepMesh_IncrementalMesh @ 0.5 mm / 20°); the FIRST triangulation makes every
LATER BRepBndLib Add_s/AddOptimal_s return a slightly different (mesh-derived)
bbox, so which detector saw which bbox depended on ThreadPoolExecutor
scheduling. Measured pre-fix: 3 distinct catalogs over 6 runs on the
box+holes+fillets fixture below. The fix is a canonical single-threaded
tessellation pass BEFORE the fan-out (same mesh parameters, so total meshing
work is unchanged — only WHEN it happens moved).

HONEST EXCLUSION: ``_timings_sec`` is per-detector wall-clock and inherently
volatile; the canonical comparison strips ONLY that key. Everything else —
every feature list, every coordinate, ``initial_bbox_mm``, ``_skipped_
detectors`` — must be byte-identical.

Also pinned here:
  * parallel=True == parallel=False (the flag is now a pure perf knob);
  * ``initial_bbox_mm`` still equals the PRE-tessellation exact AddOptimal_s
    bbox of a pristine body (the PACK-B snapshot must not see the canonical
    mesh);
  * the profile CLI (python -m phone_designer.corpus.profile) runs the
    analyze pipeline stages and prints the per-stage table;
  * aggregate() math on synthetic records (analytic values).
"""
from __future__ import annotations

import json

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# Fixture bodies — non-trivial, freshly built PER RUN


def _box_holes_fillets():
    """60x40x8 slab, 5 holes (2 sizes), 4 vertical-edge fillets."""
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.modify_curvature.fillet_predicate import (
        FilletEdgesByPredicate,
    )
    from phone_designer.skills.modify_pocket.hole import Hole

    body = Box().apply(None, {
        "length_mm": 60.0, "width_mm": 40.0, "height_mm": 8.0,
    }).body
    for (x, y), d in (
        ((-20.0, -12.0), 4.0), ((20.0, -12.0), 4.0),
        ((-20.0, 12.0), 6.0), ((20.0, 12.0), 6.0),
        ((0.0, 0.0), 10.0),
    ):
        body = Hole().apply(body, {
            "position": (x, y, 8.0), "diameter_mm": d,
            "depth_mm": 10.0, "direction": "-Z",
        }).body
    return FilletEdgesByPredicate().apply(body, {
        "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
        "radius_mm": 3.0,
    }).body


def _revolved():
    """Bushing profile (lines + arc) revolved 360° — curvy surfaces."""
    from phone_designer.skills.create.sketch_revolve import SketchRevolve

    return SketchRevolve().apply(None, {
        "sketch": {"kind": "composite", "segments": [
            {"kind": "line", "start": [6.0, 0.0], "end": [14.0, 0.0]},
            {"kind": "line", "start": [14.0, 0.0], "end": [14.0, 4.0]},
            {"kind": "arc", "start": [14.0, 4.0], "end": [10.0, 8.0],
             "radius": 4.0, "ccw": True},
            {"kind": "line", "start": [10.0, 8.0], "end": [6.0, 8.0]},
            {"kind": "line", "start": [6.0, 8.0], "end": [6.0, 0.0]},
        ]},
        "angle_deg": 360.0,
    }).body


def _sheet_like():
    """Thin L-bracket (2 mm walls) + 3 holes through the base leg."""
    from phone_designer.skills.create.sketch_extrude import SketchExtrude
    from phone_designer.skills.modify_pocket.hole import Hole

    body = SketchExtrude().apply(None, {
        "sketch": {"kind": "polygon", "vertices": [
            [0.0, 0.0], [80.0, 0.0], [80.0, 2.0],
            [2.0, 2.0], [2.0, 25.0], [0.0, 25.0],
        ]},
        "height_mm": 40.0,
    }).body
    for x in (20.0, 40.0, 60.0):
        body = Hole().apply(body, {
            "position": (x, 2.0, 20.0), "diameter_mm": 5.0,
            "depth_mm": 4.0, "direction": "-Y",
        }).body
    return body


_FIXTURES = {
    "box_holes_fillets": _box_holes_fillets,
    "revolved": _revolved,
    "sheet_like": _sheet_like,
}


def _canonical_json(catalog: dict) -> str:
    """Catalog → canonical JSON. Strips ONLY ``_timings_sec`` (wall-clock,
    inherently volatile); every geometric/feature key stays in."""
    c = dict(catalog)
    c.pop("_timings_sec", None)
    return json.dumps(c, sort_keys=True)


def _extract(body, **args) -> dict:
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    return ExtractFeatureCatalog().apply(body, args).extras["feature_catalog"]


# ──────────────────────────────────────────────────────────────────────────────
# THE pin — 5x byte-identity with parallel=True, fresh body per run


@pytest.mark.parametrize("fixture_name", sorted(_FIXTURES))
def test_catalog_byte_identical_5_runs_parallel(fixture_name):
    build = _FIXTURES[fixture_name]
    canons = []
    for _ in range(5):
        body = build()  # FRESH body — fresh triangulation state each run
        canons.append(_canonical_json(_extract(body, parallel=True)))
    distinct = set(canons)
    assert len(distinct) == 1, (
        f"{fixture_name}: catalog JSON NOT byte-identical across 5 parallel "
        f"runs — {len(distinct)} distinct catalogs (determinism fix broken)"
    )
    # the fixture must actually be non-trivial (guards against a silently
    # empty catalog passing the identity check for free)
    cat = json.loads(canons[0])
    n_features = sum(
        len(cat.get(k) or []) for k in ("holes", "pockets", "edge_blends")
    )
    assert n_features >= 3, (
        f"{fixture_name}: expected a non-trivial catalog, got only "
        f"{n_features} holes+pockets+edge_blends"
    )


def test_parallel_equals_serial():
    """The parallel flag is now a PURE perf knob: identical output either way
    (both sides on fresh bodies, so neither inherits the other's mesh)."""
    par = _canonical_json(_extract(_box_holes_fillets(), parallel=True))
    ser = _canonical_json(_extract(_box_holes_fillets(), parallel=False))
    assert par == ser, "parallel=True catalog differs from parallel=False"


def test_initial_bbox_is_pre_tessellation_exact():
    """PACK-B guarantee preserved: ``initial_bbox_mm`` equals the exact
    AddOptimal_s bbox of a PRISTINE body — the canonical tessellation pass
    must run AFTER the snapshot, never before."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    pristine = _box_holes_fillets()
    bb = Bnd_Box()
    BRepBndLib.AddOptimal_s(pristine.wrapped, bb)
    expected = [float(c) for c in bb.Get()]

    cat = _extract(_box_holes_fillets(), parallel=True)
    got = cat.get("initial_bbox_mm")
    assert got is not None, "initial_bbox_mm missing from catalog"
    assert got == pytest.approx(expected, abs=1e-9), (
        f"initial_bbox_mm {got} != pristine exact bbox {expected} — the "
        "canonical tessellation leaked into the PACK-B snapshot"
    )


def test_canonical_tessellation_actually_ran():
    """The fix's tessellation pass records its timing — proves the pass is in
    the pipeline (a silent removal would resurrect the race unnoticed)."""
    cat = _extract(_box_holes_fillets(), parallel=True)
    assert "canonical_tessellation" in (cat.get("_timings_sec") or {}), (
        "_timings_sec lacks 'canonical_tessellation' — the determinism "
        "pass did not run"
    )


# ──────────────────────────────────────────────────────────────────────────────
# profile CLI (2-4b)


def _fixture_step_file(tmp_path):
    """Export the box+holes+fillets fixture to a STEP file for the CLI."""
    from phone_designer.skills.io.step_export_v2 import StepExportV2

    out = tmp_path / "profile_fixture.step"
    StepExportV2().apply(_box_holes_fillets(), {"path": str(out)})
    assert out.is_file() and out.stat().st_size > 0
    return out


def test_profile_cli_prints_stage_table(tmp_path, capsys):
    """python -m phone_designer.corpus.profile <step> — runs the analyze
    round-trip and prints the per-stage duration table."""
    from phone_designer.corpus.profile import STAGES, main

    step = _fixture_step_file(tmp_path)
    json_out = tmp_path / "profile.json"
    rc = main([str(step), "--json", str(json_out), "--top-steps", "3"])
    assert rc == 0

    out = capsys.readouterr().out
    for stage in STAGES:
        assert stage in out, f"stage column '{stage}' missing from table"
    assert "TOTAL (ms)" in out and "share %" in out
    assert "single-run wall-clock" in out  # honest caveat INSIDE the output

    payload = json.loads(json_out.read_text(encoding="utf-8"))
    rec = payload["records"][0]
    # every stage ran on this healthy fixture and carries a real duration
    for stage in STAGES:
        v = rec["stages_ms"][stage]
        assert isinstance(v, (int, float)) and v >= 0.0, (
            f"stage '{stage}' has no recorded duration: {v!r}"
        )
    assert rec["error"] is None
    assert isinstance(rec["match_ratio"], float)
    # drill-downs re-aggregated from the already-recorded durations
    assert rec["detector_ms"], "detector breakdown (catalog _timings_sec) empty"
    assert "canonical_tessellation" in rec["detector_ms"]
    agg = payload["aggregate"]
    assert agg["n_files"] == 1 and agg["grand_total_ms"] > 0.0


def test_profile_aggregate_math_analytic():
    """aggregate() on synthetic records — analytic totals/means/shares."""
    from phone_designer.corpus.profile import STAGES, aggregate

    def rec(imp, ext):
        stages = {s: None for s in STAGES}
        stages["import"] = imp
        stages["extract"] = ext
        return {"stages_ms": stages}

    agg = aggregate([rec(100.0, 300.0), rec(200.0, 400.0)])
    assert agg["n_files"] == 2
    assert agg["grand_total_ms"] == pytest.approx(1000.0)
    assert agg["stages"]["import"]["total_ms"] == pytest.approx(300.0)
    assert agg["stages"]["import"]["mean_ms"] == pytest.approx(150.0)
    assert agg["stages"]["import"]["share_pct"] == pytest.approx(30.0)
    assert agg["stages"]["extract"]["share_pct"] == pytest.approx(70.0)
    # stages that never ran are None-safe
    assert agg["stages"]["diff"]["total_ms"] == 0.0
    assert agg["stages"]["diff"]["mean_ms"] is None
    assert agg["stages"]["diff"]["n_files"] == 0


def test_profile_file_missing_input_is_honest_error():
    """A bad path produces a structured error record, not a traceback."""
    from phone_designer.corpus.profile import profile_file

    rec = profile_file("does_not_exist_xyz.step", mode="preserve_brep")
    assert rec["error"], "expected an error record for a missing file"
    assert rec["total_ms"] is not None
