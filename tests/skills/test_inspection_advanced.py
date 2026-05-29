"""Advanced inspection skills — curvature_map / mass_properties /
surface_area_by_region / section_multi_plane / hole_alignment_check.

All read-only. body unchanged after every call (post_condition body_present).
"""
from __future__ import annotations

import math

from build123d import Sphere

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.curvature_map import CurvatureMap
from phone_designer.skills.inspect.hole_alignment_check import HoleAlignmentCheck
from phone_designer.skills.inspect.mass_properties import MassProperties
from phone_designer.skills.inspect.section_multi_plane import SectionMultiPlane
from phone_designer.skills.inspect.surface_area_by_region import SurfaceAreaByRegion
from phone_designer.skills.modify_pocket.hole import Hole


# ──────────────────────────────────────────────────────────────────────────────
# curvature_map


def test_curvature_map_sphere_gaussian_equals_inverse_radius_squared():
    """Sphere(R=10) → Gaussian curvature K = 1/R² = 0.01 everywhere."""
    s = Sphere(10.0)
    r = CurvatureMap().apply(s, {
        "face_selector": {"kind": "faces_by_area", "min": 0.0},
        "samples_per_face": 16,
        "curvature_kind": "gaussian",
    })
    assert r.extras["face_count"] == 1
    samples = r.extras["faces"][0]["samples"]
    assert len(samples) >= 16
    expected_K = 1.0 / (10.0 * 10.0)
    for s_dict in samples:
        assert abs(s_dict["gaussian"] - expected_K) < 1e-4
    # body unchanged
    assert r.body is s


def test_curvature_map_planar_face_zero_curvature():
    """Box top face is planar → Gaussian = mean = 0."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = CurvatureMap().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "samples_per_face": 9,
    })
    assert r.extras["face_count"] == 1
    samples = r.extras["faces"][0]["samples"]
    for s_dict in samples:
        assert abs(s_dict["gaussian"]) < 1e-6
        assert abs(s_dict["mean"]) < 1e-6
    assert r.body is box


# ──────────────────────────────────────────────────────────────────────────────
# mass_properties


def test_mass_properties_box_volume_and_centroid():
    """Box(20×30×10) center-aligned + Z-min=0 → volume=6000 mm³,
    centroid=(0, 0, 5). Default density=1 g/cm³ → mass=6 g."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 30, "height_mm": 10}).body
    r = MassProperties().apply(box, {})
    assert abs(r.extras["volume_mm3"] - 6000.0) < 0.01
    cx, cy, cz = r.extras["centroid"]
    assert abs(cx) < 1e-3
    assert abs(cy) < 1e-3
    assert abs(cz - 5.0) < 1e-3
    # density default 1 g/cm³, volume 6 cm³ → 6 g
    assert abs(r.extras["mass_g"] - 6.0) < 1e-3
    # inertia matrix is 3x3
    M = r.extras["inertia_matrix"]
    assert len(M) == 3 and all(len(row) == 3 for row in M)
    # diagonal terms are positive
    diag = r.extras["inertia_diag"]
    assert diag["Ixx"] > 0
    assert diag["Iyy"] > 0
    assert diag["Izz"] > 0
    # body unchanged
    assert r.body is box


def test_mass_properties_density_scales_mass_only():
    """Density=2.7 (aluminum) → mass = volume_cm3 * 2.7. Volume itself
    is unchanged."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = MassProperties().apply(box, {"density_g_per_cm3": 2.7})
    # 1 cm³ = 1000 mm³; box is 1000 mm³ = 1 cm³
    assert abs(r.extras["volume_mm3"] - 1000.0) < 0.01
    assert abs(r.extras["mass_g"] - 2.7) < 1e-3
    assert r.extras["density_g_per_cm3"] == 2.7


# ──────────────────────────────────────────────────────────────────────────────
# surface_area_by_region


def test_surface_area_by_region_top_and_bottom_of_box():
    """Box(20x20x10): top area = bottom area = 400 mm². Total = 800."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = SurfaceAreaByRegion().apply(box, {
        "region_selectors": [
            {"kind": "face_named", "name": "top"},
            {"kind": "face_named", "name": "bottom"},
        ],
    })
    assert r.extras["region_count"] == 2
    assert abs(r.extras["total_area_mm2"] - 800.0) < 0.01
    for reg in r.extras["regions"]:
        assert reg["face_count"] == 1
        assert abs(reg["area_mm2"] - 400.0) < 0.01
        assert reg["error"] is None
    assert r.body is box


