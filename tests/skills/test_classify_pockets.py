"""classify_pockets — pocket family classifier.

Build a box with three distinct pocket-family features and assert the
classifier finds all three with the right types:

    1. Blind hole — a finite-depth drilled cylinder.
    2. Through hole — drilled cylinder that exits the opposite face.
    3. Stepped pocket — counterbore-like: a wide shallow pocket with a
       narrower deeper pocket inside it (two distinct planar floors).
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.classify_pockets import ClassifyPockets
from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket
from phone_designer.skills.modify_pocket.hole import Hole


# ──────────────────────────────────────────────────────────────────────────────
# Fixture builder


def _box_with_three_pockets():
    """Return a 60×40×12 box carrying a blind hole, a through hole, and a
    stepped (counterbore-like) pocket — spatially separated so each one
    forms its own connected component on the face graph.
    """
    body = Box().apply(None, {
        "length_mm": 60.0,
        "width_mm": 40.0,
        "height_mm": 12.0,
    }).body

    # 1) blind hole: enters from +Z top face, depth 5mm (does not exit)
    body = Hole().apply(body, {
        "position": (-20.0, 0.0, 12.0),
        "diameter_mm": 4.0,
        "depth_mm": 5.0,
        "direction": "-Z",
    }).body

    # 2) through hole: enters from +Z, deep enough to exit out the bottom
    body = Hole().apply(body, {
        "position": (0.0, 0.0, 12.0),
        "diameter_mm": 4.0,
        "depth_mm": None,        # through-hole convention in Hole skill
        "direction": "-Z",
    }).body

    # 3) stepped pocket: a 10x10 wide shallow pocket on top + a 5x5 deeper
    #    pocket inside it (two planar floor levels along Z).
    body = ExtrudePocket().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {
            "kind": "rectangle",
            "length_mm": 10.0,
            "width_mm": 10.0,
            "center_x_mm": 20.0,
            "center_y_mm": 0.0,
        },
        "depth_mm": 2.0,
    }).body
    body = ExtrudePocket().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {
            "kind": "rectangle",
            "length_mm": 5.0,
            "width_mm": 5.0,
            "center_x_mm": 20.0,
            "center_y_mm": 0.0,
        },
        "depth_mm": 4.0,
    }).body
    return body


# ──────────────────────────────────────────────────────────────────────────────
# Tests


def test_classify_finds_three_pockets():
    """classify_pockets returns at least three entries on the fixture body."""
    body = _box_with_three_pockets()
    r = ClassifyPockets().apply(body, {})
    pockets = r.extras["pockets"]
    assert len(pockets) >= 3, (
        f"expected ≥3 pockets, got {len(pockets)}: "
        f"{[p['type'] for p in pockets]}"
    )
    # body unchanged
    assert r.body is body


def test_classify_types_blind_through_stepped():
    """Among the detected pockets we must see one blind, one through, and
    one stepped."""
    body = _box_with_three_pockets()
    r = ClassifyPockets().apply(body, {})
    types = {p["type"] for p in r.extras["pockets"]}
    assert "blind" in types, f"no blind hole detected. types={types}"
    assert "through" in types, f"no through hole detected. types={types}"
    assert "stepped" in types, f"no stepped pocket detected. types={types}"


def test_classify_each_pocket_has_geometry():
    """Every pocket descriptor must carry axis, top/bottom diameter, depth and
    face_indices."""
    body = _box_with_three_pockets()
    pockets = ClassifyPockets().apply(body, {}).extras["pockets"]
    for p in pockets:
        assert "id" in p
        assert p["type"] in ("blind", "through", "stepped", "conical", "spherical")
        assert len(p["axis_origin"]) == 3
        assert len(p["axis_dir"]) == 3
        assert p["depth_mm"] >= 0
        assert p["top_d_mm"] >= 0
        assert p["bottom_d_mm"] >= 0
        assert len(p["face_indices"]) >= 1


def test_classify_empty_box_has_no_pockets():
    """A plain box has no pockets — classifier returns an empty list."""
    body = Box().apply(None, {
        "length_mm": 20.0, "width_mm": 20.0, "height_mm": 10.0,
    }).body
    pockets = ClassifyPockets().apply(body, {}).extras["pockets"]
    assert pockets == []


def test_classify_min_depth_filters_shallow():
    """min_depth_mm above any pocket depth filters all out."""
    body = _box_with_three_pockets()
    pockets = ClassifyPockets().apply(body, {"min_depth_mm": 1000.0}).extras["pockets"]
    assert pockets == []


def test_classify_pockets_body_present_post():
    """Skill registers post body_present (read-only)."""
    spec = ClassifyPockets.spec
    assert spec.category == "inspect"
    assert spec.level == "atomic"
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "body_present" in kinds
