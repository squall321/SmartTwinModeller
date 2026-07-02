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
    # OCCT Fuse of disjoint solids "succeeds" but returns a 5-solid compound —
    # extras must report the TRUE solid count, not fused=True/n_bodies=1.
    r = PathArrayOrientation().apply(_seed(), {
        "path_points": [(0.0, 0.0, 0.0), (40.0, 0.0, 0.0)],
        "count": 5,
        "fuse": True,
    })
    v = _volume(r.body)
    assert abs(v - 320.0) < 1.0, f"straight fused vol {v:.3f}, expected 320"
    assert r.extras["transform"]["n_bodies"] == 5
    assert r.extras["transform"]["fused"] is False


def test_overlapping_fuse_is_one_body():
    # Stations every 2mm along a straight 8mm path << the 4mm box -> copies
    # OVERLAP and genuinely union into ONE solid: bar spanning x in [-2, 10]
    # (XY-centered box) -> 12*4*4 = 192. fused=True must mean exactly this.
    r = PathArrayOrientation().apply(_seed(), {
        "path_points": [(0.0, 0.0, 0.0), (8.0, 0.0, 0.0)],
        "count": 5,
        "fuse": True,
    })
    v = _volume(r.body)
    assert abs(v - 192.0) < 1.0, f"overlap union vol {v:.3f}, expected 192"
    assert r.extras["transform"]["n_bodies"] == 1
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


def _offset_seed():
    # Box spanning local x∈[2,4], y∈[-1,1], z∈[-0.5,0.5] — non-axisymmetric,
    # centroid 3 mm off the frame origin along local +X, so each instance's
    # centroid reveals the frame X image: X_i = (centroid_i - station_i) / 3.
    from build123d import Part
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    return Part(BRepPrimAPI_MakeBox(
        gp_Pnt(2.0, -1.0, -0.5), gp_Pnt(4.0, 1.0, 0.5)).Shape())


def _solid_centroids(body):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    out = []
    ex = TopExp_Explorer(body.wrapped, TopAbs_SOLID)
    while ex.More():
        g = GProp_GProps()
        BRepGProp.VolumeProperties_s(ex.Current(), g)
        c = g.CentreOfMass()
        out.append((c.X(), c.Y(), c.Z()))
        ex.Next()
    return out


def test_no_roll_flip_between_stations_on_curved_path():
    """Regression (parallel transport): gp_Ax3(P, dir(T))'s IMPLICIT X-direction
    is discontinuous in T, so instances rolled 90–180° about the tangent between
    adjacent stations as the path heading crossed OCCT's component-magnitude
    crossover. The frame X must be parallel-transported station to station: the
    roll between adjacent instances stays below ~2x the tangent turn angle
    (11.25°/station here), while the old behaviour jumped ~170°.
    """
    from OCP.BRepAdaptor import BRepAdaptor_CompCurve
    from OCP.GCPnts import GCPnts_AbscissaPoint
    from OCP.gp import gp_Pnt, gp_Vec

    from phone_designer.skills.transform.path_array_orientation import (
        _build_path_wire,
    )

    # Quarter circle R=40 from (0,-40,0) to (40,0,0): tangents sweep heading
    # 0°→90°, the range where the implicit-X flip occurred mid-path.
    R, n, count = 40.0, 9, 9
    pts = [
        (R * math.cos(-math.pi / 2 + math.pi / 2 * k / (n - 1)),
         R * math.sin(-math.pi / 2 + math.pi / 2 * k / (n - 1)),
         0.0)
        for k in range(n)
    ]
    r = PathArrayOrientation().apply(_offset_seed(), {
        "path_points": pts,
        "count": count,
        "curve_type": "bspline",
        "align_to_tangent": True,
        "fuse": False,
    })
    assert _count_solids(r.body) == count

    # Recompute the station points exactly as the skill does, then recover the
    # frame X image at each station from the instance centroid (3 mm offset).
    wire = _build_path_wire(pts, "bspline")
    cc = BRepAdaptor_CompCurve(wire)
    u0 = cc.FirstParameter()
    total = float(GCPnts_AbscissaPoint.Length_s(cc))
    xs = []
    for i, c in enumerate(_solid_centroids(r.body)):
        s = total * i / (count - 1)
        u = GCPnts_AbscissaPoint(cc, s, u0).Parameter()
        P = gp_Pnt()
        T = gp_Vec()
        cc.D1(u, P, T)
        x = ((c[0] - P.X()) / 3.0, (c[1] - P.Y()) / 3.0, (c[2] - P.Z()) / 3.0)
        m = math.sqrt(x[0] ** 2 + x[1] ** 2 + x[2] ** 2)
        assert abs(m - 1.0) < 1e-6, f"station {i}: |X image| {m:.6f} != 1"
        xs.append(x)
    max_turn_deg = 2.0 * (90.0 / (count - 1))
    for i, (a, b) in enumerate(zip(xs, xs[1:])):
        dot = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]))
        ang = math.degrees(math.acos(dot))
        assert ang < max_turn_deg, (
            f"roll flip between instances {i} and {i + 1}: {ang:.1f}° "
            f"(limit {max_turn_deg:.2f}°)")


def test_too_few_points_refused():
    # The refusal must carry the STRUCTURED failure code — a Field(min_length=2)
    # would fire pydantic's raw too_short error before the validator, making
    # fm.path_too_few_points unreachable.
    import pytest
    with pytest.raises(Exception) as exc:
        PathArrayOrientation().apply(_seed(), {
            "path_points": [(0.0, 0.0, 0.0)],
            "count": 3,
        })
    assert "fm.path_too_few_points" in str(exc.value)
    with pytest.raises(Exception) as exc:
        PathArrayOrientation().apply(_seed(), {
            "path_points": [],
            "count": 3,
        })
    assert "fm.path_too_few_points" in str(exc.value)


def test_post_condition_and_registration():
    spec = PathArrayOrientation.spec
    assert any(pc.kind == "body_present" for pc in spec.post_conditions)
    assert spec.category == "transform"
    assert spec.name == "path_array_orientation"
