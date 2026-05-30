"""Wave3-C standard-part catalog matching skills — unit tests.

Verifies:
  - match_standard_hole returns M3 / clearance_medium for 3.4mm.
  - match_standard_bearing returns "608" for (8,22,7).
  - match_standard_oring returns AS568-013 for (cs=1.78, id=10.82).
  - identify_fastener_recess classifies a real hex floor as hex_socket size '3'.
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.identify_fastener_recess import (
    IdentifyFastenerRecess,
)
from phone_designer.skills.inspect.match_standard_bearing import (
    MatchStandardBearing,
)
from phone_designer.skills.inspect.match_standard_hole import MatchStandardHole
from phone_designer.skills.inspect.match_standard_oring import (
    MatchStandardORing,
)
from phone_designer.skills.modify_pocket.hex_socket_recess import HexSocketRecess


def _box(L: float = 40.0, W: float = 40.0, H: float = 10.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ──────────────────────────────────────────────────────────────────────────────
# match_standard_hole


def test_match_standard_hole_m3_clearance_medium():
    body = _box()
    r = MatchStandardHole().apply(
        body, {"hole_diameter_mm": 3.4, "fit_kind": "clearance"},
    )
    matches = r.extras["matches"]
    assert matches, "no matches returned"
    assert len(matches) <= 3
    top = matches[0]
    assert top["thread_spec"] == "M3"
    assert top["fit"] == "medium"
    assert top["catalog"] == "metric"
    assert top["catalog_diameter_mm"] == 3.4
    assert top["deviation_mm"] < 1e-6
    assert top["confidence"] > 0.99
    # body untouched
    assert r.body is body


def test_match_standard_hole_m4_tap():
    body = _box()
    r = MatchStandardHole().apply(
        body, {"hole_diameter_mm": 3.3, "fit_kind": "tap"},
    )
    top = r.extras["matches"][0]
    # tap_drill for M4 is 3.3
    assert top["thread_spec"] == "M4"
    assert top["fit"] == "tap"
    assert top["deviation_mm"] < 1e-6


def test_match_standard_hole_auto_returns_three_or_fewer():
    body = _box()
    r = MatchStandardHole().apply(
        body, {"hole_diameter_mm": 5.5, "fit_kind": "auto"},
    )
    matches = r.extras["matches"]
    assert 1 <= len(matches) <= 3
    # All have required keys.
    for m in matches:
        for k in (
            "thread_spec", "fit", "catalog",
            "catalog_diameter_mm", "deviation_mm", "confidence",
        ):
            assert k in m, f"missing {k}"


# ──────────────────────────────────────────────────────────────────────────────
# match_standard_bearing


def test_match_standard_bearing_608():
    body = _box()
    r = MatchStandardBearing().apply(
        body, {"outer_d_mm": 22, "bore_d_mm": 8, "width_mm": 7},
    )
    matches = r.extras["matches"]
    assert matches
    top = matches[0]
    assert top["bearing_spec"] == "608"
    assert top["catalog_dims"]["outer_d_mm"] == 22
    assert top["catalog_dims"]["bore_id_mm"] == 8
    assert top["catalog_dims"]["width_mm"] == 7
    assert top["deviation"]["total_mm"] < 1e-6
    assert top["confidence"] > 0.99


def test_match_standard_bearing_partial_query():
    """Only outer + bore — width missing should still match 608."""
    body = _box()
    r = MatchStandardBearing().apply(
        body, {"outer_d_mm": 22, "bore_d_mm": 8},
    )
    top = r.extras["matches"][0]
    assert top["bearing_spec"] == "608"
    assert "width_mm" not in top["deviation"]
    assert top["deviation"]["total_mm"] < 1e-6


def test_match_standard_bearing_close_miss_ranks_top():
    """An approximate measurement still picks the right bearing."""
    body = _box()
    r = MatchStandardBearing().apply(
        body, {"outer_d_mm": 22.1, "bore_d_mm": 7.95, "width_mm": 7.02},
    )
    assert r.extras["matches"][0]["bearing_spec"] == "608"


# ──────────────────────────────────────────────────────────────────────────────
# match_standard_oring


def test_match_standard_oring_dash013():
    body = _box()
    r = MatchStandardORing().apply(body, {"cs_mm": 1.78, "id_mm": 10.82})
    matches = r.extras["matches"]
    assert matches
    top = matches[0]
    assert top["dash"] == "AS568-013"
    assert top["catalog_cs"] == 1.78
    assert top["catalog_id"] == 10.82
    assert top["deviation"]["total_mm"] < 1e-6


def test_match_standard_oring_returns_three_neighbours():
    body = _box()
    r = MatchStandardORing().apply(body, {"cs_mm": 1.78, "id_mm": 11.0})
    matches = r.extras["matches"]
    assert 1 <= len(matches) <= 3
    # Closest by id_mm should be AS568-013 (10.82) or 012 (9.25) or 014 (12.42).
    top_dashes = {m["dash"] for m in matches}
    assert "AS568-013" in top_dashes


# ──────────────────────────────────────────────────────────────────────────────
# identify_fastener_recess


def test_identify_fastener_recess_hex_floor():
    body = _box()
    body = HexSocketRecess().apply(
        body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "size": "3",
            "depth_mm": 2.5,
        },
    ).body

    r = IdentifyFastenerRecess().apply(
        body,
        {"face_selector": {"kind": "faces_by_area", "min": 5.0, "max": 10.0}},
    )
    match = r.extras["match"]
    assert match["recess_kind"] == "hex_socket"
    assert match["size"] == "3"
    assert match["confidence"] > 0.9
    # body untouched
    assert r.body is body


# ──────────────────────────────────────────────────────────────────────────────
# Spec sanity


def test_match_skills_declare_body_present_post_condition():
    """All 4 inspect-only skills must use body_present post-condition."""
    for cls in (
        MatchStandardHole, MatchStandardBearing, MatchStandardORing,
        IdentifyFastenerRecess,
    ):
        spec = cls.spec
        assert spec.post_conditions, f"{spec.name}: post_conditions empty"
        kinds = {pc.kind for pc in spec.post_conditions}
        assert kinds == {"body_present"}, (
            f"{spec.name}: expected exactly body_present, got {kinds}"
        )


def test_catalogs_load_under_match_skills():
    """All four target catalogs are reachable from the skill _load helper."""
    from phone_designer.skills.inspect.match_standard_hole import _load
    metric = _load("standards", "threads_metric")
    assert metric is not None
    assert "M3" in metric["threads"]

    bearings = _load("standards", "bearings_metric")
    assert bearings is not None
    assert "608" in bearings["bearings"]

    rings = _load("standards", "o_rings_as568")
    assert rings is not None
    assert "AS568-013" in rings["o_rings"]

    drivers = _load("standards", "drivers")
    assert drivers is not None
    assert "hex_socket" in drivers
    assert "3" in drivers["hex_socket"]
