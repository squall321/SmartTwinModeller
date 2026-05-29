"""GD&T inspection skills — flatness / perpendicularity / parallelism /
cylindricity / circularity / position.

All read-only. body unchanged after every call (post_condition body_present).
"""
from __future__ import annotations

import math

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.cylinder import Cylinder
from phone_designer.skills.inspect.gdt_circularity import GdtCircularity
from phone_designer.skills.inspect.gdt_cylindricity import GdtCylindricity
from phone_designer.skills.inspect.gdt_flatness import GdtFlatness
from phone_designer.skills.inspect.gdt_parallelism import GdtParallelism
from phone_designer.skills.inspect.gdt_perpendicularity import GdtPerpendicularity
from phone_designer.skills.inspect.gdt_position import GdtPosition
from phone_designer.skills.modify_pocket.hole import Hole


# ──────────────────────────────────────────────────────────────────────────────
# gdt_flatness


def test_gdt_flatness_box_top_face_is_flat():
    """Box top face is a true plane → deviation ≈ 0, pass=True."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = GdtFlatness().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "tolerance_mm": 0.01,
        "samples": 16,
    })
    f = r.extras["flatness"]
    assert f["deviation_mm"] < 1e-4
    assert f["pass"] is True
    assert f["tolerance_mm"] == 0.01
    assert f["sample_count"] >= 16
    # plane normal of box top is +Z (sign may differ).
    nz = f["plane_normal"][2]
    assert abs(abs(nz) - 1.0) < 1e-3
    # body unchanged
    assert r.body is box


def test_gdt_flatness_sphere_face_fails():
    """A sphere surface is far from planar → big deviation, pass=False."""
    from build123d import Sphere
    s = Sphere(10.0)
    r = GdtFlatness().apply(s, {
        "face_selector": {"kind": "faces_by_area", "min": 0.0},
        "tolerance_mm": 0.1,
        "samples": 25,
    })
    f = r.extras["flatness"]
    assert f["deviation_mm"] > 1.0
    assert f["pass"] is False
    assert r.body is s


# ──────────────────────────────────────────────────────────────────────────────
# gdt_perpendicularity


def test_gdt_perpendicularity_box_top_vs_side_is_perpendicular():
    """Box top (+Z) vs side (+X) → exactly 90°, pass=True."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = GdtPerpendicularity().apply(box, {
        "face_selector_a": {"kind": "face_named", "name": "top"},
        "face_selector_b": {
            "kind": "faces_by_normal", "direction": (1.0, 0.0, 0.0), "tol_deg": 1.0,
        },
        "tolerance_deg": 0.1,
    })
    p = r.extras["perpendicularity"]
    assert abs(p["angle_deg"] - 90.0) < 1e-3
    assert p["deviation_deg"] < 1e-3
    assert p["pass"] is True
    assert r.body is box


def test_gdt_perpendicularity_two_parallel_faces_fail():
    """Box top vs bottom → 180°, deviation = 90°, pass=False."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = GdtPerpendicularity().apply(box, {
        "face_selector_a": {"kind": "face_named", "name": "top"},
        "face_selector_b": {"kind": "face_named", "name": "bottom"},
        "tolerance_deg": 1.0,
    })
    p = r.extras["perpendicularity"]
    assert p["pass"] is False
    assert p["deviation_deg"] > 80.0


# ──────────────────────────────────────────────────────────────────────────────
# gdt_parallelism


def test_gdt_parallelism_box_top_vs_bottom_parallel():
    """Top (+Z) and bottom (−Z) are anti-parallel → treated as parallel
    (180° → deviation = 0). pass=True."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = GdtParallelism().apply(box, {
        "face_selector_a": {"kind": "face_named", "name": "top"},
        "face_selector_b": {"kind": "face_named", "name": "bottom"},
        "tolerance_deg": 0.1,
    })
    p = r.extras["parallelism"]
    assert p["deviation_deg"] < 1e-3
    assert p["pass"] is True
    assert r.body is box


