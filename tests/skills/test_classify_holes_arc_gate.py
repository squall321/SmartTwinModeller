"""classify_holes — angular-extent gate + thread-confidence floor (GEARBOX fix).

Reproduced bug (gearbox_housing.step): the drawing hole table carried four
spurious 'd24 depth85 simple' rows — the r12 CORNER FILLETS of the rectangular
cavity (quarter-cylinder faces, ~90° each), not holes. A hole's cylindrical
wall must enclose ~360°; classify_holes now requires the summed per-radius
u-extent of a bore group to reach ``_HOLE_MIN_ARC_EXTENT_DEG`` (300°).

Second bug: bearing counterbores drew junk thread guesses (M10 at confidence
0.026–0.077 — nearest catalog row, however far). Matches below
``_THREAD_MATCH_MIN_CONFIDENCE`` (0.30) are now suppressed to None.

Pins:
    1. box + rect pocket with r12 corner fillets → 0 holes.
    2. same pocket + 2 real drilled holes → exactly 2 holes.
    3. counterbore stack next to the pocket → still exactly 1 'counterbore'.
    4. low-confidence thread guess → None; high-confidence (M5 on Ø5.5,
       conf 1.0) KEPT exactly as before.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.classify_holes import (
    _HOLE_MIN_ARC_EXTENT_DEG,
    _THREAD_MATCH_MIN_CONFIDENCE,
    ClassifyHoles,
    _cyl_face_arc_extent_deg,
    _surface_kind,
)
from phone_designer.skills.modify_pocket.counterbore_hole import CounterboreHole
from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket
from phone_designer.skills.modify_pocket.hole import Hole


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures


def _top_face_selector():
    from phone_designer.skills._selectors import FacesByNormalSelector
    return FacesByNormalSelector(direction=(0.0, 0.0, 1.0), tol_deg=5.0)


def _box_with_rounded_pocket():
    """80 × 60 × 20 block with a 50 × 30 rectangular pocket, r12 corner
    fillets, 12 deep — the gearbox cavity pattern in miniature. The four
    corner fillets are quarter-cylinder faces (~90° each) on four DIFFERENT
    axes; before the extent gate each one became a spurious 'd24 simple'
    hole row."""
    body = Box().apply(None, {
        "length_mm": 80.0, "width_mm": 60.0, "height_mm": 20.0,
    }).body
    body = ExtrudePocket().apply(body, {
        "face_selector": _top_face_selector(),
        "sketch": {
            "kind": "rounded_rect",
            "length_mm": 50.0, "width_mm": 30.0, "corner_r_mm": 12.0,
        },
        "depth_mm": 12.0,
    }).body
    return body


def _drill(body, x, y, d_mm, depth_mm=25.0, z=20.0):
    return Hole().apply(body, {
        "position": (x, y, z),
        "diameter_mm": d_mm,
        "depth_mm": depth_mm,
        "direction": "-Z",
    }).body


def _holes(body, match_standards=False):
    return ClassifyHoles().apply(
        body, {"match_standards": match_standards},
    ).extras["holes"]


# ──────────────────────────────────────────────────────────────────────────────
# Helper-level: u-extent measurement


def test_arc_extent_quarter_fillet_vs_full_bore():
    """The gate's measurement itself: pocket corner fillets report ~90°,
    a drilled bore wall reports ~360°."""
    from phone_designer.skills._resolvers import _all_faces

    pocket_body = _box_with_rounded_pocket()
    shape = pocket_body.wrapped
    fillet_exts = [
        _cyl_face_arc_extent_deg(f)
        for f in _all_faces(shape) if _surface_kind(f) == "cylinder"
    ]
    assert len(fillet_exts) == 4, (
        f"expected 4 corner-fillet faces, got {len(fillet_exts)}"
    )
    for ext in fillet_exts:
        assert 60.0 <= ext <= 120.0, f"corner fillet extent {ext}° not ~90°"
        assert ext < _HOLE_MIN_ARC_EXTENT_DEG

    drilled = _drill(Box().apply(None, {
        "length_mm": 30.0, "width_mm": 30.0, "height_mm": 10.0,
    }).body, 0.0, 0.0, 6.0, depth_mm=15.0, z=10.0)
    bore_exts = [
        _cyl_face_arc_extent_deg(f)
        for f in _all_faces(drilled.wrapped) if _surface_kind(f) == "cylinder"
    ]
    assert bore_exts, "drilled bore produced no cylindrical face"
    assert max(bore_exts) >= _HOLE_MIN_ARC_EXTENT_DEG, (
        f"full bore wall extent {max(bore_exts)}° below the gate"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Pin 1 — pocket corner fillets are NOT holes


def test_pocket_corner_fillets_yield_zero_holes():
    body = _box_with_rounded_pocket()
    holes = _holes(body)
    assert holes == [], (
        "rect-pocket corner fillets misclassified as holes: "
        f"{[(h['type'], h['diameters_mm'], h['depth_mm']) for h in holes]}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Pin 2 — real drilled holes survive the gate, fillets still dropped


def test_two_real_holes_plus_pocket_corners_yield_exactly_two():
    body = _box_with_rounded_pocket()
    body = _drill(body, -32.0, -22.0, 5.0)
    body = _drill(body, 32.0, 22.0, 6.6)
    holes = _holes(body)
    assert len(holes) == 2, (
        f"expected exactly 2 holes, got {len(holes)}: "
        f"{[(h['type'], h['diameters_mm']) for h in holes]}"
    )
    found = sorted(min(h["diameters_mm"]) for h in holes)
    assert abs(found[0] - 5.0) < 0.1, f"Ø5 hole missing: {found}"
    assert abs(found[1] - 6.6) < 0.1, f"Ø6.6 hole missing: {found}"
    for h in holes:
        assert h["type"] == "simple"
        assert h["depth_mm"] >= 19.5  # through the 20 mm block


# ──────────────────────────────────────────────────────────────────────────────
# Pin 3 — counterbore stack (multi-radius group) still classified counterbore


def test_counterbore_next_to_pocket_still_counterbore():
    from phone_designer.skills._selectors import FacesNearPointSelector

    body = _box_with_rounded_pocket()
    # top face of the pocketed block is symmetric → centroid (0, 0, 20).
    body = CounterboreHole().apply(body, {
        "face_selector": FacesNearPointSelector(point=(0.0, 0.0, 20.0)),
        "position_xy": (30.0, 20.0),
        "thread_spec": "M4",
        "fit": "medium",
        "depth_mm": 6.0,
    }).body
    holes = _holes(body)
    assert len(holes) == 1, (
        f"expected exactly the counterbore, got {len(holes)}: "
        f"{[(h['type'], h['diameters_mm']) for h in holes]}"
    )
    h = holes[0]
    assert h["type"] == "counterbore", f"expected counterbore, got {h['type']}"
    # M4 ISO: cb Ø8 + medium clearance shaft Ø4.5 — both radii present.
    assert abs(max(h["diameters_mm"]) - 8.0) < 0.1
    assert abs(min(h["diameters_mm"]) - 4.5) < 0.1


# ──────────────────────────────────────────────────────────────────────────────
# Pin 4 — thread-confidence floor


def _slab_d35_and_d55():
    """80 × 60 × 10 slab: Ø35 bore (nearest catalog row M10 coarse Ø12 →
    confidence ≈ 0.042, pure noise) + Ø5.5 (M5 clearance medium, exact →
    confidence 1.0)."""
    body = Box().apply(None, {
        "length_mm": 80.0, "width_mm": 60.0, "height_mm": 10.0,
    }).body
    body = _drill(body, 0.0, 0.0, 35.0, depth_mm=15.0, z=10.0)
    body = _drill(body, 30.0, 20.0, 5.5, depth_mm=15.0, z=10.0)
    return body


def test_low_confidence_thread_guess_suppressed_to_none():
    holes = _holes(_slab_d35_and_d55(), match_standards=True)
    big = [h for h in holes if abs(min(h["diameters_mm"]) - 35.0) < 0.2]
    assert big, (
        f"Ø35 bore not detected: {[h['diameters_mm'] for h in holes]}"
    )
    assert big[0]["standard_match"] is None, (
        "junk thread guess not suppressed: "
        f"{big[0]['standard_match']}"
    )


def test_high_confidence_thread_match_kept():
    holes = _holes(_slab_d35_and_d55(), match_standards=True)
    m5 = [h for h in holes if abs(min(h["diameters_mm"]) - 5.5) < 0.2]
    assert m5, (
        f"Ø5.5 hole not detected: {[h['diameters_mm'] for h in holes]}"
    )
    sm = m5[0]["standard_match"]
    assert sm is not None, "high-confidence M5 match was wrongly suppressed"
    assert sm["thread_spec"] == "M5", f"expected M5, got {sm}"
    assert sm["confidence"] >= 0.9


def test_emitted_matches_always_at_or_above_floor():
    """Contract: any non-None standard_match carries confidence ≥ floor."""
    holes = _holes(_slab_d35_and_d55(), match_standards=True)
    assert holes, "no holes detected"
    for h in holes:
        sm = h["standard_match"]
        if sm is not None:
            assert sm["confidence"] >= _THREAD_MATCH_MIN_CONFIDENCE


# ──────────────────────────────────────────────────────────────────────────────
# Real-world pin — gearbox_housing.step (workspace artifact; skipped if absent)


_GEARBOX = (
    Path(__file__).resolve().parents[2] / ".pd_workspace" / "gearbox_housing.step"
)


@pytest.mark.skipif(not _GEARBOX.exists(), reason="gearbox workspace artifact absent")
def test_gearbox_housing_no_spurious_d24_rows_and_no_junk_threads():
    from phone_designer.skills.create.import_step import ImportStep

    body = ImportStep().apply(None, {"path": str(_GEARBOX)}).body
    holes = ClassifyHoles().apply(body, {"match_standards": True}).extras["holes"]

    # the four r12 cavity-corner fillets must NOT appear as d24 holes.
    d24 = [
        h for h in holes
        if any(abs(d - 24.0) < 0.2 for d in h["diameters_mm"])
    ]
    assert d24 == [], (
        f"spurious d24 corner-fillet rows still present: "
        f"{[(h['type'], h['diameters_mm'], h['depth_mm']) for h in d24]}"
    )

    # the real drilled patterns survive: 6 × Ø5.5 flange + 4 × Ø6.6 base.
    d55 = [h for h in holes if any(abs(d - 5.5) < 0.1 for d in h["diameters_mm"])]
    d66 = [h for h in holes if any(abs(d - 6.6) < 0.1 for d in h["diameters_mm"])]
    assert len(d55) >= 6, f"flange Ø5.5 holes lost: {len(d55)}"
    assert len(d66) >= 4, f"base Ø6.6 holes lost: {len(d66)}"

    # bearing seats (Y-axis bores) survive the gate…
    y_bores = [
        h for h in holes
        if abs(abs(h["axis_dir"][1]) - 1.0) < 0.05
        and max(h["diameters_mm"]) >= 15.0
    ]
    assert y_bores, "bearing-seat bores lost by the extent gate"
    # …and none of them carries a junk thread guess any more.
    for h in y_bores:
        sm = h["standard_match"]
        assert sm is None or sm["confidence"] >= _THREAD_MATCH_MIN_CONFIDENCE, (
            f"junk thread guess on bearing bore: {sm}"
        )
