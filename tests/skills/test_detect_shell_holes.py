"""detect_shell_holes — open-shell hole detection on a synthetic shelled box.

The skill only reports CLOSED free-boundary wires, so a watertight solid
must yield zero holes. To synthesize the mesh-scan case we build a box,
drill two through-bores, then drop the bottom face and re-sew the rest
into an open shell: the bores' bottom rims become two closed circular
free boundaries, and the box's bottom rectangle becomes a third closed
free boundary whose 120 mm perimeter exceeds the 80 mm default cap
(rejected_perimeter).

KNOWN SOURCE BUG pinned here (do NOT silently "fix" these assertions —
fix ``_fit_circle_2d`` instead and the strict xfail below will flag it):

    ``_fit_circle_2d``'s Kasa normal equations mis-scale the ``c`` column
    (row 1 uses ``Sx`` where the calculus gives ``Sx/2``; row 3 uses ``n``
    where it gives ``n/2``). Because the projection origin is the wire
    centroid, ``Sx ≈ Sy ≈ 0`` and the solver lands on ``c = R²/2``, i.e.
    fitted radius = R/√2 ≈ 0.707·R for a PERFECT circle. Consequences:

      1. max radial deviation ratio is √2−1 ≈ 0.414 for every clean
         circle — over the default sin(10°) ≈ 0.174 gate, so circular
         cutouts are rejected with default args (rejected_geometry).
      2. with the gate loosened to ≥ asin(√2−1) ≈ 24.5°, the holes ARE
         reported, at the correct center but with diameter_mm = true/√2
         and arc_completeness = √2.
"""
from __future__ import annotations

import math

import pytest
from build123d import Axis, Box as B3dBox, Cylinder, Pos
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing

from phone_designer.skills.inspect.detect_shell_holes import DetectShellHoles


HOLE_D = 6.0              # true bore diameter
HOLE_XS = (-8.0, 8.0)      # bore centers on the x axis
BOX = (30.0, 30.0, 10.0)   # build123d Box is CENTER-aligned → z in [-5, +5]
# Gate wide enough to admit the √2−1 radial-deviation artifact (see module
# docstring); sin(30°) = 0.5 > 0.414.
LOOSE_GATE = {"axis_tolerance_deg": 30.0}


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
# Shelled box with circular cutouts — both detected (loosened gate)


def test_two_circular_cutouts_detected_with_center_and_diameter():
    res = DetectShellHoles().apply(_open_shell_with_two_bores(), LOOSE_GATE)
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
        # CURRENT (buggy) diameter: true/√2 — see module docstring. The
        # measured perimeter is the trustworthy size signal today.
        assert h["diameter_mm"] == pytest.approx(HOLE_D / math.sqrt(2), abs=0.05)
        assert h["arc_completeness"] == pytest.approx(math.sqrt(2), abs=0.01)


@pytest.mark.xfail(
    strict=True,
    reason="_fit_circle_2d Kasa normal equations mis-scale the c column "
           "(should be Sx/2 / n/2) → fitted r = R/√2, radial deviation "
           "√2−1 > sin(10°) default gate. Once fixed, default args must "
           "detect both Ø6 cutouts with true diameter — then fold this "
           "test into the one above and drop the LOOSE_GATE workaround.",
)
def test_default_args_detect_true_diameter_once_circle_fit_is_fixed():
    res = DetectShellHoles().apply(_open_shell_with_two_bores(), {})
    holes = res.extras["shell_holes"]
    assert len(holes) == 2
    for h in holes:
        assert h["diameter_mm"] == pytest.approx(HOLE_D, abs=0.1)


def test_default_gate_currently_rejects_the_perfect_circles():
    # Pin the CURRENT default-args outcome so a future circle-fit fix is
    # forced to revisit this file (together with the strict xfail above).
    res = DetectShellHoles().apply(_open_shell_with_two_bores(), {})
    summary = res.extras["shell_holes_summary"]
    assert summary["closed_wires_found"] == 3  # 2 bore rims + outer rectangle
    assert summary["holes_reported"] == 0
    assert summary["rejected_perimeter"] == 1  # the 120 mm > 80 mm rectangle
    assert summary["rejected_geometry"] == 2   # the two perfect circles (!)


def test_summary_counts_and_perimeter_rejection_of_outer_rim():
    res = DetectShellHoles().apply(_open_shell_with_two_bores(), LOOSE_GATE)
    summary = res.extras["shell_holes_summary"]
    # 3 closed free wires total: 2 bore rims + the 120 mm outer rectangle.
    assert summary["closed_wires_found"] == 3
    assert summary["holes_reported"] == 2
    assert summary["rejected_perimeter"] == 1  # the 120 mm > 80 mm rectangle
    assert summary["rejected_geometry"] == 0


def test_perimeter_window_can_exclude_the_bores():
    # Tighten max_perimeter below pi*6 ~ 18.85 mm → bores get rejected too.
    res = DetectShellHoles().apply(
        _open_shell_with_two_bores(),
        {"max_perimeter_mm": 10.0, **LOOSE_GATE},
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
    res = DetectShellHoles().apply(solid, LOOSE_GATE)
    assert res.extras["shell_holes"] == []
    assert res.extras["shell_holes_summary"]["closed_wires_found"] == 0
