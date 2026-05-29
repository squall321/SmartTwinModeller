"""Inspect deepening — hole_to_hole_distance / edge_to_edge_distance /
datum_plane_assign / tolerance_stack / section_at_centroid.

All read-only; body is unchanged after every call (post_condition body_present).
"""
from __future__ import annotations

import math

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.datum_plane_assign import DatumPlaneAssign
from phone_designer.skills.inspect.edge_to_edge_distance import EdgeToEdgeDistance
from phone_designer.skills.inspect.hole_to_hole_distance import HoleToHoleDistance
from phone_designer.skills.inspect.section_at_centroid import SectionAtCentroid
from phone_designer.skills.inspect.tolerance_stack import ToleranceStack
from phone_designer.skills.modify_pocket.hole import Hole


# ──────────────────────────────────────────────────────────────────────────────
# hole_to_hole_distance


def test_hole_to_hole_distance_two_parallel_holes():
    """Two parallel −Z holes 10 mm apart in X → pair distance ≈ 10."""
    box = Box().apply(None, {"length_mm": 40, "width_mm": 20, "height_mm": 10}).body
    h1 = Hole().apply(box, {
        "position": (-5.0, 0.0, 10.0),
        "diameter_mm": 3.0,
        "depth_mm": 5.0,
        "direction": "-Z",
    }).body
    h2 = Hole().apply(h1, {
        "position": (5.0, 0.0, 10.0),
        "diameter_mm": 3.0,
        "depth_mm": 5.0,
        "direction": "-Z",
    }).body

    r = HoleToHoleDistance().apply(h2, {})
    assert r.extras["hole_count"] == 2
    distances = r.extras["hole_distances"]
    # Only one unordered pair (0,1)
    assert len(distances) == 1
    i, j, d = distances[0]
    assert i == 0 and j == 1
    assert abs(d - 10.0) < 0.05
    # parallel-axis classification
    assert r.extras["pair_details"][0]["kind"] == "parallel"
    # body unchanged
    assert r.body is h2


def test_hole_to_hole_distance_no_holes():
    """Plain box → 0 cylindrical faces, empty distance list."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = HoleToHoleDistance().apply(box, {})
    assert r.extras["hole_count"] == 0
    assert r.extras["hole_distances"] == []
    assert r.extras["pair_details"] == []


# ──────────────────────────────────────────────────────────────────────────────
# edge_to_edge_distance


def test_edge_to_edge_distance_parallel_z_edges_on_box():
    """Two Z-edges of a 20×20×10 box that share an X face → distance ≈ 20 mm.

    Selector_a = Z-edges with center near (-10, -10) corner.
    Selector_b = Z-edges with center near (+10, +10) corner.
    Min distance = sqrt(20² + 20²) ≈ 28.284.
    """
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = EdgeToEdgeDistance().apply(box, {
        "edge_selector_a": {
            "kind": "edges_by_position",
            "bbox": {"min": (-11.0, -11.0, 0.0), "max": (-9.0, -9.0, 10.0)},
        },
        "edge_selector_b": {
            "kind": "edges_by_position",
            "bbox": {"min": (9.0, 9.0, 0.0), "max": (11.0, 11.0, 10.0)},
        },
    })
    expected = math.sqrt(20.0 ** 2 + 20.0 ** 2)
    assert abs(r.extras["distance_mm"] - expected) < 0.05
    assert r.extras["edge_count_a"] >= 1
    assert r.extras["edge_count_b"] >= 1
    assert r.body is box


def test_edge_to_edge_distance_axis_aligned_z_edges():
    """All 4 Z-edges on a 20×20×10 box; selector_a == selector_b on Z-edges →
    distance 0 (an edge matches itself in the cross-pair)."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = EdgeToEdgeDistance().apply(box, {
        "edge_selector_a": {"kind": "axis_aligned_edges", "axis": "Z"},
        "edge_selector_b": {"kind": "axis_aligned_edges", "axis": "Z"},
    })
    # Same set → trivial self-match gives 0
    assert r.extras["distance_mm"] < 0.01
    assert r.extras["edge_count_a"] == 4
    assert r.extras["edge_count_b"] == 4


# ──────────────────────────────────────────────────────────────────────────────
# datum_plane_assign