def test_gdt_parallelism_top_vs_side_fails():
    """Top vs side face → 90° apart → not parallel, pass=False."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = GdtParallelism().apply(box, {
        "face_selector_a": {"kind": "face_named", "name": "top"},
        "face_selector_b": {
            "kind": "faces_by_normal", "direction": (1.0, 0.0, 0.0), "tol_deg": 1.0,
        },
        "tolerance_deg": 1.0,
    })
    p = r.extras["parallelism"]
    assert p["pass"] is False
    assert p["deviation_deg"] > 80.0


# ──────────────────────────────────────────────────────────────────────────────
# gdt_cylindricity


def test_gdt_cylindricity_perfect_cylinder_passes():
    """Cylinder side face → analytic radius, deviation ≈ 0, pass=True."""
    cyl = Cylinder().apply(None, {
        "radius_mm": 5.0, "height_mm": 20.0,
    }).body
    r = GdtCylindricity().apply(cyl, {
        # Pick the curved side face (cylindrical, largest area = 2πrh).
        "face_selector": {
            "kind": "largest_n", "n": 1, "sort_by": "measure",
            "inner": {"kind": "faces_by_area", "min": 50.0, "max": 1e6},
        },
        "tolerance_mm": 0.01,
        "samples": 36,
    })
    c = r.extras["cylindricity"]
    assert c["deviation_mm"] < 1e-3
    assert c["pass"] is True
    assert abs(c["fit_radius_mm"] - 5.0) < 1e-3
    assert r.body is cyl


def test_gdt_cylindricity_planar_face_fails():
    """A box face is not a cylinder — point-fit deviation huge, pass=False."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = GdtCylindricity().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "tolerance_mm": 0.1,
        "samples": 25,
    })
    c = r.extras["cylindricity"]
    # A planar 20×20 face fit to a cylinder will have very large radial spread.
    assert c["deviation_mm"] > 1.0
    assert c["pass"] is False


# ──────────────────────────────────────────────────────────────────────────────
# gdt_circularity


def test_gdt_circularity_cylinder_circular_edge_passes():
    """Cylinder top edge is an exact circle → deviation ≈ 0, pass=True."""
    cyl = Cylinder().apply(None, {
        "radius_mm": 5.0, "height_mm": 10.0,
    }).body
    r = GdtCircularity().apply(cyl, {
        # Pick edges by length: a circle of r=5 has circumference ~31.4 mm.
        "edge_selector": {
            "kind": "edges_by_length", "min": 30.0, "max": 33.0,
        },
        "tolerance_mm": 0.01,
        "samples": 36,
    })
    c = r.extras["circularity"]
    assert c["deviation_mm"] < 1e-3
    assert c["pass"] is True
    assert abs(c["fit_radius_mm"] - 5.0) < 1e-3
    assert r.body is cyl


def test_gdt_circularity_straight_edge_not_round():
    """A straight edge on a box — when fit as a circle, the residual is huge."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 10}).body
    r = GdtCircularity().apply(box, {
        # Any X-aligned edge of the box top is a straight line.
        "edge_selector": {"kind": "axis_aligned_edges", "axis": "X"},
        "tolerance_mm": 0.05,
        "samples": 10,
    })
    c = r.extras["circularity"]
    # Straight line → fit circle has near-infinite radius; deviations grow
    # with sample spread. Either deviation huge OR fit_radius huge — either
    # way pass must be False under a tight tolerance.
    assert c["pass"] is False


# ──────────────────────────────────────────────────────────────────────────────
# gdt_position


def test_gdt_position_hole_on_target_passes():
    """Drill a hole at (0,0); target_xy=(0,0) → deviation ≈ 0, pass=True."""
    box = Box().apply(None, {"length_mm": 40, "width_mm": 40, "height_mm": 10}).body
    drilled = Hole().apply(box, {
        "position": (0.0, 0.0, 10.0),
        "diameter_mm": 4.0,
        "depth_mm": 5.0,
        "direction": "-Z",
    }).body
    r = GdtPosition().apply(drilled, {
        # The hole's curved cylindrical face — pick smallest-area face that
        # is cylindrical. Use faces_by_area within the hole's surface area
        # bound. A r=2, depth=5 hole has lateral area = 2*pi*r*h ≈ 62.8 mm².
        "face_selector": {
            "kind": "faces_by_area", "min": 50.0, "max": 80.0,
        },
        "target_xy": (0.0, 0.0),
        "tolerance_mm": 0.05,  # diametral zone 0.05mm
    })
    p = r.extras["position"]
    assert p["deviation_mm"] < 1e-3
    assert p["diametral_zone_mm"] < 1e-3
    assert p["pass"] is True
    assert abs(p["hole_radius_mm"] - 2.0) < 1e-3
    assert r.body is drilled


def test_gdt_position_hole_off_target_fails():
    """Drill a hole at (5,0); target_xy=(0,0) → deviation = 5 mm, pass=False."""
    box = Box().apply(None, {"length_mm": 40, "width_mm": 40, "height_mm": 10}).body
    drilled = Hole().apply(box, {
        "position": (5.0, 0.0, 10.0),
        "diameter_mm": 4.0,
        "depth_mm": 5.0,
        "direction": "-Z",
    }).body
    r = GdtPosition().apply(drilled, {
        "face_selector": {
            "kind": "faces_by_area", "min": 50.0, "max": 80.0,
        },
        "target_xy": (0.0, 0.0),
        "tolerance_mm": 0.5,
    })
    p = r.extras["position"]
    assert abs(p["deviation_mm"] - 5.0) < 1e-3
    assert p["pass"] is False
