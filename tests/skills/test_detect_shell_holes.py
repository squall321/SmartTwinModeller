"""detect_shell_holes — open-shell hole detection on a synthetic shelled box.

The skill only reports CLOSED free-boundary wires, so a watertight solid
must yield zero holes. To synthesize the mesh-scan case we build a box,
drill two through-bores, then drop the bottom face and re-sew the rest
into an open shell: the bores' bottom rims become two closed circular
free boundaries, and the box's bottom rectangle becomes a third closed
free boundary whose 120 mm perimeter exceeds the 80 mm default cap
(rejected_perimeter).

HISTORY (V6 audit, fixed 2026-06-10): ``_fit_circle_2d``'s Kasa normal
equations used to mis-scale the ``c`` column ([Sx, Sy, n] instead of
[Sx/2, Sy/2, n/2]), so with centroid-origin projection (Sx ≈ Sy ≈ 0) a
PERFECT circle fitted at r = R/√2 — radial deviation √2−1 ≈ 0.414 over
the default sin(10°) ≈ 0.174 gate, rejecting every clean circular cutout
at default args. This file used to pin that behavior behind a loosened
gate (axis_tolerance_deg=30) plus a strict xfail; the fix landed and the
tests below now assert the correct behavior at DEFAULT args: true
diameter, exact centers, arc_completeness ≈ 1.0.
"""
from __future__ import annotations

import math

import pytest
from build123d import Axis, Box as B3dBox, Cylinder, Pos
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing

from phone_designer.skills.inspect.detect_shell_holes import (
    DetectShellHoles,
    _fit_circle_2d,
)


HOLE_D = 6.0              # true bore diameter
HOLE_XS = (-8.0, 8.0)      # bore centers on the x axis
BOX = (30.0, 30.0, 10.0)   # build123d Box is CENTER-aligned → z in [-5, +5]


def _open_shell_with_two_bores():
    """30x30x10 box, two vertical Ø6 through-bores, bottom face removed."""
    solid = B3dBox(*BOX)
    for x in HOLE_XS:
        solid = solid - Pos(x, 0, 0) * Cylinder(HOLE_D / 2.0, 40)

    bottom = solid.faces().sort_by(Axis.Z)[0]  # planar face at z=-5 with both openings
    sew = BRepBuilderAPI_Sewing(1e-6)
    for f in solid.faces():
        if f == bottom:
            continue
        sew.Add(f.wrapped)
    sew.Perform()
    return sew.SewedShape()  # raw open TopoDS shell — _occt_shape passes it through


# ──────────────────────────────────────────────────────────────────────────────
# Kasa circle fit — exact recovery of perfect circles (the V6 audit hand case)


def test_fit_circle_2d_recovers_unit_circle_exactly():
    pts = [
        (math.cos(t), math.sin(t))
        for t in (2.0 * math.pi * i / 32 for i in range(32))
    ]
    cx, cy, r = _fit_circle_2d(pts)
    assert cx == pytest.approx(0.0, abs=1e-12)
    assert cy == pytest.approx(0.0, abs=1e-12)
    assert r == pytest.approx(1.0, abs=1e-12)


def test_fit_circle_2d_recovers_offset_circle():
    # Non-zero Sx/Sy exercises the full 3×3 system, not just the c row.
    pts = [
        (3.0 + 2.5 * math.cos(t), -4.0 + 2.5 * math.sin(t))
        for t in (2.0 * math.pi * i / 48 for i in range(48))
    ]
    cx, cy, r = _fit_circle_2d(pts)
    assert cx == pytest.approx(3.0, abs=1e-9)
    assert cy == pytest.approx(-4.0, abs=1e-9)
    assert r == pytest.approx(2.5, abs=1e-9)


# ──────────────────────────────────────────────────────────────────────────────
# Shelled box with circular cutouts — both detected at DEFAULT args


def test_two_circular_cutouts_detected_with_center_and_diameter():
    res = DetectShellHoles().apply(_open_shell_with_two_bores(), {})
    holes = res.extras["shell_holes"]
    assert len(holes) == 2, (
        f"expected exactly 2 shell holes, got {len(holes)}: {holes}"
    )

    holes = sorted(holes, key=lambda h: h["center"][0])
    for h, expected_x in zip(holes, sorted(HOLE_XS)):
        cx, cy, cz = h["center"]
        assert cx == pytest.approx(expected_x, abs=0.2)
        assert cy == pytest.approx(0.0, abs=0.2)
        assert cz == pytest.approx(-BOX[2] / 2.0, abs=0.2)  # bottom rim z = -5
        # Bore axis is vertical — fitted plane normal must be ±Z.
        assert abs(h["axis"][2]) == pytest.approx(1.0, abs=0.05)
        assert h["planarity_score"] > 0.9
        assert h["perimeter_mm"] == pytest.approx(math.pi * HOLE_D, rel=0.01)
        # Fitted diameter now matches the true bore diameter (Ø6 ± 1%).
        assert h["diameter_mm"] == pytest.approx(HOLE_D, rel=0.01)
        assert h["arc_completeness"] == pytest.approx(1.0, abs=0.01)


def test_summary_counts_and_perimeter_rejection_of_outer_rim():
    res = DetectShellHoles().apply(_open_shell_with_two_bores(), {})
    summary = res.extras["shell_holes_summary"]
    # 3 closed free wires total: 2 bore rims + the 120 mm outer rectangle.
    assert summary["closed_wires_found"] == 3
    assert summary["holes_reported"] == 2
    assert summary["rejected_perimeter"] == 1  # the 120 mm > 80 mm rectangle
    assert summary["rejected_geometry"] == 0   # perfect circles pass the gate


def test_perimeter_window_can_exclude_the_bores():
    # Tighten max_perimeter below pi*6 ~ 18.85 mm → bores get rejected too.
    res = DetectShellHoles().apply(
        _open_shell_with_two_bores(),
        {"max_perimeter_mm": 10.0},
    )
    assert res.extras["shell_holes"] == []
    assert res.extras["shell_holes_summary"]["rejected_perimeter"] == 3


# ──────────────────────────────────────────────────────────────────────────────
# Watertight solid — no free boundaries, no holes


def test_solid_box_reports_no_shell_holes():
    res = DetectShellHoles().apply(B3dBox(*BOX), {})
    assert res.extras["shell_holes"] == []
    assert res.extras["shell_holes_summary"]["closed_wires_found"] == 0


def test_solid_box_with_drilled_bores_still_reports_none():
    # Even WITH analytic through-holes the solid stays watertight — this is
    # exactly the classify_holes territory the docstring delegates to.
    solid = B3dBox(*BOX)
    for x in HOLE_XS:
        solid = solid - Pos(x, 0, 0) * Cylinder(HOLE_D / 2.0, 40)
    res = DetectShellHoles().apply(solid, {})
    assert res.extras["shell_holes"] == []
    assert res.extras["shell_holes_summary"]["closed_wires_found"] == 0
