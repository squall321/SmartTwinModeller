"""Tests for inspect.detect_linear_array and inspect.detect_circular_array.

Builds:

    A) 60 × 20 × 10 plate with 5 Ø3 holes drilled along +X, 8 mm spacing.
    B) 60 × 60 × 10 plate with 6 Ø3 holes evenly distributed on a circle
       of radius 15 mm in the XY plane (centred at the box origin).
"""
from __future__ import annotations

import math

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.detect_circular_array import (
    DetectCircularArray,
)
from phone_designer.skills.inspect.detect_linear_array import DetectLinearArray
from phone_designer.skills.modify_pocket.hole import Hole


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures


def _box_with_linear_hole_array():
    """60 × 20 × 10 plate. 5 Ø3 blind holes along +X at y=0, spacing 8mm.

    Holes are placed at x ∈ {-16, -8, 0, 8, 16}, depth 5, entering from
    the top.
    """
    body = Box().apply(None, {
        "length_mm": 60.0, "width_mm": 20.0, "height_mm": 10.0,
    }).body
    for x in (-16.0, -8.0, 0.0, 8.0, 16.0):
        body = Hole().apply(body, {
            "position": (x, 0.0, 10.0),
            "diameter_mm": 3.0,
            "depth_mm": 5.0,
            "direction": "-Z",
        }).body
    return body


def _box_with_circular_hole_array():
    """60 × 60 × 10 plate. 6 Ø3 blind holes on a circle of radius 15 mm
    centred at XY origin, entering from the top."""
    body = Box().apply(None, {
        "length_mm": 60.0, "width_mm": 60.0, "height_mm": 10.0,
    }).body
    R = 15.0
    for k in range(6):
        theta = 2.0 * math.pi * k / 6.0
        x = R * math.cos(theta)
        y = R * math.sin(theta)
        body = Hole().apply(body, {
            "position": (x, y, 10.0),
            "diameter_mm": 3.0,
            "depth_mm": 5.0,
            "direction": "-Z",
        }).body
    return body


# ──────────────────────────────────────────────────────────────────────────────
# Linear array


def test_linear_array_detects_5_holes_in_a_row():
    body = _box_with_linear_hole_array()
    r = DetectLinearArray().apply(body, {
        "feature_kind": "hole", "min_count": 3,
    })
    runs = r.extras["linear_arrays"]
    assert runs, "expected at least one linear hole array"
    # find the run with the maximum count
    best = max(runs, key=lambda d: d["count"])
    assert best["count"] == 5, (
        f"expected 5-hole run, got runs={[d['count'] for d in runs]}"
    )
    assert abs(best["spacing_mm"] - 8.0) < 0.5
    # direction should be mostly along ±X
    dx, dy, dz = best["direction"]
    assert abs(abs(dx) - 1.0) < 0.1, f"unexpected direction {best['direction']}"
    assert abs(dy) < 0.1 and abs(dz) < 0.1
    assert best["feature_kind"] == "hole"
    # body unchanged
    assert r.body is body


def test_linear_array_respects_min_count():
    body = _box_with_linear_hole_array()
    r = DetectLinearArray().apply(body, {
        "feature_kind": "hole", "min_count": 6,
    })
    # there is no 6-hole linear run on this body
    runs = r.extras["linear_arrays"]
    assert all(d["count"] < 6 or d["count"] == 0 for d in runs) or runs == []


def test_linear_array_body_present_post():
    spec = DetectLinearArray.spec
    assert spec.category == "inspect"
    assert spec.level == "atomic"
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "body_present" in kinds


# ──────────────────────────────────────────────────────────────────────────────
# Circular array


def test_circular_array_detects_6_holes_on_circle():
    body = _box_with_circular_hole_array()
    r = DetectCircularArray().apply(body, {
        "feature_kind": "hole", "min_count": 4,
    })
    arrays = r.extras["circular_arrays"]
    assert arrays, "expected at least one circular hole array"
    best = max(arrays, key=lambda d: d["count"])
    assert best["count"] == 6
    assert abs(best["radius_mm"] - 15.0) < 0.5
    assert abs(best["angular_pitch_deg"] - 60.0) < 1.0
    # axis should be ~ ±Z
    ax, ay, az = best["axis"]
    assert abs(abs(az) - 1.0) < 0.1
    assert abs(ax) < 0.1 and abs(ay) < 0.1
    # centre near origin in XY
    cx, cy, _cz = best["center"]
    assert abs(cx) < 0.5 and abs(cy) < 0.5
    assert best["feature_kind"] == "hole"
    # body unchanged
    assert r.body is body


def test_circular_array_respects_min_count():
    body = _box_with_circular_hole_array()
    r = DetectCircularArray().apply(body, {
        "feature_kind": "hole", "min_count": 8,
    })
    arrays = r.extras["circular_arrays"]
    # No ring of size ≥8 exists; expect either empty or all counts < 8.
    assert all(d["count"] < 8 for d in arrays)


def test_circular_array_body_present_post():
    spec = DetectCircularArray.spec
    assert spec.category == "inspect"
    assert spec.level == "atomic"
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "body_present" in kinds
