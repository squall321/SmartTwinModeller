"""classify_holes — small-diameter (sub-mm to Ø1 mm) hole hardening.

COMPLEX-CAD pass-24 (2026-06-10): Ventilator's regen body lost 12 of 13
Ø1 mm holes because the analytic cylinder location of our own cut faces
can sit mid-band, which made ``_standardize_entry``'s stage-1 flip land
outside the body and the pass-23 phantom filter then dropped real holes.

These tests pin the floor behavior on a clean slab:

    1. Ø1.0 mm and Ø0.6 mm through-holes are BOTH detected, with
       diameters within ±5 %.
    2. A Ø12 mm hole on the same slab is still detected identically
       (large-hole guard — pass-24 must not disturb existing behavior).
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.classify_holes import ClassifyHoles
from phone_designer.skills.modify_pocket.hole import Hole


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures


def _slab_with_small_holes():
    """30 × 30 × 4 slab carrying three through-holes (over-drilled so the
    cut fully clears the bottom face):

        1. Ø1.0  at (-8,  0)
        2. Ø0.6  at (+8,  0)
        3. Ø12.0 at ( 0, +8)   — large-hole guard
    """
    body = Box().apply(None, {
        "length_mm": 30.0, "width_mm": 30.0, "height_mm": 4.0,
    }).body

    for (x, y), d in (((-8.0, 0.0), 1.0), ((8.0, 0.0), 0.6), ((0.0, 8.0), 12.0)):
        body = Hole().apply(body, {
            "position": (x, y, 4.0),
            "diameter_mm": d,
            "depth_mm": 6.0,
            "direction": "-Z",
        }).body
    return body


def _find_hole_with_diameter(holes, d_mm, tol_frac=0.05):
    """First hole whose diameters_mm contains an entry within ±tol_frac."""
    for h in holes:
        for d in h.get("diameters_mm") or []:
            if abs(float(d) - d_mm) <= tol_frac * d_mm:
                return h
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Tests


def test_small_diameter_holes_detected_with_correct_diameters():
    body = _slab_with_small_holes()
    holes = ClassifyHoles().apply(body, {"match_standards": False}).extras["holes"]
    assert len(holes) >= 3, (
        f"expected >=3 holes, got {len(holes)}: "
        f"{[(h['type'], h['diameters_mm']) for h in holes]}"
    )

    h10 = _find_hole_with_diameter(holes, 1.0)
    assert h10 is not None, (
        "O1.0 mm hole not detected — "
        f"diameters found: {[h['diameters_mm'] for h in holes]}"
    )
    h06 = _find_hole_with_diameter(holes, 0.6)
    assert h06 is not None, (
        "O0.6 mm hole not detected — "
        f"diameters found: {[h['diameters_mm'] for h in holes]}"
    )
    assert h10 is not h06, "O1.0 and O0.6 resolved to the SAME descriptor"

    # entry positions land at the drilled XY (sub-0.1 mm — same axis).
    assert abs(h10["axis_origin"][0] - (-8.0)) < 0.1
    assert abs(h10["axis_origin"][1] - 0.0) < 0.1
    assert abs(h06["axis_origin"][0] - 8.0) < 0.1
    assert abs(h06["axis_origin"][1] - 0.0) < 0.1


def test_small_holes_carry_entry_fields_and_positive_depth():
    """Pass-23/24 contract: every emitted hole has body-relative entry
    fields — the planner's box-mode cut anchor — and a positive depth."""
    body = _slab_with_small_holes()
    holes = ClassifyHoles().apply(body, {"match_standards": False}).extras["holes"]
    for d_mm in (1.0, 0.6):
        h = _find_hole_with_diameter(holes, d_mm)
        assert h is not None
        assert len(h["entry_origin"]) == 3
        assert h["entry_depth_mm"] > 0.0
        assert h["depth_mm"] > 0.0


def test_large_hole_guard_detected_identically():
    """The O12 mm hole must keep its exact pre-pass-24 descriptor shape:
    simple type, single O12 diameter (+-5 %), through-slab depth."""
    body = _slab_with_small_holes()
    holes = ClassifyHoles().apply(body, {"match_standards": False}).extras["holes"]
    h12 = _find_hole_with_diameter(holes, 12.0)
    assert h12 is not None, (
        "O12 mm guard hole not detected — "
        f"diameters found: {[h['diameters_mm'] for h in holes]}"
    )
    assert h12["type"] == "simple", f"expected simple, got {h12['type']}"
    assert abs(min(h12["diameters_mm"]) - 12.0) <= 0.6
    assert abs(h12["axis_origin"][0] - 0.0) < 0.1
    assert abs(h12["axis_origin"][1] - 8.0) < 0.1
    # through the 4 mm slab — depth at least the slab thickness.
    assert h12["depth_mm"] >= 3.9
    # no private band keys may leak into the emitted descriptor.
    assert not any(k.startswith("_band") for k in h12)
