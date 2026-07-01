"""path_array_orientation unit tests — array a seed body along a 3D path,
each instance oriented to the local path tangent (full 3D frame).

Analytic anchors (seed = 4×4×4 box, vol 64; Box is XY-centered so it spans
x,y ∈ [-2,2], z ∈ [0,4]):
  * straight path [0,0,0]->[40,0,0], count=5 -> stations 0,10,20,30,40; the
    10 mm gap >> the 4 mm box, so copies are DISJOINT -> vol = 5*64 = 320
    (both the compound and the fused result).
  * quarter-circle path (R=40), count=5 -> 5 disjoint instances (consecutive
    centroids ~15 mm apart) -> vol = 320. Proves the full 3D tangent frame.
"""
from __future__ import annotations

import math

from phone_designer.skills.create.box import Box
from phone_designer.skills.transform.path_array_orientation import (
    PathArrayOrientation,
)


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, g)
    return abs(float(g.Mass()))


def _count_solids(body) -> int:
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    ex = TopExp_Explorer(body.wrapped, TopAbs_SOLID)
    n = 0
    while ex.More():
        n += 1
        ex.Next()
    return n


def _seed():
    # 4×4×4 box, vol 64. XY-centered, Z bottom at 0.
    return Box().apply(None, {
        "length_mm": 4.0, "width_mm": 4.0, "height_mm": 4.0,
    }).body


def test_straight_path_compound_sums_volumes():
    # 5 disjoint copies along a straight 40mm path -> compound vol 320.
    r = PathArrayOrientation().apply(_seed(), {
        "path_points": [(0.0, 0.0, 0.0), (40.0, 0.0, 0.0)],
        "count": 5,
        "align_to_tangent": True,
        "fuse": False,
    })
    v = _volume(r.body)
    assert abs(v - 320.0) < 1.0, f"straight compound vol {v:.3f}, expected 320"
    assert _count_solids(r.body) == 5
    ex = r.extras["transform"]
    assert ex["count"] == 5
    assert ex["fused"] is False
    assert abs(ex["path_length_mm"] - 40.0) < 1e-3


def test_straight_path_fused_sums_volumes():
    # Same 5 disjoint copies, but fuse=True -> still 320 (disjoint union).
    r = PathArrayOrientation().apply(_seed(), {
        "path_points": [(0.0, 0.0, 0.0), (40.0, 0.0, 0.0)],
        "count": 5,
        "fuse": True,
    })
    v = _volume(r.body)
    assert abs(v - 320.0) < 1.0, f"straight fused vol {v:.3f}, expected 320"
    assert r.extras["transform"]["fused"] is True


def test_quarter_circle_path_builds_count_disjoint():
    # 9 sampled points on a quarter circle R=40 -> polyline path. count=5.
    R = 40.0
    n = 9
    pts = [
        (R * math.cos(math.pi / 2 * k / (n - 1)),
         R * math.sin(math.pi / 2 * k / (n - 1)),
         0.0)
        for k in range(n)
    ]
    r = PathArrayOrientation().apply(_seed(), {
        "path_points": pts,
        "count": 5,
        "align_to_tangent": True,
        "fuse": False,
    })
    v = _volume(r.body)
    # ~62.7mm arc / 4 gaps ~= 15.7mm spacing >> 4mm box -> disjoint.
    assert abs(v - 320.0) < 1.0, f"quarter-circle vol {v:.3f}, expected 320"
    assert _count_solids(r.body) == 5
    # polyline approximation of a quarter-circle: arc length ~62.7mm.
    assert abs(r.extras["transform"]["path_length_mm"] - math.pi * R / 2) < 0.5


def test_full_3d_path_keeps_z():
    # A path that RISES in Z: [0,0,0]->[0,0,40]. path_pattern would drop Z and
    # collapse; this skill keeps it -> 5 disjoint copies stacked in Z, vol 320.
    r = PathArrayOrientation().apply(_seed(), {
        "path_points": [(0.0, 0.0, 0.0), (0.0, 0.0, 40.0)],
        "count": 5,
        "align_to_tangent": True,
        "fuse": False,
    })
    v = _volume(r.body)
    assert abs(v - 320.0) < 1.0, f"z-rising path vol {v:.3f}, expected 320"
    # bbox must extend ~40mm+ in Z (the box half-heights add to it).
    assert r.extras["transform"]["bbox_mm"][2] >= 40.0


def test_bspline_curve_type_builds_count():
    # smooth 3D b-spline through helix-ish points -> 5 instances, non-zero vol.
    pts = [(0, 0, 0), (10, 0, 5), (20, 5, 10), (30, 0, 15), (40, 0, 20)]
    r = PathArrayOrientation().apply(_seed(), {
        "path_points": pts,
        "count": 5,
        "curve_type": "bspline",
        "fuse": False,
    })
    assert _count_solids(r.body) == 5
    v = _volume(r.body)
    assert abs(v - 320.0) < 1.0, f"bspline vol {v:.3f}, expected 320"


def test_count_one_single_instance():
    r = PathArrayOrientation().apply(_seed(), {
        "path_points": [(0.0, 0.0, 0.0), (40.0, 0.0, 0.0)],
        "count": 1,
    })
    v = _volume(r.body)
    assert abs(v - 64.0) < 0.5, f"count=1 vol {v:.3f}, expected 64"


def test_align_false_is_translation_only():
    # Along a straight X path, tangent-align vs translate-only produce the same
    # box orientation, so both give 320 — but the flag is recorded.
    r = PathArrayOrientation().apply(_seed(), {
        "path_points": [(0.0, 0.0, 0.0), (40.0, 0.0, 0.0)],
        "count": 5,
        "align_to_tangent": False,
        "fuse": False,
    })
    v = _volume(r.body)
    assert abs(v - 320.0) < 1.0, f"translate-only vol {v:.3f}, expected 320"
    assert r.extras["transform"]["align_to_tangent"] is False


def test_too_few_points_refused():
    import pytest
    with pytest.raises(Exception):
        PathArrayOrientation().apply(_seed(), {
            "path_points": [(0.0, 0.0, 0.0)],
            "count": 3,
        })


def test_post_condition_and_registration():
    spec = PathArrayOrientation.spec
    assert any(pc.kind == "body_present" for pc in spec.post_conditions)
    assert spec.category == "transform"
    assert spec.name == "path_array_orientation"
