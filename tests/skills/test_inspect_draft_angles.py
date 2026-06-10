"""inspect_draft_angles — per-face signed draft report vs pull direction.

Fixtures (build123d, algebra mode — skills accept anything with .wrapped):

    a) tapered boss: rectangle extruded with taper=2.0 deg → 4 drafted
       walls, authored draft exactly +2.0 deg.
    b) plain box: 4 dead-vertical walls → min draft 0.0, below_min_draft.
    c) horizontal bore through a box: the bore's cylindrical face has its
       UV-midpoint normal ⟂ pull (signed draft ≈ 0) — the documented
       inspect_undercut_zones_v2 1-sample false-negative — but its upper
       half overhangs the pull. The 5×5 grid must flag it undercut.
       Plus an inverted cone frustum (plain authored negative draft).
    d) box top/bottom faces (normal ‖ ±pull) are skipped entirely.
"""
from __future__ import annotations

import math

from build123d import (
    Box as B3Box,
    BuildPart,
    BuildSketch,
    Cone,
    Cylinder,
    Rectangle,
    extrude,
)

from phone_designer.skills.inspect.inspect_draft_angles import InspectDraftAngles


# ──────────────────────────────────────────────────────────────────────────────
# Fixture builders


def _tapered_boss(draft_deg: float = 2.0):
    """20×20 rectangle extruded 10 mm with ``draft_deg`` taper — build123d's
    positive taper slopes the walls inward going up, i.e. authored positive
    draft for a +Z pull."""
    with BuildPart() as bp:
        with BuildSketch():
            Rectangle(20.0, 20.0)
        extrude(amount=10.0, taper=draft_deg)
    return bp.part


def _plain_box():
    return B3Box(20.0, 20.0, 10.0)


def _undercut_cone(overhang_deg: float = 2.0):
    """Cone frustum that widens going up — lateral wall overhangs the +Z
    pull by exactly ``overhang_deg`` (authored negative draft)."""
    bottom_r = 8.0
    top_r = bottom_r + 10.0 * math.tan(math.radians(overhang_deg))
    return Cone(bottom_radius=bottom_r, top_radius=top_r, height=10.0)


def _box_with_horizontal_bore():
    """30×30×10 box with a 6 mm bore along Y — the bore's cylindrical face
    undercuts a +Z pull on its upper half, but its UV-midpoint normal is
    horizontal (signed draft ≈ 0): the v2 1-sample false-negative case."""
    return B3Box(30.0, 30.0, 10.0) - Cylinder(
        radius=3.0, height=40.0, rotation=(90, 0, 0),
    )


def _report(body, **args):
    r = InspectDraftAngles().apply(body, args)
    assert r.body is body  # read-only
    return r.extras["draft_report"]


# ──────────────────────────────────────────────────────────────────────────────
# (a) authored 2.0 deg draft → measured within ±0.05 deg


def test_tapered_boss_measures_authored_draft():
    report = _report(_tapered_boss(2.0))
    assert report["guarded"] is False
    walls = report["faces"]
    # top + bottom skipped → only the 4 drafted walls remain
    assert len(walls) == 4, f"expected 4 wall reports, got {walls}"
    for f in walls:
        assert abs(f["min_deg"] - 2.0) <= 0.05, f
        assert abs(f["mean_deg"] - 2.0) <= 0.05, f
        assert f["verdict"] == "ok", f
        assert len(f["worst_uv_xyz"]) == 3
    assert report["counts"] == {"ok": 4, "below_min_draft": 0, "undercut": 0}


def test_tapered_boss_below_min_draft_when_threshold_raised():
    """The same 2 deg boss fails a 3 deg minimum — but is NOT an undercut."""
    report = _report(_tapered_boss(2.0), min_draft_deg=3.0)
    verdicts = {f["verdict"] for f in report["faces"]}
    assert verdicts == {"below_min_draft"}


# ──────────────────────────────────────────────────────────────────────────────
# (b) dead-vertical wall → 0.0 deg, below_min_draft


