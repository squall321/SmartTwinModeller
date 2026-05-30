"""classify_holes — hole-family classifier.

Build a box with several hole types and assert the classifier:

    1. Finds at least one hole per kind that was drilled.
    2. Tags each as simple / counterbore / countersink / spotface.
    3. For an M3 clearance hole, returns the M3 row from the standards
       catalog (or at least a high-confidence match within ±0.4 mm).
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.classify_holes import ClassifyHoles
from phone_designer.skills.modify_pocket.clearance_hole import ClearanceHole
from phone_designer.skills.modify_pocket.counterbore_hole import CounterboreHole
from phone_designer.skills.modify_pocket.countersink_hole import CountersinkHole
from phone_designer.skills.modify_pocket.hole import Hole


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures


def _top_face_selector():
    from phone_designer.skills._selectors import FacesByNormalSelector
    return FacesByNormalSelector(direction=(0.0, 0.0, 1.0), tol_deg=5.0)


def _box_with_assorted_holes():
    """60 × 60 × 15 plate carrying:

        1. simple blind hole  Ø4 × 6 deep      (at -22, -22)
        2. M3 clearance hole  Ø3.4 through     (at +22, -22)
        3. M4 counterbore                       (at -22, +22)
        4. M3 countersink                       (at +22, +22)
    """
    body = Box().apply(None, {
        "length_mm": 60.0, "width_mm": 60.0, "height_mm": 15.0,
    }).body

    # (1) simple blind hole, Ø4, depth 6, enters from top.
    body = Hole().apply(body, {
        "position": (-22.0, -22.0, 15.0),
        "diameter_mm": 4.0,
        "depth_mm": 6.0,
        "direction": "-Z",
    }).body

    # (2) M3 clearance, medium fit (Ø3.4), through.
    body = ClearanceHole().apply(body, {
        "face_selector": _top_face_selector(),
        "position_xy": (22.0, -22.0),
        "thread_spec": "M3",
        "fit": "medium",
        "depth_mm": 1.0,        # ignored when through
        "through": True,
    }).body

    # (3) M4 counterbore: cb Ø8 × 4.4, shaft Ø4.5 × 6.
    body = CounterboreHole().apply(body, {
        "face_selector": _top_face_selector(),
        "position_xy": (-22.0, 22.0),
        "thread_spec": "M4",
        "fit": "medium",
        "depth_mm": 6.0,
    }).body

    # (4) M3 countersink: cone Ø6 × 3 deep apex, shaft Ø3.4 × 6.
    body = CountersinkHole().apply(body, {
        "face_selector": _top_face_selector(),
        "position_xy": (22.0, 22.0),
        "thread_spec": "M3",
        "fit": "medium",
        "depth_mm": 6.0,
    }).body

    return body


# ──────────────────────────────────────────────────────────────────────────────
# Tests


def test_classify_holes_finds_all_features():
    body = _box_with_assorted_holes()
    r = ClassifyHoles().apply(body, {})
    holes = r.extras["holes"]
    # at least four holes — one per drilled feature
    assert len(holes) >= 4, (
        f"expected ≥4 holes, got {len(holes)}: "
        f"{[h['type'] for h in holes]}"
    )
    # body unchanged
    assert r.body is body


def test_classify_types_cover_simple_counterbore_countersink():
    body = _box_with_assorted_holes()
    holes = ClassifyHoles().apply(body, {}).extras["holes"]
    types = {h["type"] for h in holes}
    # We expect at least these three categories to appear.
    assert "simple" in types, f"no simple hole detected. types={types}"
    assert "counterbore" in types, f"no counterbore detected. types={types}"
    assert "countersink" in types, f"no countersink detected. types={types}"


def test_classify_each_hole_has_required_fields():
    body = _box_with_assorted_holes()
    holes = ClassifyHoles().apply(body, {}).extras["holes"]
    for h in holes:
        assert "id" in h
        assert h["type"] in (
            "simple", "counterbore", "countersink",
            "counterdrill", "spotface", "threaded",
        )
        assert len(h["axis_origin"]) == 3
        assert len(h["axis_dir"]) == 3
        assert h["depth_mm"] >= 0
        assert isinstance(h["diameters_mm"], list)
        assert len(h["diameters_mm"]) >= 1
        # standard_match present (may be None when match_standards=False)
        assert "standard_match" in h


def test_classify_m3_clearance_matches_m3_standard():
    """The Ø3.4 clearance hole should map to thread_spec 'M3' (closest entry
    by diameter in threads_metric.yaml)."""
    body = _box_with_assorted_holes()
    holes = ClassifyHoles().apply(body, {"match_standards": True}).extras["holes"]
    # find the simple straight hole whose primary diameter is ~3.4
    matches = [
        h for h in holes
        if h["type"] in ("simple", "counterbore", "countersink")
        and any(abs(d - 3.4) < 0.2 for d in h["diameters_mm"])
    ]
    assert matches, (
        "no hole with Ø3.4 found among "
        f"{[(h['type'], h['diameters_mm']) for h in holes]}"
    )
    h = matches[0]
    sm = h["standard_match"]
    assert sm is not None
    assert sm["thread_spec"] == "M3", (
        f"expected M3 match, got {sm}"
    )
    assert sm["confidence"] > 0.5


def test_classify_disables_standard_lookup():
    body = _box_with_assorted_holes()
    holes = ClassifyHoles().apply(body, {"match_standards": False}).extras["holes"]
    assert all(h["standard_match"] is None for h in holes)


def test_classify_empty_box_has_no_holes():
    body = Box().apply(None, {
        "length_mm": 20.0, "width_mm": 20.0, "height_mm": 10.0,
    }).body
    holes = ClassifyHoles().apply(body, {}).extras["holes"]
    assert holes == []


def test_classify_holes_body_present_post():
    spec = ClassifyHoles.spec
    assert spec.category == "inspect"
    assert spec.level == "atomic"
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "body_present" in kinds
