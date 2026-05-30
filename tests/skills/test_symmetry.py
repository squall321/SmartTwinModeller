"""Symmetry detection skills — mirror + rotational."""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.cylinder import Cylinder
from phone_designer.skills.inspect.detect_mirror_symmetry import DetectMirrorSymmetry
from phone_designer.skills.inspect.detect_rotational_symmetry import (
    DetectRotationalSymmetry,
)


# -------------------- mirror --------------------

def test_mirror_box_three_axis_planes_high_score():
    """A box centered around its bbox center is mirror-symmetric across XZ, YZ, XY."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 20}).body
    r = DetectMirrorSymmetry().apply(box, {"test_planes": ["XZ", "YZ", "XY"]})
    planes = r.extras["mirror_planes"]
    assert len(planes) == 3
    for p in planes:
        assert p["symmetry_score"] > 0.9


def test_mirror_box_auto_finds_high_score_planes():
    """auto mode (PCA) should still surface near-perfect mirror planes for a box."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 20, "height_mm": 10}).body
    r = DetectMirrorSymmetry().apply(box, {"test_planes": ["auto"]})
    planes = r.extras["mirror_planes"]
    assert len(planes) >= 1
    # Best PCA-aligned plane should be very symmetric on a centered box.
    assert planes[0]["symmetry_score"] > 0.85


def test_mirror_body_unchanged():
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = DetectMirrorSymmetry().apply(box, {"test_planes": ["XZ"]})
    assert r.body is box


def test_mirror_spec_metadata():
    spec = DetectMirrorSymmetry.spec
    assert spec.name == "detect_mirror_symmetry"
    assert spec.category == "inspect"
    assert "mirror_planes" in spec.produces_features


# -------------------- rotational --------------------

def test_rotational_cylinder_high_score_along_axis():
    """A cylinder is rotationally symmetric to (theoretically) any order about its
    own axis. We expect very high scores for many orders along the cylinder's
    Z-axis (the default axis when axis="Z").
    """
    cyl = Cylinder().apply(None, {
        "radius_mm": 10.0, "height_mm": 20.0, "axis": "Z",
    }).body
    r = DetectRotationalSymmetry().apply(cyl, {
        "test_axes": ["Z"], "max_order": 8, "sample_count": 200,
    })
    rot = r.extras["rotational_axes"]
    assert len(rot) >= 1
    # All orders along Z should look essentially symmetric (infinite-order surrogate)
    high = [e for e in rot if e["score"] > 0.9]
    assert len(high) >= 5  # most orders 2..8 should pass


def test_rotational_cylinder_off_axis_lower_than_on_axis():
    """Off-axis tests (X or Y for a Z-cylinder) should generally score lower than
    on-axis tests for non-trivial orders."""
    cyl = Cylinder().apply(None, {
        "radius_mm": 10.0, "height_mm": 30.0, "axis": "Z",
    }).body
    r = DetectRotationalSymmetry().apply(cyl, {
        "test_axes": ["X", "Y", "Z"], "max_order": 6,
    })
    rot = r.extras["rotational_axes"]

    def best_for_axis_dir(dir_xyz):
        cands = [e for e in rot if tuple(e["axis_dir"]) == dir_xyz]
        return max((e["score"] for e in cands), default=0.0)

    z_best = best_for_axis_dir((0.0, 0.0, 1.0))
    x_best = best_for_axis_dir((1.0, 0.0, 0.0))
    # Z (cylinder axis) should be at least as strong as X for any order
    assert z_best >= x_best - 0.05


def test_rotational_body_unchanged():
    cyl = Cylinder().apply(None, {
        "radius_mm": 5.0, "height_mm": 10.0, "axis": "Z",
    }).body
    r = DetectRotationalSymmetry().apply(cyl, {
        "test_axes": ["Z"], "max_order": 4,
    })
    assert r.body is cyl


def test_rotational_spec_metadata():
    spec = DetectRotationalSymmetry.spec
    assert spec.name == "detect_rotational_symmetry"
    assert spec.category == "inspect"
    assert "rotational_axes" in spec.produces_features


def test_rotational_results_sorted_by_score_desc():
    cyl = Cylinder().apply(None, {
        "radius_mm": 8.0, "height_mm": 16.0, "axis": "Z",
    }).body
    r = DetectRotationalSymmetry().apply(cyl, {
        "test_axes": ["Z"], "max_order": 5,
    })
    rot = r.extras["rotational_axes"]
    scores = [e["score"] for e in rot]
    assert scores == sorted(scores, reverse=True)
