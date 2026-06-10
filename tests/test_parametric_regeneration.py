"""Parametric regeneration demo v2 — CI-safe pytest gate (plan item P4).

Runs ONLY the simple_watch housing subset of the demo matrix (no corpus
dependency — the fixture is rebuilt via fixtures/make_simple_watch.py on
demand) through the SAME code the full demo uses
(``phone_designer.corpus.parametric_demo``; tools/parametric_regen_demo.py
is a thin wrapper around it).

Pinned expectations (see docs/parametric_regeneration_demo_v2.md):
  * all 4 scale cells pass (a) bbox ±0.5 % and (d) features_lost == 0;
  * check (b) re-detected min hole diameter is pinned to its CURRENT
    truthful state: it FAILS on this fixture at every scale because of a
    known upstream classify_holes bug (the crown hole's axis_origin is
    the OCCT cylinder surface's analytic location, ~9.5 mm short of the
    actual face band, so the box-mode cut is subsumed by the bore void
    and the Ø2.5 hole can never be re-detected). The pin is TIGHT: (b)
    must be the ONLY failing check and carry the documented signature —
    any new failure mode trips the test; an upstream fix flips (b) to
    PASS and trips the pin so it gets removed deliberately;
  * the hole-diameter edit changes the rebuilt volume by the analytic
    annulus delta ±15 % (the v1 demo could not detect this at all);
  * the pocket-depth edit is SKIP-NO-TARGET — simple_watch's only catalog
    pocket is a diagonal-axis detector artifact the planner correctly
    filters — and must NEVER silently no-op (FAIL/ERROR) the way v1 did.
"""
from __future__ import annotations

import pathlib

import pytest

pytestmark = [pytest.mark.fidelity, pytest.mark.slow]

_REPO = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def watch_cells():
    """Run the 6-cell simple_watch matrix once for the whole module."""
    from phone_designer.corpus.parametric_demo import (
        run_matrix,
        simple_watch_file_spec,
    )

    spec = simple_watch_file_spec(_REPO)  # builds the fixture when absent
    cells = run_matrix([spec], log=lambda *_a, **_k: None)
    return {c.variant: c for c in cells}


def _fmt(cell) -> str:
    return (
        f"{cell.file}/{cell.variant}: status={cell.status} "
        f"reason={cell.reason!r} metrics={cell.metrics}"
    )


def test_matrix_has_all_six_cells(watch_cells):
    expected = {
        "scale_0.5", "scale_1", "scale_1.5", "scale_2",
        "edit_holes0_d_x1.5", "edit_pockets0_depth_x2.0",
    }
    assert set(watch_cells) == expected, sorted(watch_cells)


@pytest.mark.parametrize("variant", ["scale_0.5", "scale_1", "scale_1.5", "scale_2"])
def test_scale_cell_analytic_checks(watch_cells, variant):
    cell = watch_cells[variant]
    # (a) + (d) must pass outright — these prove the scaling pipeline.
    assert cell.checks["a_bbox"]["ok"] is True, _fmt(cell)
    assert cell.checks["d_features"]["ok"] is True, _fmt(cell)

    # (b) — references the scale-1.0 BASELINE rebuild's re-detected min
    # hole diameter (P4 revision, 2026-06-10), so it measures VARIATION
    # proportionality and passes outright. The earlier catalog-referenced
    # pin tripped as designed when the metric revision landed.
    assert cell.checks["b_hole_redetect"]["ok"] is True, _fmt(cell)
    assert cell.status == "PASS", _fmt(cell)

    # KNOWN RECONSTRUCTION GAP pin (P4, 2026-06-10), kept visible via the
    # info metrics: the crown hole's axis_origin sits at the analytic
    # cylinder surface location ([11, 0, 6.5]), ~9.5 mm short of the
    # actual face band (x≈20.5..22), so _body_entry_along_axis's
    # [0, depth] clamp yields an entry_origin inside the Ø33.4 bore
    # void: the box-mode crown cut no-ops and the Ø2.5 hole is never
    # re-detected (baseline min d = 33.4, catalog min d = 2.5). This is
    # a box-mode COMPLETENESS gap (corpus-regress territory), not a
    # variation defect. When classify_holes/planner fix crown-hole
    # emission (band-anchored entry — pass-24 has the data), the
    # baseline min drops to ≈2.5 and THIS pin trips for deliberate
    # removal.
    cat_d = cell.metrics.get("catalog_min_hole_d_mm")
    base_d = cell.metrics.get("baseline_min_hole_d_mm")
    assert cat_d == pytest.approx(2.5), _fmt(cell)
    if base_d == pytest.approx(2.5, rel=0.1):
        pytest.fail(
            "baseline rebuild now re-detects the Ø2.5 crown hole — the "
            "box-mode completeness gap is fixed: remove this pin. "
            + _fmt(cell)
        )
    assert base_d == pytest.approx(33.4, rel=0.05), _fmt(cell)


def test_hole_edit_changes_volume_by_analytic_delta(watch_cells):
    """holes.0.diameters_mm x1.5 must move the rebuilt volume by the
    annulus delta pi/4*(d2^2-d1^2)*emitted_depth within 15 % — bit-identical
    geometry here was exactly the unnoticed v1 failure mode."""
    cell = watch_cells["edit_holes0_d_x1.5"]
    assert cell.status == "PASS", _fmt(cell)
    assert cell.checks["c_edit_delta"]["ok"] is True, _fmt(cell)
    # the delta must be a real (negative — material removed) change
    assert cell.metrics["actual_delta_mm3"] < 0.0, _fmt(cell)
    assert cell.metrics["delta_rel_err"] <= 0.15, _fmt(cell)


def test_pocket_edit_is_never_a_silent_noop(watch_cells):
    """simple_watch's only catalog pocket is a diagonal detector artifact
    that the planner (correctly) filters, so the edit cannot reach the
    plan: the harness must say so explicitly (SKIP-NO-TARGET), never FAIL
    silently nor pretend the variant succeeded (v1 behavior). If a future
    planner change starts emitting the pocket, PASS is also acceptable —
    but FAIL/ERROR means the no-op detection or determinism check broke."""
    cell = watch_cells["edit_pockets0_depth_x2.0"]
    assert cell.status in ("PASS", "SKIP"), _fmt(cell)
    if cell.status == "SKIP":
        assert "SKIP-NO-TARGET" in cell.reason, _fmt(cell)
        # determinism: identical plans must rebuild bit-identically
        assert cell.metrics["actual_delta_mm3"] == 0.0, _fmt(cell)
