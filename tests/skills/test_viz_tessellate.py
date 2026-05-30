"""Tests for Wave3-C tessellation / visualization skills:

    - face_tessellate
    - edge_polyline_extract
    - curvature_at_point
    - normal_map_compute
    - color_by_property

All five are read-only inspect skills (post: ``body_present``). The fixtures
are a closed 20x20x10 box and a 10 mm-radius sphere; both produce known mesh
counts and curvature values.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.sphere import Sphere
from phone_designer.skills.inspect.color_by_property import ColorByProperty
from phone_designer.skills.inspect.curvature_at_point import CurvatureAtPoint
from phone_designer.skills.inspect.edge_polyline_extract import EdgePolylineExtract
from phone_designer.skills.inspect.face_tessellate import FaceTessellate
from phone_designer.skills.inspect.normal_map_compute import NormalMapCompute


# -------------------------- fixtures ----------------------------------------

def _box(L=20.0, W=20.0, H=10.0):
    return Box().apply(None,
                       {"length_mm": L, "width_mm": W, "height_mm": H}).body


def _sphere(r=10.0):
    return Sphere().apply(None, {"radius_mm": r}).body


# -------------------------- face_tessellate ---------------------------------

def test_face_tessellate_box_top_face_produces_triangles():
    """Selecting the +Z face of a box yields ≥ 2 triangles forming a 20x20 quad."""
    box = _box()
    r = FaceTessellate().apply(box, {
        "face_selector": {
            "kind": "faces_by_normal",
            "direction": (0.0, 0.0, 1.0),
            "tol_deg": 5.0,
        },
        "deflection_mm": 0.1,
    })
    mesh = r.extras["mesh"]
    assert mesh["face_count"] >= 1
    # The +Z face must have at least 2 triangles (planar 20×20).
    first = mesh["faces"][0]
    assert len(first["vertices"]) >= 3
    assert len(first["triangles"]) >= 2
    assert len(first["normals"]) == len(first["vertices"])

    # Every triangle index must be in-range.
    nv = len(first["vertices"])
    for (a, b, c) in first["triangles"]:
        assert 0 <= a < nv
        assert 0 <= b < nv
        assert 0 <= c < nv

    # The +Z planar face's normals should all point ~ +Z.
    for n in first["normals"]:
        assert n[2] > 0.9, f"+Z face normal expected +Z, got {n}"

    # Body unchanged.
    assert r.body is box


def test_face_tessellate_deflection_finer_means_more_triangles_on_sphere():
    sphere = _sphere(10.0)
    sel = {
        "kind": "first_n",
        "inner": {"kind": "faces_by_area"},
        "n": 1,
    }
    coarse = FaceTessellate().apply(sphere, {
        "face_selector": sel,
        "deflection_mm": 1.0,
    }).extras["mesh"]
    fine = FaceTessellate().apply(sphere, {
        "face_selector": sel,
        "deflection_mm": 0.05,
    }).extras["mesh"]
    assert fine["triangle_count_total"] > coarse["triangle_count_total"]


def test_face_tessellate_none_body_raises():
    with pytest.raises(ValueError):
        FaceTessellate().apply(None, {
            "face_selector": {"kind": "faces_by_area"},
            "deflection_mm": 0.1,
        })


# -------------------------- edge_polyline_extract ---------------------------

def test_edge_polyline_extract_axis_aligned_z_edges_of_box():
    """Box has 4 vertical (Z-aligned) edges, each 10 mm long. Sample 5 segs."""
    box = _box()
    r = EdgePolylineExtract().apply(box, {
        "edge_selector": {"kind": "axis_aligned_edges", "axis": "Z"},
        "segments_per_edge": 5,
    })
    polylines = r.extras["polylines"]
    assert r.extras["edge_count"] == 4
    assert len(polylines) == 4
    for pl in polylines:
        # segments_per_edge + 1 = 6 points
        assert len(pl) == 6
        # Endpoints must differ by exactly H=10 in Z.
        z0 = pl[0][2]
        z1 = pl[-1][2]
        assert abs(abs(z1 - z0) - 10.0) < 1e-3

        # Successive points should be monotone in Z (uniform sampling).
        zs = [p[2] for p in pl]
        assert zs == sorted(zs) or zs == sorted(zs, reverse=True)


def test_edge_polyline_extract_segments_count_obeyed():
    box = _box()
    for n in (1, 4, 25):
        r = EdgePolylineExtract().apply(box, {
            "edge_selector": {"kind": "axis_aligned_edges", "axis": "X"},
            "segments_per_edge": n,
        })
        for pl in r.extras["polylines"]:
            assert len(pl) == n + 1


def test_edge_polyline_extract_empty_selector_yields_empty_list():
    box = _box()
    r = EdgePolylineExtract().apply(box, {
        "edge_selector": {
            "kind": "edges_by_length",
            "min": 1e9, "max": 1e10,
        },
        "segments_per_edge": 10,
    })
    assert r.extras["polylines"] == []
    assert r.extras["edge_count"] == 0


# -------------------------- curvature_at_point ------------------------------

def test_curvature_at_point_on_sphere_returns_1_over_r_squared():
    """For a sphere of radius R, Gaussian K = 1/R², mean H = ±1/R."""
    R = 10.0
    sphere = _sphere(R)
    r = CurvatureAtPoint().apply(sphere, {
        "face_selector": {"kind": "faces_by_area"},
        "u": 0.5,
        "v": 0.5,
    })
    c = r.extras["curvature"]
    assert c["is_defined"] is True

    expected_K = 1.0 / (R * R)
    assert abs(c["gaussian"] - expected_K) < 1e-3
    # Mean curvature on a sphere has constant magnitude 1/R (sign = orientation).
    assert abs(abs(c["mean"]) - 1.0 / R) < 1e-3
    # principal curvatures k1 == k2 == 1/R on a sphere.
    assert abs(abs(c["k1"]) - 1.0 / R) < 1e-3
    assert abs(abs(c["k2"]) - 1.0 / R) < 1e-3


def test_curvature_at_point_on_planar_face_is_zero():
    box = _box()
    r = CurvatureAtPoint().apply(box, {
        "face_selector": {
            "kind": "faces_by_normal",
            "direction": (0.0, 0.0, 1.0),
            "tol_deg": 5.0,
        },
        "u": 0.5,
        "v": 0.5,
    })
    c = r.extras["curvature"]
    assert c["is_defined"] is True
    assert abs(c["gaussian"]) < 1e-6
    assert abs(c["mean"])     < 1e-6


def test_curvature_at_point_no_match_returns_undefined():
    box = _box()
    r = CurvatureAtPoint().apply(box, {
        "face_selector": {"kind": "tagged", "tag": "NO_SUCH_TAG"},
        "u": 0.5,
        "v": 0.5,
    })
    c = r.extras["curvature"]
    assert c["is_defined"] is False
    assert r.extras["face_count_matched"] == 0


# -------------------------- normal_map_compute ------------------------------

def test_normal_map_compute_box_has_axis_aligned_normals():
    """A box's 6 faces all have axis-aligned outward normals."""
    box = _box()
    r = NormalMapCompute().apply(box, {"samples_per_face": 4})
    nm = r.extras["normal_map"]
    assert r.extras["face_count"] == 6
    assert len(nm) == 6

    # 4 → grid side ceil(sqrt(4)) = 2 → 2*2 = 4 samples per face.
    for face_entry in nm:
        assert len(face_entry["samples"]) == 4
        for s in face_entry["samples"]:
            n = s["normal"]
            # |n| ≈ 1
            assert abs(math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) - 1.0) < 1e-3
            # Exactly one component is ±1, the other two are ~0.
            big = max(abs(n[0]), abs(n[1]), abs(n[2]))
            assert abs(big - 1.0) < 1e-3
            small = sum(1 for c in n if abs(c) < 1e-3)
            assert small == 2