def test_surface_area_by_region_empty_selector_zero_area():
    """Selector matching nothing (e.g., out-of-range area) → area=0."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = SurfaceAreaByRegion().apply(box, {
        "region_selectors": [
            {"kind": "faces_by_area", "min": 1e6},  # nothing matches
        ],
    })
    assert r.extras["region_count"] == 1
    assert r.extras["total_area_mm2"] == 0.0
    assert r.extras["regions"][0]["face_count"] == 0
    assert r.extras["regions"][0]["area_mm2"] == 0.0


# ──────────────────────────────────────────────────────────────────────────────
# section_multi_plane


def test_section_multi_plane_two_horizontal_planes_through_box():
    """Two horizontal planes through 20×20×10 box → each yields a square
    perimeter ≈ 80 mm with 4 edges."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = SectionMultiPlane().apply(box, {
        "planes": [
            {"origin": (0.0, 0.0, 3.0), "normal": (0.0, 0.0, 1.0)},
            {"origin": (0.0, 0.0, 7.0), "normal": (0.0, 0.0, 1.0)},
        ],
    })
    assert r.extras["plane_count"] == 2
    for sec in r.extras["sections"]:
        assert sec["edge_count"] == 4
        assert abs(sec["total_length_mm"] - 80.0) < 0.5
        assert sec["error"] is None
    assert r.body is box


def test_section_multi_plane_off_body_returns_empty():
    """A plane far from the body → polylines empty, no crash."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = SectionMultiPlane().apply(box, {
        "planes": [
            {"origin": (0.0, 0.0, 500.0), "normal": (0.0, 0.0, 1.0)},
        ],
    })
    assert r.extras["plane_count"] == 1
    sec = r.extras["sections"][0]
    assert sec["edge_count"] == 0
    assert sec["polylines"] == []


# ──────────────────────────────────────────────────────────────────────────────
# hole_alignment_check


def test_hole_alignment_check_two_coaxial_holes_in_one_group():
    """Drill two through-aligned holes from opposite sides of a thin box.
    Both share the same Z axis line → 1 group, is_group_coaxial=True."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    h1 = Hole().apply(box, {
        "position": (0.0, 0.0, 10.0), "diameter_mm": 2.0, "depth_mm": 4.0,
        "direction": "-Z",
    }).body
    h2 = Hole().apply(h1, {
        "position": (0.0, 0.0, 0.0), "diameter_mm": 2.0, "depth_mm": 4.0,
        "direction": "+Z",
    }).body
    r = HoleAlignmentCheck().apply(h2, {
        "axis_tolerance_deg": 1.0,
        "position_tolerance_mm": 0.5,
    })
    assert r.extras["hole_count"] == 2
    assert r.extras["group_count"] == 1
    g = r.extras["groups"][0]
    assert g["is_group_coaxial"] is True
    assert len(g["holes"]) == 2
    # axis aligned with Z (normalized + sign flipped to + direction)
    ax = g["axis"]
    assert abs(abs(ax[2]) - 1.0) < 0.01
    for h in g["holes"]:
        assert h["is_coaxial"] is True
        assert h["distance_from_axis_mm"] < 0.5
    assert r.body is h2


def test_hole_alignment_check_two_offset_holes_not_coaxial():
    """Two parallel-Z holes at different XY positions → one group (axes
    parallel) but is_group_coaxial=False, distance > tolerance."""
    box = Box().apply(None, {"length_mm": 40, "width_mm": 40, "height_mm": 10}).body
    h1 = Hole().apply(box, {
        "position": (-10.0, 0.0, 10.0), "diameter_mm": 3.0, "depth_mm": 5.0,
        "direction": "-Z",
    }).body
    h2 = Hole().apply(h1, {
        "position": (10.0, 0.0, 10.0), "diameter_mm": 3.0, "depth_mm": 5.0,
        "direction": "-Z",
    }).body
    r = HoleAlignmentCheck().apply(h2, {
        "axis_tolerance_deg": 1.0,
        "position_tolerance_mm": 0.5,
    })
    assert r.extras["hole_count"] == 2
    # Both share Z axis direction → 1 group
    assert r.extras["group_count"] == 1
    g = r.extras["groups"][0]
    assert g["is_group_coaxial"] is False
    # At least one hole reports a large axis distance (≈20 mm apart in X)
    distances = [h["distance_from_axis_mm"] for h in g["holes"]]
    assert max(distances) > 0.5
