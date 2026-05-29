"""Primitive create skill 단위 테스트.

각 skill 마다:
  - volume sanity (closed-form 과 ±1% 이내)
  - bbox sanity
  - post-condition framework 가 result 를 받아들임 (body_present 가 raise 하지 않음)
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.cone import Cone
from phone_designer.skills.create.cylinder import Cylinder
from phone_designer.skills.create.prism_n_sided import PrismNSided
from phone_designer.skills.create.sphere import Sphere
from phone_designer.skills.create.torus import Torus
from phone_designer.skills.create.wedge import Wedge


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _bbox(body):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(body.wrapped, bb, True)
    return bb.Get()  # (xmin, ymin, zmin, xmax, ymax, zmax)


# ---------- cylinder ----------

def test_cylinder_volume_matches_formula():
    r = Cylinder().apply(None, {"radius_mm": 10.0, "height_mm": 20.0})
    assert _volume(r.body) == pytest.approx(math.pi * 100 * 20, rel=0.01)


def test_cylinder_bbox():
    r = Cylinder().apply(None, {
        "radius_mm": 5.0, "height_mm": 12.0,
        "center_x_mm": 0.0, "center_y_mm": 0.0, "base_z_mm": 0.0,
    })
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    assert xmin == pytest.approx(-5.0, abs=0.05)
    assert xmax == pytest.approx(5.0, abs=0.05)
    assert ymin == pytest.approx(-5.0, abs=0.05)
    assert ymax == pytest.approx(5.0, abs=0.05)
    assert zmin == pytest.approx(0.0, abs=0.05)
    assert zmax == pytest.approx(12.0, abs=0.05)


def test_cylinder_post_condition_passes():
    # apply() 내부에서 post_conditions 가 평가되므로 raise 없으면 OK.
    r = Cylinder().apply(None, {"radius_mm": 3.0, "height_mm": 8.0})
    assert r.body is not None


def test_cylinder_axis_x_bbox():
    """axis=X → length 가 X 방향."""
    r = Cylinder().apply(None, {
        "radius_mm": 2.0, "height_mm": 10.0, "axis": "X",
    })
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    assert (xmax - xmin) == pytest.approx(10.0, abs=0.1)
    assert (ymax - ymin) == pytest.approx(4.0, abs=0.1)


# ---------- sphere ----------

def test_sphere_volume_matches_formula():
    r = Sphere().apply(None, {"radius_mm": 7.0})
    assert _volume(r.body) == pytest.approx((4.0 / 3.0) * math.pi * 7.0 ** 3, rel=0.01)


def test_sphere_bbox():
    r = Sphere().apply(None, {"radius_mm": 5.0, "center": (1.0, 2.0, 3.0)})
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    assert xmin == pytest.approx(-4.0, abs=0.05)
    assert xmax == pytest.approx(6.0, abs=0.05)
    assert ymin == pytest.approx(-3.0, abs=0.05)
    assert ymax == pytest.approx(7.0, abs=0.05)
    assert zmin == pytest.approx(-2.0, abs=0.05)
    assert zmax == pytest.approx(8.0, abs=0.05)


def test_sphere_post_condition_passes():
    r = Sphere().apply(None, {"radius_mm": 4.0})
    assert r.body is not None


# ---------- cone ----------

def test_cone_frustum_volume_matches_formula():
    r1, r2, h = 10.0, 5.0, 20.0
    r = Cone().apply(None, {
        "radius_lower_mm": r1, "radius_upper_mm": r2, "height_mm": h,
    })
    expected = math.pi * h / 3.0 * (r1 * r1 + r2 * r2 + r1 * r2)
    assert _volume(r.body) == pytest.approx(expected, rel=0.01)


def test_cone_pure_volume_matches_formula():
    r1, h = 8.0, 15.0
    r = Cone().apply(None, {
        "radius_lower_mm": r1, "radius_upper_mm": 0.0, "height_mm": h,
    })
    expected = math.pi * h / 3.0 * (r1 * r1)
    assert _volume(r.body) == pytest.approx(expected, rel=0.01)


def test_cone_bbox():
    r = Cone().apply(None, {
        "radius_lower_mm": 4.0, "radius_upper_mm": 2.0, "height_mm": 10.0,
    })
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    assert xmax == pytest.approx(4.0, abs=0.05)
    assert xmin == pytest.approx(-4.0, abs=0.05)
    assert zmin == pytest.approx(0.0, abs=0.05)
    assert zmax == pytest.approx(10.0, abs=0.05)


def test_cone_rejects_both_radii_zero():
    with pytest.raises(Exception):
        Cone().apply(None, {
            "radius_lower_mm": 0.0, "radius_upper_mm": 0.0, "height_mm": 10.0,
        })


def test_cone_post_condition_passes():
    r = Cone().apply(None, {
        "radius_lower_mm": 3.0, "radius_upper_mm": 1.0, "height_mm": 5.0,
    })
    assert r.body is not None


# ---------- torus ----------

def test_torus_volume_matches_formula():
    R, rmin = 20.0, 5.0
    r = Torus().apply(None, {
        "major_radius_mm": R, "minor_radius_mm": rmin,
    })
    expected = 2.0 * math.pi * math.pi * R * rmin * rmin
    assert _volume(r.body) == pytest.approx(expected, rel=0.01)


def test_torus_bbox():
    """OCCT 의 Bnd_Box 는 parametric surface 를 약간 부풀려 enclose 하므로
    실제 geometry 보다 살짝 큼. 절대 envelope (outer+epsilon, ≤ outer + 2*r) 안이어야."""
    R, rmin = 15.0, 3.0
    r = Torus().apply(None, {"major_radius_mm": R, "minor_radius_mm": rmin})
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    outer = R + rmin
    # Bnd_Box 의 looseness 는 minor radius 와 비슷한 정도. 절대 outer + rmin 을 넘지 않음.
    assert outer - 0.1 <= xmax <= outer + rmin + 0.5
    assert -(outer + rmin + 0.5) <= xmin <= -(outer - 0.1)
    # Z 도 동일하게 ±rmin envelope 안.
    assert rmin - 0.1 <= zmax <= rmin + rmin + 0.5
    assert -(rmin + rmin + 0.5) <= zmin <= -(rmin - 0.1)


def test_torus_rejects_major_le_minor():
    with pytest.raises(Exception):
        Torus().apply(None, {
            "major_radius_mm": 5.0, "minor_radius_mm": 5.0,
        })


def test_torus_post_condition_passes():
    r = Torus().apply(None, {"major_radius_mm": 10.0, "minor_radius_mm": 2.0})
    assert r.body is not None


# ---------- wedge ----------

def test_wedge_volume_matches_formula():
    dx, dy, dz, ltx = 10.0, 8.0, 6.0, 4.0
    r = Wedge().apply(None, {
        "dx": dx, "dy": dy, "dz": dz, "ltx_mm": ltx,
    })
    expected = dx * dy * dz * (1.0 + ltx / dx) / 2.0
    assert _volume(r.body) == pytest.approx(expected, rel=0.05)


def test_wedge_bbox():
    r = Wedge().apply(None, {
        "dx": 10.0, "dy": 5.0, "dz": 8.0, "ltx_mm": 3.0,
    })
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    assert xmin == pytest.approx(0.0, abs=0.05)
    assert xmax == pytest.approx(10.0, abs=0.05)
    assert ymin == pytest.approx(0.0, abs=0.05)
    assert ymax == pytest.approx(5.0, abs=0.05)
    assert zmin == pytest.approx(0.0, abs=0.05)
    assert zmax == pytest.approx(8.0, abs=0.05)


def test_wedge_rejects_ltx_too_large():
    with pytest.raises(Exception):
        Wedge().apply(None, {
            "dx": 5.0, "dy": 5.0, "dz": 5.0, "ltx_mm": 6.0,
        })


def test_wedge_post_condition_passes():
    r = Wedge().apply(None, {
        "dx": 4.0, "dy": 4.0, "dz": 4.0, "ltx_mm": 2.0,
    })
    assert r.body is not None


# ---------- prism_n_sided ----------

def test_prism_hexagon_volume_matches_formula():
    n, R, h = 6, 10.0, 20.0
    r = PrismNSided().apply(None, {
        "n_sides": n, "circumscribed_radius_mm": R, "height_mm": h,
    })
    expected = (n * R * R * math.sin(2.0 * math.pi / n) / 2.0) * h
    assert _volume(r.body) == pytest.approx(expected, rel=0.01)


def test_prism_triangle_volume_matches_formula():
    n, R, h = 3, 7.0, 15.0
    r = PrismNSided().apply(None, {
        "n_sides": n, "circumscribed_radius_mm": R, "height_mm": h,
    })
    expected = (n * R * R * math.sin(2.0 * math.pi / n) / 2.0) * h
    assert _volume(r.body) == pytest.approx(expected, rel=0.01)


def test_prism_bbox():
    r = PrismNSided().apply(None, {
        "n_sides": 8, "circumscribed_radius_mm": 5.0, "height_mm": 10.0,
    })
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    assert xmax == pytest.approx(5.0, abs=0.1)
    assert xmin == pytest.approx(-5.0, abs=0.1)
    assert zmin == pytest.approx(0.0, abs=0.05)
    assert zmax == pytest.approx(10.0, abs=0.05)


def test_prism_rejects_n_lt_3():
    with pytest.raises(Exception):
        PrismNSided().apply(None, {
            "n_sides": 2, "circumscribed_radius_mm": 5.0, "height_mm": 10.0,
        })


def test_prism_post_condition_passes():
    r = PrismNSided().apply(None, {
        "n_sides": 5, "circumscribed_radius_mm": 3.0, "height_mm": 7.0,
    })
    assert r.body is not None


# ---------- spec metadata sanity ----------

def test_all_primitives_have_body_present_post_condition():
    from phone_designer.skills._post_conditions import PostCondition
    for cls in (Cylinder, Sphere, Cone, Torus, Wedge, PrismNSided):
        pcs = cls.spec.post_conditions
        assert any(isinstance(p, PostCondition) and p.kind == "body_present" for p in pcs), \
            f"{cls.spec.name} missing body_present post_condition"
