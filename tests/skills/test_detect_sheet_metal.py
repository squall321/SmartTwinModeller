"""detect_sheet_metal — sheet-metal recognition + bend table (2026-06-20).

Synthetic bent-sheet fixtures (L-bracket 1 bend, U-channel 2 bends, flat blank,
solid block) pin recall + the measured bend geometry; a real corpus EXTRUSION
(2020 T-slot bracket) pins the anti-fake-accuracy guard — it must NOT be called
sheet metal and must report ZERO fake bends (its 180° edge rounds are not
press-brake bends).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.skills.inspect.detect_sheet_metal import DetectSheetMetal


def _sm(body, **kw):
    return DetectSheetMetal().apply(body, kw).extras["sheet_metal"]


def _l_bracket(t=2.0, r=2.0, La=30.0, Lb=20.0, W=25.0):
    from build123d import Axis, Box, Pos, fillet
    hor = Pos(La / 2, 0, t / 2) * Box(La, W, t)
    ver = Pos(t / 2, 0, Lb / 2) * Box(t, W, Lb)
    L = hor + ver
    e = min(L.edges().filter_by(Axis.Y),
            key=lambda e: (e.center().X ** 2 + e.center().Z ** 2))
    return fillet(e, r)


def _u_channel(t=2.0, r=2.0, Lb=20.0, W=25.0):
    from build123d import Axis, Box, Pos, fillet
    base = Pos(0, 0, t / 2) * Box(60, W, t)
    lt = Pos(-30 + t / 2, 0, Lb / 2) * Box(t, W, Lb)
    rt = Pos(30 - t / 2, 0, Lb / 2) * Box(t, W, Lb)
    U = base + lt + rt
    outer = [e for e in U.edges().filter_by(Axis.Y)
             if abs(e.center().Z) < 0.01 and abs(abs(e.center().X) - 30) < 0.01]
    return fillet(outer, r)


def test_l_bracket_one_bend_measured():
    r = _sm(_l_bracket())
    assert r["is_sheet_metal"] is True
    assert r["thickness_mm"] == pytest.approx(2.0, abs=0.05)
    assert r["n_bends"] == 1
    b = r["bends"][0]
    assert b["radius_mm"] == pytest.approx(2.0, abs=0.05)
    assert b["angle_deg"] == pytest.approx(90.0, abs=1.0)   # from the bend arc
    assert b["bend_line_length_mm"] == pytest.approx(25.0, abs=0.5)
    assert b["bend_allowance_mm"] > 0 and b["bend_deduction_mm"] > 0
    assert r["grade"] == "estimate"


def test_u_channel_two_bends():
    r = _sm(_u_channel())
    assert r["is_sheet_metal"] is True
    assert r["n_bends"] == 2
    assert r["flat_pattern"]["bends_share_single_axis"] is True


def test_l_bracket_developed_length():
    # flat blank = horizontal flat (~28) + vertical flat (~18) + bend allowance
    # (~4.52) ≈ 50.5mm, width = the bend-line length (25mm)
    fp = _sm(_l_bracket())["flat_pattern"]
    assert fp["developed_length_mm"] == pytest.approx(50.5, abs=1.0)
    assert fp["blank_width_mm"] == pytest.approx(25.0, abs=0.5)
    assert fp["flat_blank_area_mm2"] > 0


def test_flat_blank_developed_length_is_its_own_extent():
    from build123d import Box, Pos
    fp = _sm(Pos(0, 0, 1.0) * Box(50, 30, 2.0))["flat_pattern"]
    # an unbent blank's developed pattern is just the plate itself
    assert fp["developed_length_mm"] == pytest.approx(50.0, abs=0.5)
    assert fp["flat_blank_area_mm2"] == pytest.approx(1500.0, abs=20.0)


def test_flat_blank_is_sheet_with_no_bends():
    from build123d import Box, Pos
    r = _sm(Pos(0, 0, 1.0) * Box(50, 30, 2.0))
    assert r["is_sheet_metal"] is True
    assert r["n_bends"] == 0
    assert r["thickness_mm"] == pytest.approx(2.0, abs=0.05)


def test_solid_block_is_not_sheet_metal():
    from build123d import Box
    r = _sm(Box(40, 30, 25))
    assert r["is_sheet_metal"] is False
    assert r["n_bends"] == 0


def _concentric_hem():
    # base flange (z 0-2) + 180° fold (inner R2/outer R4, concentric at (4,4)) +
    # return flange (z 6-8) doubled back — an OPEN hem with a real gap.
    from build123d import (BuildLine, BuildSketch, Line, Plane, Polyline,
                           RadiusArc, extrude, make_face)
    with BuildSketch(Plane.XZ) as s:
        with BuildLine():
            Polyline((40, 0), (4, 0))
            RadiusArc((4, 0), (4, 8), 4.0)        # outer fold, center (4,4)
            Polyline((4, 8), (30, 8))
            Line((30, 8), (30, 6))
            Polyline((30, 6), (4, 6))
            RadiusArc((4, 6), (4, 2), 2.0)        # inner fold, concentric (4,4)
            Polyline((4, 2), (40, 2))
            Line((40, 2), (40, 0))
        make_face()
    return extrude(s.sketch, amount=25)


def _rounded_edge_plate():
    # a plate whose +X edge is rounded over to a 180° semicircle (radius t/2)
    from build123d import (BuildLine, BuildSketch, Line, Plane, Polyline,
                           RadiusArc, extrude, make_face)
    with BuildSketch(Plane.XZ) as s:
        with BuildLine():
            Polyline((0, 0), (48, 0))
            RadiusArc((48, 0), (48, 2), 1.0)      # semicircle cap radius t/2
            Polyline((48, 2), (0, 2))
            Line((0, 2), (0, 0))
        make_face()
    return extrude(s.sketch, amount=30)


def test_hem_recognised_as_a_fold_not_a_bend():
    r = _sm(_concentric_hem())
    assert r["is_sheet_metal"] is True
    assert r["thickness_mm"] == pytest.approx(2.0, abs=0.05)
    assert r["n_hems"] == 1 and r["rounded_edges"] == 0
    h = r["hems"][0]
    assert h["angle_deg"] == pytest.approx(180.0, abs=1.0)
    assert h["bend_allowance_mm"] > 0
    # a 180° fold has no meaningful bend deduction (tan(θ/2)→∞)
    assert h["bend_deduction_mm"] is None


def test_rounded_edge_is_not_counted_as_a_hem():
    r = _sm(_rounded_edge_plate())
    assert r["is_sheet_metal"] is True
    assert r["rounded_edges"] == 1   # the one sheet's two sides, offset ≈ thickness
    assert r["n_hems"] == 0          # NOT a hem (no return flange)


def test_thick_part_outside_gauge_is_not_sheet():
    # an 8mm-thick slab is plate/machined, not formed sheet (gauge bound rejects
    # thick assemblies whose minimum wall was mis-read as 'thin')
    from build123d import Box, Pos
    r = _sm(Pos(0, 0, 4.0) * Box(120, 90, 8.0))
    assert r["is_sheet_metal"] is False
    assert any("gauge range" in a for a in r["assumptions"])


def test_flat_blank_is_flagged_ambiguous_with_lower_confidence():
    # a flat unformed blank cannot be told from a machined plate / moulded slab
    from build123d import Box, Pos
    flat = _sm(Pos(0, 0, 1.0) * Box(50, 30, 2.0))
    bent = _sm(_l_bracket())
    assert flat["is_sheet_metal"] is True
    assert flat["flat_unformed"] is True          # honestly flagged
    assert bent["flat_unformed"] is False         # a formed bracket is unambiguous
    assert bent["confidence"] > flat["confidence"]  # forming is stronger evidence
    assert any("FLAT (unformed)" in a for a in flat["assumptions"])


def test_k_factor_raises_bend_allowance():
    soft = _sm(_l_bracket(), k_factor=0.33)["bends"][0]["bend_allowance_mm"]
    hard = _sm(_l_bracket(), k_factor=0.50)["bends"][0]["bend_allowance_mm"]
    assert hard > soft  # BA = arc·(r + K·t) grows with K


_EXTRUSION = Path("corpus/oem/industrial/freecad__2020_corner_bracket.step")


@pytest.mark.skipif(not _EXTRUSION.exists(), reason="corpus extrusion absent")
def test_extrusion_not_sheet_and_no_fake_bends():
    """Anti-fake-accuracy: a constant-cross-section ALUMINIUM EXTRUSION is not
    sheet metal, and its 180° edge rounds must NOT be reported as bends."""
    from phone_designer.skills.create.import_step import ImportStep
    body = ImportStep().apply(None, {"path": str(_EXTRUSION)}).body
    r = _sm(body)
    assert r["is_sheet_metal"] is False
    assert r["n_bends"] == 0
