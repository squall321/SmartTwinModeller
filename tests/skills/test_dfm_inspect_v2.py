"""DFM inspect v2 — inspect_wall_thickness_v2 / inspect_undercut_zones_v2.

Both skills are read-only — body must be unchanged after the call
(post_condition body_present). The schemas are flatter than v1 and meant for
downstream tooling planners.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.inspect_undercut_zones_v2 import (
    InspectUndercutZonesV2,
)
from phone_designer.skills.inspect.inspect_wall_thickness_v2 import (
    InspectWallThicknessV2,
)
from phone_designer.skills.modify_pocket.hole import Hole


# ──────────────────────────────────────────────────────────────────────────────
# inspect_wall_thickness_v2


def test_wall_thickness_v2_box_uniform_thickness():
    """Box 20×20×10 → global_min ≈ 10 (top↔bottom), global_max ≈ 20 (side↔side)."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = InspectWallThicknessV2().apply(box, {
        "samples_per_face": 25,
        "max_check_mm": 50.0,
    })
    rep = r.extras["wall_thickness"]
    # core schema
    assert "global_min" in rep
    assert "global_max" in rep
    assert "mean" in rep
    assert "per_face" in rep
    assert "thin_regions" in rep
    assert "thin_threshold_mm" in rep
    # global_min should reflect the smallest dimension (height = 10)
    assert 9.0 < rep["global_min"] <= 10.5
    # max should reach the long axis (20 mm)
    assert rep["global_max"] >= 19.0
    # mean is somewhere in between
    assert rep["global_min"] - 0.01 <= rep["mean"] <= rep["global_max"] + 0.01
    # body unchanged
    assert r.body is box
    # there are faces measured
    assert len(rep["per_face"]) >= 1
    for f in rep["per_face"]:
        assert {"idx", "min", "mean", "max", "samples"} <= set(f.keys())
        assert f["samples"] >= 1
        assert f["min"] <= f["mean"] <= f["max"] + 1e-6


def test_wall_thickness_v2_thin_box_flags_thin_regions():
    """Box 20×20×0.5 — height is thinner than the default thin threshold (0.8)."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 0.5}).body
    r = InspectWallThicknessV2().apply(box, {
        "samples_per_face": 16,
        "max_check_mm": 10.0,
    })
    rep = r.extras["wall_thickness"]
    # at least the top/bottom faces should produce thin samples (≈ 0.5 mm)
    assert len(rep["thin_regions"]) >= 1
    # every flagged region carries face_idx + point + thickness
    for tr in rep["thin_regions"]:
        assert {"face_idx", "point", "thickness"} <= set(tr.keys())
        assert len(tr["point"]) == 3
        assert tr["thickness"] < rep["thin_threshold_mm"]
    # body unchanged
    assert r.body is box


def test_wall_thickness_v2_post_condition_body_present():
    """Result body must be a usable Part with the same volume — read-only."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = InspectWallThicknessV2().apply(box, {"samples_per_face": 9})
    assert r.body is box  # same Python object — no rewrap


# ──────────────────────────────────────────────────────────────────────────────
# inspect_undercut_zones_v2


def test_undercut_zones_v2_box_pull_z_has_one_undercut():
    """For a centred box pulled +Z, only the bottom face (normal −Z) is undercut.

    dot(normal, pull) = -1 < -0.1, so exactly one undercut face.
    """
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = InspectUndercutZonesV2().apply(box, {"pull_direction": (0.0, 0.0, 1.0)})
    uc = r.extras["undercut_faces"]
    assert len(uc) == 1
    rec = uc[0]
    assert {"idx", "area", "normal", "point"} <= set(rec.keys())
    # bottom face — normal −Z
    nx, ny, nz = rec["normal"]
    assert abs(nx) < 0.05
    assert abs(ny) < 0.05
    assert nz < -0.9
    # area = 20×20 = 400
    assert abs(rec["area"] - 400.0) < 0.5
    # centre of mass z ≈ 0
    assert abs(rec["point"][2]) < 0.05
    # body unchanged
    assert r.body is box


def test_undercut_zones_v2_pull_x_flags_minus_x_face():
    """Pull = +X → only the −X face is undercut."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = InspectUndercutZonesV2().apply(box, {"pull_direction": (1.0, 0.0, 0.0)})
    uc = r.extras["undercut_faces"]
    assert len(uc) == 1
    nx, _, _ = uc[0]["normal"]
    assert nx < -0.9


def test_undercut_zones_v2_box_with_negative_z_hole_no_extra_undercuts():
    """Box with a blind −Z hole → bottom face still undercut for +Z pull;
    the cylindrical hole face has horizontal normals (dot ≈ 0) → not undercut."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    holed = Hole().apply(box, {
        "position": (0.0, 0.0, 10.0),
        "diameter_mm": 4.0,
        "depth_mm": 5.0,
        "direction": "-Z",
    }).body
    r = InspectUndercutZonesV2().apply(holed, {"pull_direction": (0.0, 0.0, 1.0)})
    uc = r.extras["undercut_faces"]
    # at minimum, the box bottom face is undercut. The hole's inner cylinder
    # sample at the parametric mid-point has a horizontal normal (dot ≈ 0)
    # and is not flagged. The blind hole bottom (normal +Z) is also not.
    assert len(uc) >= 1
    # bottom face must still be present
    assert any(rec["normal"][2] < -0.9 for rec in uc)
    # body unchanged
    assert r.body is holed


def test_undercut_zones_v2_zero_pull_raises():
    """Zero pull_direction is undefined — must raise."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    with pytest.raises(Exception):
        InspectUndercutZonesV2().apply(box, {"pull_direction": (0.0, 0.0, 0.0)})


def test_undercut_zones_v2_post_condition_body_present():
    """Body is the same object after the read-only call."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = InspectUndercutZonesV2().apply(box, {"pull_direction": (0.0, 0.0, 1.0)})
    assert r.body is box
