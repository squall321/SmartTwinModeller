"""curvature_comb — read-only curve curvature sampler (BRepLProp_CLProps).

Sanity anchor: a true circle of radius R has curvature κ = 1/R at EVERY
station, so radius reads R everywhere. We build a cylinder of radius 10 (so its
circular edge is a circle of R=10) and assert every sampled radius ≈ 10 and
every curvature ≈ 0.1.

Also: a straight box edge has κ = 0 → radius = +inf everywhere. body must be
returned unchanged (post_condition body_present).
"""
from __future__ import annotations

import math

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.cylinder import Cylinder
from phone_designer.skills.inspect.curvature_comb import CurvatureComb


def test_curvature_comb_circle_radius_10_constant():
    """Cylinder r=10 → circular edge is a circle of R=10.

    Every station: curvature ≈ 0.1 (1/10), radius ≈ 10.
    """
    cyl = Cylinder().apply(None, {"radius_mm": 10.0, "height_mm": 20.0}).body

    r = CurvatureComb().apply(cyl, {
        # A circle of r=10 has circumference 2π·10 ≈ 62.83 mm — pick that edge.
        "edge_selector": {"kind": "edges_by_length", "min": 60.0, "max": 66.0},
        "n_samples": 40,
    })

    ex = r.extras
    assert ex["edge_count"] >= 1
    assert ex["n_samples"] == 40
    assert len(ex["samples"]) == 40

    # Every station reads κ = 0.1 (R = 10) — constant curvature comb.
    for s in ex["samples"]:
        assert abs(s["radius"] - 10.0) < 1e-4, f"radius off at u={s['u']}: {s['radius']}"
        assert abs(s["curvature"] - 0.1) < 1e-6, (
            f"curvature off at u={s['u']}: {s['curvature']}"
        )
        # point lies on the r=10 circle: sqrt(x²+y²) ≈ 10.
        x, y, _z = s["point"]
        assert abs(math.hypot(x, y) - 10.0) < 1e-4

    assert abs(ex["min_radius"] - 10.0) < 1e-4
    assert abs(ex["max_curvature"] - 0.1) < 1e-6

    # Read-only — body unchanged.
    assert r.body is cyl


def test_curvature_comb_straight_edge_is_flat():
    """A straight box edge has κ = 0 → radius = +inf at every station."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 10}).body

    r = CurvatureComb().apply(box, {
        "edge_selector": {"kind": "axis_aligned_edges", "axis": "X"},
        "n_samples": 12,
    })

    ex = r.extras
    assert ex["n_samples"] >= 2
    for s in ex["samples"]:
        assert s["curvature"] < 1e-9
        assert math.isinf(s["radius"])

    assert math.isinf(ex["min_radius"])
    assert ex["max_curvature"] < 1e-9
    assert r.body is box


def test_curvature_comb_no_edges_raises():
    """A selector matching 0 edges raises a clear ValueError."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    try:
        CurvatureComb().apply(box, {
            # No box edge is ~999 mm long → 0 matches.
            "edge_selector": {"kind": "edges_by_length", "min": 999.0},
            "n_samples": 8,
        })
    except ValueError as e:
        assert "0 edges" in str(e)
    else:
        raise AssertionError("expected ValueError for 0-edge selector")