def test_vertical_walls_zero_draft_below_min():
    report = _report(_plain_box())
    walls = report["faces"]
    assert len(walls) == 4
    for f in walls:
        assert f["min_deg"] == 0.0, f
        assert f["mean_deg"] == 0.0, f
        assert f["verdict"] == "below_min_draft", f
    assert report["counts"] == {"ok": 0, "below_min_draft": 4, "undercut": 0}


# ──────────────────────────────────────────────────────────────────────────────
# (c) undercuts — plain negative-draft wall + the v2 curved false-negative


def test_inverted_cone_wall_is_undercut():
    report = _report(_undercut_cone(2.0))
    under = [f for f in report["faces"] if f["verdict"] == "undercut"]
    assert len(under) == 1, report["faces"]
    assert abs(under[0]["min_deg"] - (-2.0)) <= 0.05, under[0]
    assert report["counts"]["undercut"] == 1


def test_horizontal_bore_curved_undercut_detected():
    """Curved face undercutting away from its UV midpoint — the documented
    inspect_undercut_zones_v2 false negative (its single midpoint sample
    sees signed draft ≈ 0 and dot(n, pull) ≈ 0 > -0.1)."""
    report = _report(_box_with_horizontal_bore())
    assert report["counts"]["undercut"] >= 1, report
    under = [f for f in report["faces"] if f["verdict"] == "undercut"]
    # The bore's top half overhangs steeply — the worst sample must be a
    # strongly negative draft, and located on the bore wall (|x| < bore
    # radius region, strictly inside the box in z).
    worst = min(under, key=lambda f: f["min_deg"])
    assert worst["min_deg"] < -45.0, worst
    x, y, z = worst["worst_uv_xyz"]
    assert abs(z) <= 3.01, worst  # on the 3 mm-radius bore, not an outer wall
    # mean over the full closed cylinder is ~0 — the 1-sample binary check
    # had nothing to bite on; only dense sampling exposes the negative lobe.
    assert worst["mean_deg"] > worst["min_deg"]


# ──────────────────────────────────────────────────────────────────────────────
# (d) faces ‖ pull are skipped


def test_box_top_and_bottom_faces_skipped():
    body = _plain_box()
    report = _report(body)
    # 6 box faces − 4 reported walls = 2 skipped (top + bottom)
    assert report["skipped_face_count"] == 2
    assert len(report["faces"]) == 4
    reported = {f["face_index"] for f in report["faces"]}
    assert len(reported) == 4
    # None of the reported faces may have a ±Z-facing normal: every wall's
    # worst sample must sit on a vertical side (|x|=10 or |y|=10).
    for f in report["faces"]:
        x, y, _ = f["worst_uv_xyz"]
        assert abs(abs(x) - 10.0) < 1e-3 or abs(abs(y) - 10.0) < 1e-3, f


def test_pull_direction_respected():
    """With pull along +X the box's ±X faces become the skipped pair and the
    other 4 are the walls."""
    report = _report(_plain_box(), pull_direction=[1.0, 0.0, 0.0])
    assert report["skipped_face_count"] == 2
    assert len(report["faces"]) == 4
    assert report["pull_direction"] == [1.0, 0.0, 0.0]
    for f in report["faces"]:
        x, _, _ = f["worst_uv_xyz"]
        assert abs(abs(x) - 10.0) > 1e-3, f  # not the ±X end faces


# ──────────────────────────────────────────────────────────────────────────────
# guard + arg validation


def test_face_count_guard_trips():
    report = _report(_plain_box(), max_face_count=3)
    assert report["guarded"] is True
    assert report["faces"] == []
    assert report["face_count"] == 6
    assert report["limit"] == 3
    assert report["counts"] == {"ok": 0, "below_min_draft": 0, "undercut": 0}


def test_zero_pull_vector_raises():
    import pytest

    with pytest.raises(ValueError, match="non-zero"):
        InspectDraftAngles().apply(_plain_box(), {
            "pull_direction": [0.0, 0.0, 0.0],
        })
