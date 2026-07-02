"""analyze_assembly — one-call multi-body STEP analysis (Track 2-3).

Builds a synthetic assembly IN THE TEST (no corpus dependency): a compound of
2 identical Ø3x12 bolts (one deliberately rotated, so dedup must be
rigid-motion-invariant) + 1 plate, exported to STEP. Pins:

  * dedup groups the 2 bolts into ONE signature class with count=2;
  * totals against ANALYTIC volumes (plate 40x20x5 = 4000 mm3, bolt
    pi*1.5^2*12 = 84.823 mm3);
  * interference DETECTED for the overlapping plate x bolt pair with the
    analytic overlap volume pi*1.5^2*4.5 = 31.809 mm3, and CLEAR for the
    separated pair;
  * the MANDATORY standard-part gate: the recognized Ø3 round part (M3
    candidate) NEVER carries a machined-cost estimate even with
    estimate_cost=True, while the plate does get one;
  * honest budget skips (per_component_timeout_s=0.0 deterministic
    degenerate) + honest pair-budget skips (max_pairs=1);
  * strict-JSON safety + schema_version, and both structured refusals
    (fm.step_read_failed / fm.empty_assembly) reachable without a traceback.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from phone_designer.skills.reverse_engineer.analyze_assembly import (
    AnalyzeAssembly,
    _recognize_standard_part,
    _round_part_probe,
)

PLATE_VOL = 40.0 * 20.0 * 5.0                      # 4000 mm3
BOLT_VOL = math.pi * 1.5 ** 2 * 12.0               # 84.8230 mm3
OVERLAP_VOL = math.pi * 1.5 ** 2 * 4.5             # 31.8086 mm3 (z -2.0..+2.5)


def _write_step(shapes, path: Path) -> str:
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    from phone_designer.skills.assembly._compound import build_compound

    comp = build_compound(
        [s.wrapped if hasattr(s, "wrapped") else s for s in shapes])
    w = STEPControl_Writer()
    w.Transfer(comp, STEPControl_AsIs)
    w.Write(str(path))
    return str(path)


@pytest.fixture(scope="module")
def asm_step(tmp_path_factory) -> str:
    """Plate (40x20x5 @origin) + bolt A (Ø3x12 @ (10,0,4) — OVERLAPS the
    plate by 4.5 mm of shank) + bolt B (identical but ROTATED 90° about X and
    far away at (100,0,20) — clear of everything)."""
    from build123d import Box, Cylinder, Pos, Rot

    plate = Box(40, 20, 5)
    bolt_a = Pos(10, 0, 4) * Cylinder(1.5, 12)
    bolt_b = Pos(100, 0, 20) * Rot(90, 0, 0) * Cylinder(1.5, 12)
    return _write_step([plate, bolt_a, bolt_b],
                       tmp_path_factory.mktemp("asm") / "asm.step")


@pytest.fixture(scope="module")
def report(asm_step) -> dict:
    """ONE full run shared by most tests (estimate_cost=True so the
    standard-part gate is exercised in the same pass)."""
    res = AnalyzeAssembly().apply(None, {
        "assembly_path": asm_step,
        "estimate_cost": True,
    })
    return res.extras["assembly_analysis"]


def _class_by_count(report: dict, count: int) -> dict:
    rows = [r for r in report["components"] if r["count"] == count]
    assert len(rows) == 1, (count, [(r["class_id"], r["count"])
                                    for r in report["components"]])
    return rows[0]


# ── schema + JSON safety ─────────────────────────────────────────────────────


def test_schema_version_and_strict_json(report):
    assert report["schema_version"] == 1
    assert report["kind"] == "AssemblyReportV1"
    assert report["refusal"] is None
    # strict-JSON contract: no inf/nan anywhere in the artifact.
    json.dumps(report, allow_nan=False)
    # honest grade label lives INSIDE the artifact.
    assert report["grade"] == "light"
    assert "light profile" in report["grade_note"]


# ── dedup ────────────────────────────────────────────────────────────────────


def test_dedup_groups_identical_bolts_into_one_class(report):
    assert report["totals"]["components_total"] == 3
    assert report["totals"]["signature_classes"] == 2
    bolts = _class_by_count(report, 2)
    assert len(bolts["names"]) == 2
    assert abs(bolts["volume_mm3"] - BOLT_VOL) < 0.5
    assert report["dedup"]["analysis_runs_saved"] == 1


def test_dedup_off_gives_one_class_per_instance(asm_step):
    res = AnalyzeAssembly().apply(None, {
        "assembly_path": asm_step,
        "dedup_instances": False,
        "per_component_timeout_s": 0.0,  # analysis not needed for this pin
        "interference": False,
    })
    rep = res.extras["assembly_analysis"]
    assert rep["totals"]["signature_classes"] == 3
    assert all(r["count"] == 1 for r in rep["components"])


# ── totals + light analysis ──────────────────────────────────────────────────


def test_totals_analytic_volumes(report):
    expected = PLATE_VOL + 2 * BOLT_VOL
    assert abs(report["totals"]["total_volume_mm3"] - expected) < 1.0
    bolts = _class_by_count(report, 2)
    assert abs(bolts["total_volume_mm3"] - 2 * BOLT_VOL) < 1.0


def test_light_analysis_carries_catalog_counts_and_mass(report):
    plate = _class_by_count(report, 1)
    assert plate["analysis_profile"] == "light"
    ana = plate["analysis"]
    assert isinstance(ana["feature_counts"], dict)
    mass = ana["mass"]
    # density 1.0 g/cm3 -> mass_g == volume_cm3 == 4.0 g for the plate.
    assert abs(mass["volume_mm3"] - PLATE_VOL) < 1.0
    assert abs(mass["mass_g"] - PLATE_VOL / 1000.0) < 0.01
    # rollup mass covers every instance (plate + 2 bolts).
    total_g = (PLATE_VOL + 2 * BOLT_VOL) / 1000.0
    assert abs(report["totals"]["total_mass_g"] - total_g) < 0.01


# ── interference / clearance (static pose only) ─────────────────────────────


def test_interference_detected_for_overlapping_pair(report):
    inter = report["interference"]
    assert inter["label"] == "static_pose_only"
    assert "static_pose_only" in inter["note"]
    contacts = inter["contacts"]
    assert len(contacts) == 1, contacts
    plate = _class_by_count(report, 1)
    bolts = _class_by_count(report, 2)
    pair = {contacts[0]["a"], contacts[0]["b"]}
    assert plate["names"][0] in pair
    assert pair & set(bolts["names"]), (pair, bolts["names"])
    assert abs(contacts[0]["overlap_volume_mm3"] - OVERLAP_VOL) < 0.5


def test_clearance_clear_for_separated_pair(report):
    inter = report["interference"]
    assert inter["checked_pairs"] == 3
    assert inter["skipped_pairs"] == 0
    # ONLY the overlapping pair violates min_clearance (distance 0); the far
    # bolt is clear of both other components.
    violations = inter["clearance_violations"]
    assert len(violations) == 1, violations
    assert violations[0]["min_dist_mm"] == pytest.approx(0.0, abs=1e-6)
    contacts = {frozenset((c["a"], c["b"])) for c in inter["contacts"]}
    assert frozenset((violations[0]["a"], violations[0]["b"])) in contacts


def test_pair_budget_skips_honestly(asm_step):
    res = AnalyzeAssembly().apply(None, {
        "assembly_path": asm_step,
        "per_component_timeout_s": 0.0,  # focus the run on the matrix
        "max_pairs": 1,
    })
    inter = res.extras["assembly_analysis"]["interference"]
    assert inter["pairs_total"] == 3
    assert inter["checked_pairs"] == 1
    assert inter["skipped_pairs"] == 2
    assert "NOT checked" in inter["skipped_note"]
    # the AABB-gap sort puts the overlapping (gap 0) pair first, so the
    # single checked pair still finds the contact.
    assert len(inter["contacts"]) == 1


# ── mandatory standard-part cost gate ────────────────────────────────────────


def test_standard_part_recognized_with_honest_labels(report):
    bolts = _class_by_count(report, 2)
    std = bolts["standard_part"]
    assert std is not None
    assert std["kind"] == "fastener_or_pin"
    assert std["designation"] == "M3"
    assert std["confidence"] >= 0.99
    # honest labels INSIDE the artifact: candidate grade + stated basis.
    assert std["grade"] == "estimate"
    assert "NOT verified" in std["basis"]


def test_standard_part_never_carries_machined_cost(report):
    """THE mandatory gate: estimate_cost=True was passed for this run, yet
    the recognized catalog part must NOT get a machined-cost estimate."""
    bolts = _class_by_count(report, 2)
    assert bolts["standard_part"] is not None
    assert bolts["cost_estimate"] is None
    assert "SUPPRESSED" in bolts["cost_note"]
    assert "catalog" in bolts["cost_note"]
    # while the NON-standard plate does get the opt-in estimate.
    plate = _class_by_count(report, 1)
    assert plate["standard_part"] is None
    assert isinstance(plate["cost_estimate"], dict)
    assert plate["cost_estimate"].get("unit_cost_usd", 0) > 0
    assert report["totals"]["standard_part_classes"] == 1


def test_ring_probe_matches_bearing_catalog():
    """Helper-level pin: an OD22 / bore8 / W7 ring maps to the 608 bearing."""
    from build123d import Cylinder

    ring = Cylinder(11, 7) - Cylinder(4, 7.2)
    probe = _round_part_probe(ring.wrapped)
    assert probe == {"form": "ring", "outer_d_mm": 22.0, "bore_d_mm": 8.0,
                     "width_mm": 7.0}
    std = _recognize_standard_part(ring, probe, 0.85)
    assert std is not None and std["kind"] == "bearing_or_bushing"
    assert std["designation"] == "608"


def test_oring_probe_matches_as568_catalog():
    """Helper-level pin: a torus with cs 1.78 / id 10.82 maps to AS568-013."""
    from build123d import Torus

    tor = Torus(6.3, 0.89)  # major r 6.3, minor r 0.89
    probe = _round_part_probe(tor.wrapped)
    assert probe == {"form": "oring", "cs_mm": 1.78, "id_mm": 10.82}
    std = _recognize_standard_part(tor, probe, 0.85)
    assert std is not None and std["kind"] == "oring"
    assert "013" in std["designation"]


def test_plate_is_not_recognized_as_standard():
    from build123d import Box

    assert _round_part_probe(Box(40, 20, 5).wrapped) is None


# ── budget honesty ───────────────────────────────────────────────────────────


def test_budget_zero_skips_all_classes_honestly(asm_step):
    res = AnalyzeAssembly().apply(None, {
        "assembly_path": asm_step,
        "per_component_timeout_s": 0.0,
    })
    rep = res.extras["assembly_analysis"]
    assert rep["totals"]["classes_skipped_for_budget"] == 2
    assert rep["totals"]["classes_analyzed"] == 0
    for row in rep["components"]:
        assert row["skipped_for_budget"] is True
        assert row["analysis"] is None
        assert "skipped_for_budget" in row["errors"]["budget"]
    # dedup + the interference matrix still ran (separate phases).
    assert rep["totals"]["signature_classes"] == 2
    assert rep["interference"] is not None
    assert rep["budget"]["per_component_timeout_s"] == 0.0
    assert "cooperative" in rep["budget"]["policy"]
    json.dumps(rep, allow_nan=False)


# ── structured refusals (no tracebacks) ──────────────────────────────────────


def test_missing_file_refuses_step_read_failed():
    res = AnalyzeAssembly().apply(None, {
        "assembly_path": "does/not/exist.step",
    })
    rep = res.extras["assembly_analysis"]
    assert rep["refusal"] == "fm.step_read_failed"
    assert rep["components"] == []
    assert rep["error"]  # raw import error carried, not masked
    assert rep["schema_version"] == 1
    json.dumps(rep, allow_nan=False)


def test_solidless_step_refuses_empty_assembly(tmp_path):
    """A STEP carrying only a bare face (no solids, no shells) must refuse
    with fm.empty_assembly, not crash or fake an analysis."""
    from build123d import Rectangle

    path = _write_step([Rectangle(10, 10)], tmp_path / "face_only.step")
    res = AnalyzeAssembly().apply(None, {"assembly_path": path})
    rep = res.extras["assembly_analysis"]
    assert rep["refusal"] == "fm.empty_assembly"
    assert rep["components"] == []
    assert rep["totals"]["components_total"] == 0
    json.dumps(rep, allow_nan=False)


def test_args_reject_unknown_key(asm_step):
    with pytest.raises(Exception):
        AnalyzeAssembly().apply(None, {"assembly_path": asm_step,
                                       "bogus_arg": 1})


# ── cache reuse (_re_cache mechanism) ────────────────────────────────────────


def test_cache_hit_on_second_run(asm_step, tmp_path):
    cache_dir = str(tmp_path / "re_cache")
    args = {"assembly_path": asm_step, "cache": True,
            "cache_dir": cache_dir, "interference": False}
    first = AnalyzeAssembly().apply(None, dict(args)).extras[
        "assembly_analysis"]
    assert all(r["cache_hit"] is False for r in first["components"])
    assert first["dedup"]["cache"]["writes"] == 2
    second = AnalyzeAssembly().apply(None, dict(args)).extras[
        "assembly_analysis"]
    assert all(r["cache_hit"] is True for r in second["components"])
    # the cached light analysis is identical to the freshly computed one.
    for a, b in zip(first["components"], second["components"]):
        assert a["analysis"] == b["analysis"]