def test_normal_map_compute_sphere_normals_point_radially():
    """For each sample on the sphere, normal direction ≈ (point − center) / R."""
    R = 8.0
    sphere = _sphere(R)
    r = NormalMapCompute().apply(sphere, {"samples_per_face": 16})
    nm = r.extras["normal_map"]
    assert len(nm) >= 1

    for face_entry in nm:
        for s in face_entry["samples"]:
            p = s["point"]
            n = s["normal"]
            r_mag = math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2)
            if r_mag < 1e-6:
                continue
            radial = (p[0] / r_mag, p[1] / r_mag, p[2] / r_mag)
            dot = n[0] * radial[0] + n[1] * radial[1] + n[2] * radial[2]
            # Either outward or inward, but parallel to radius.
            assert abs(abs(dot) - 1.0) < 5e-2


def test_normal_map_compute_none_body_raises():
    with pytest.raises(ValueError):
        NormalMapCompute().apply(None, {"samples_per_face": 4})


# -------------------------- color_by_property -------------------------------

def test_color_by_property_draft_angle_box_has_two_distinct_groups():
    """Box: 2 horizontal faces (draft=0°) + 4 vertical (draft=90°)."""
    box = _box()
    r = ColorByProperty().apply(box, {
        "property": "draft_angle",
        "colormap": "viridis",
    })
    fcs = r.extras["face_colors"]
    assert r.extras["face_count"] == 6
    assert len(fcs) == 6

    # Each color is a 3-tuple in [0, 1].
    for entry in fcs:
        rgb = entry["color_rgb"]
        assert len(rgb) == 3
        for c in rgb:
            assert 0.0 <= c <= 1.0

    # Values: 0° for top/bottom (2 of them) and 90° for the 4 side faces.
    vals = sorted(round(e["value"], 2) for e in fcs)
    horizontal = [v for v in vals if v < 1.0]
    vertical = [v for v in vals if v > 89.0]
    assert len(horizontal) == 2
    assert len(vertical) == 4