def test_datum_plane_assign_three_faces():
    """A/B/C → top / +X face / +Y face on a 20×20×10 box."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = DatumPlaneAssign().apply(box, {
        "assignments": [
            {"label": "A", "face_selector": {"kind": "face_named", "name": "top"}},
            {"label": "B", "face_selector": {"kind": "faces_by_normal",
                                              "direction": (1.0, 0.0, 0.0),
                                              "tol_deg": 5.0}},
            {"label": "C", "face_selector": {"kind": "faces_by_normal",
                                              "direction": (0.0, 1.0, 0.0),
                                              "tol_deg": 5.0}},
        ]
    })
    assert r.extras["datum_count"] == 3
    datums = r.extras["datums"]
    assert "A" in datums and "B" in datums and "C" in datums
    # datum A is the top face → +Z normal
    a_normal = datums["A"]["normal"]
    assert a_normal[2] > 0.9
    assert datums["A"]["is_planar"] is True
    # datum A area ≈ 20*20 = 400
    assert abs(datums["A"]["area_mm2"] - 400.0) < 0.5
    # body unchanged
    assert r.body is box


def test_datum_plane_assign_missing_selector_warns():
    """Skip + warn when an entry lacks a selector or label."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = DatumPlaneAssign().apply(box, {
        "assignments": [
            {"label": "A"},  # missing selector
            {"face_selector": {"kind": "face_named", "name": "top"}},  # missing label
        ]
    })
    assert r.extras["datum_count"] == 0
    assert len(r.extras["warnings"]) == 2


# ──────────────────────────────────────────────────────────────────────────────
# tolerance_stack


def test_tolerance_stack_simple_chain():
    """3-link stack: 10±0.1, 20±0.2, 30±0.3 → nominal 60, worst ±0.6, RSS ≈ 0.374."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = ToleranceStack().apply(box, {
        "dimensions": [
            {"name": "L1", "nominal": 10.0, "plus": 0.1, "minus": 0.1},
            {"name": "L2", "nominal": 20.0, "plus": 0.2, "minus": 0.2},
            {"name": "L3", "nominal": 30.0, "plus": 0.3, "minus": 0.3},
        ]
    })
    assert r.extras["dim_count"] == 3
    assert abs(r.extras["nominal_total"] - 60.0) < 1e-6
    assert abs(r.extras["worst_case_max"] - 60.6) < 1e-6
    assert abs(r.extras["worst_case_min"] - 59.4) < 1e-6
    assert abs(r.extras["worst_case_plus"] - 0.6) < 1e-6
    assert abs(r.extras["worst_case_minus"] - 0.6) < 1e-6
    expected_rss = math.sqrt(0.01 + 0.04 + 0.09)
    assert abs(r.extras["rss_plus"] - expected_rss) < 1e-5
    assert abs(r.extras["rss_minus"] - expected_rss) < 1e-5
    # stack tuples preserved
    assert r.extras["stack"][0] == ("L1", 10.0, 0.1, 0.1)
    assert r.body is box


def test_tolerance_stack_asymmetric_and_empty():
    """Asymmetric ± and 0-entry chain edge cases."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r1 = ToleranceStack().apply(box, {
        "dimensions": [
            {"name": "A", "nominal": 5.0, "plus": 0.3, "minus": 0.1},
        ]
    })
    assert abs(r1.extras["worst_case_max"] - 5.3) < 1e-6
    assert abs(r1.extras["worst_case_min"] - 4.9) < 1e-6

    r2 = ToleranceStack().apply(box, {"dimensions": []})
    assert r2.extras["dim_count"] == 0
    assert r2.extras["nominal_total"] == 0.0
    assert r2.extras["worst_case_max"] == 0.0


# ──────────────────────────────────────────────────────────────────────────────
# section_at_centroid


def test_section_at_centroid_auto_longest_axis_x():
    """40×20×10 box → longest axis = X → plane normal +X through centroid."""
    box = Box().apply(None, {"length_mm": 40, "width_mm": 20, "height_mm": 10}).body
    r = SectionAtCentroid().apply(box, {"axis": "auto"})
    assert r.extras["axis"] == "X"
    # Section through centroid (0,0,5) with +X normal → rectangle 20×10
    pls = r.extras["polylines"]
    assert r.extras["edge_count"] == 4
    assert len(pls) == 4
    # perimeter ≈ 2*(20+10) = 60
    assert abs(r.extras["total_length_mm"] - 60.0) < 0.5
    # centroid roughly (0,0,5) for box at origin
    centroid = r.extras["centroid"]
    assert abs(centroid[0]) < 0.01
    assert abs(centroid[1]) < 0.01
    assert abs(centroid[2] - 5.0) < 0.01
    assert r.body is box


def test_section_at_centroid_explicit_z_axis():
    """20×20×10 box, axis=Z → plane normal +Z through centroid (0,0,5).
    Cross-section is 20×20 square, perimeter ≈ 80."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = SectionAtCentroid().apply(box, {"axis": "Z"})
    assert r.extras["axis"] == "Z"
    assert r.extras["edge_count"] == 4
    assert abs(r.extras["total_length_mm"] - 80.0) < 0.5
    plane_normal = r.extras["plane_normal"]
    assert plane_normal == [0.0, 0.0, 1.0]