def test_color_by_property_wall_thickness_box_is_finite():
    """For a 20x20x10 box every face center should ray-hit the opposing face."""
    box = _box(20.0, 20.0, 10.0)
    r = ColorByProperty().apply(box, {
        "property": "wall_thickness",
        "colormap": "plasma",
    })
    for entry in r.extras["face_colors"]:
        v = entry["value"]
        # 4 faces see thickness 20, 2 faces see thickness 10.
        assert math.isfinite(v), f"face {entry['face_idx']} value was {v}"
        assert 9.5 <= v <= 20.5


def test_color_by_property_curvature_box_is_zero_for_planar():
    box = _box()
    r = ColorByProperty().apply(box, {
        "property": "curvature",
        "colormap": "viridis",
    })
    for entry in r.extras["face_colors"]:
        assert abs(entry["value"]) < 1e-6


def test_color_by_property_stress_returns_uniform_zero():
    """Stress is a placeholder field — every face value should be 0."""
    box = _box()
    r = ColorByProperty().apply(box, {
        "property": "stress",
        "colormap": "coolwarm",
    })
    for entry in r.extras["face_colors"]:
        assert entry["value"] == 0


def test_color_by_property_unknown_colormap_falls_back_to_viridis():
    """Unknown colormap names must not raise — they fall back gracefully."""
    box = _box()
    r = ColorByProperty().apply(box, {
        "property": "draft_angle",
        "colormap": "fnord_does_not_exist",
    })
    assert len(r.extras["face_colors"]) == 6


# -------------------------- spec sanity -------------------------------------

def test_viz_tessellate_specs_registered():
    for cls, name in [
        (FaceTessellate,      "face_tessellate"),
        (EdgePolylineExtract, "edge_polyline_extract"),
        (CurvatureAtPoint,    "curvature_at_point"),
        (NormalMapCompute,    "normal_map_compute"),
        (ColorByProperty,     "color_by_property"),
    ]:
        assert cls.spec.name == name
        assert cls.spec.category == "inspect"
        assert cls.spec.level == "atomic"
        assert any(pc.kind == "body_present" for pc in cls.spec.post_conditions)
