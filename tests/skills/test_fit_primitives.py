"""Wave1-C — best-fit primitive skills (fit_plane / fit_cylinder /
fit_sphere / fit_cone / fit_torus).

All read-only. body unchanged after every call (post_condition body_present).
Each skill picks one face via ``face_selector`` and returns analytic params
+ a max residual. When the underlying surface natively matches the requested
primitive (e.g. Box top face → GeomAbs_Plane for ``fit_plane``), the skill
short-circuits to the OCCT analytic params and the residual is ≈ 0.
"""
from __future__ import annotations

import math

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.cone import Cone
from phone_designer.skills.create.cylinder import Cylinder
from phone_designer.skills.create.sphere import Sphere
from phone_designer.skills.create.torus import Torus
from phone_designer.skills.inspect.fit_cone import FitCone
from phone_designer.skills.inspect.fit_cylinder import FitCylinder
from phone_designer.skills.inspect.fit_plane import FitPlane
from phone_designer.skills.inspect.fit_sphere import FitSphere
from phone_designer.skills.inspect.fit_torus import FitTorus


# Selector for "the curved side face" of a body when there is a single
# dominant curved face (e.g. cylinder / sphere / torus / cone single body).
_LARGEST_CURVED_FACE = {
    "kind": "largest_n", "n": 1, "sort_by": "measure",
    "inner": {"kind": "faces_by_area", "min": 1.0, "max": 1e9},
}


# ──────────────────────────────────────────────────────────────────────────────
# fit_plane

def test_fit_plane_box_top_is_native():
    """Box top face is GeomAbs_Plane → analytic short-circuit, residual ≈ 0."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = FitPlane().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
    })
    fp = r.extras["fit_plane"]
    assert fp["is_planar"] is True
    assert fp["max_residual_mm"] < 1e-6
    # Z is the top normal — sign may flip per face orientation.
    assert abs(abs(fp["normal"][2]) - 1.0) < 1e-6
    assert fp["sample_count"] >= 4
    assert r.body is box


def test_fit_plane_on_sphere_face_non_native_has_residual():
    """Sphere face is NOT planar — SVD plane fit has a large residual."""
    s = Sphere().apply(None, {"radius_mm": 10.0}).body
    r = FitPlane().apply(s, {
        "face_selector": {"kind": "faces_by_area", "min": 0.0},
    })
    fp = r.extras["fit_plane"]
    assert fp["is_planar"] is False
    # A sphere of radius 10 covered by a least-squares plane has residual
    # of order R (max distance from the equatorial plane ≈ R).
    assert fp["max_residual_mm"] > 1.0
    assert r.body is s


# ──────────────────────────────────────────────────────────────────────────────
# fit_cylinder

def test_fit_cylinder_native_cylinder_passes():
    """Cylinder side face is GeomAbs_Cylinder → native, residual ≈ 0."""
    cyl = Cylinder().apply(None, {"radius_mm": 5.0, "height_mm": 20.0}).body
    r = FitCylinder().apply(cyl, {"face_selector": _LARGEST_CURVED_FACE})
    fc = r.extras["fit_cylinder"]
    assert fc["is_native"] is True
    assert abs(fc["radius_mm"] - 5.0) < 1e-6
    assert fc["max_residual_mm"] < 1e-6
    # Axis is +Z (sign may flip).
    assert abs(abs(fc["axis_direction"][2]) - 1.0) < 1e-6
    assert r.body is cyl


def test_fit_cylinder_on_box_top_non_native_has_residual():
    """Box top face is planar — point-fit cylinder has large residual."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = FitCylinder().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
    })
    fc = r.extras["fit_cylinder"]
    assert fc["is_native"] is False
    # Planar 20×20 fit to a cylinder → radial spread of order 10 mm.
    assert fc["max_residual_mm"] > 1.0
    assert r.body is box


# ──────────────────────────────────────────────────────────────────────────────
# fit_sphere

def test_fit_sphere_native_sphere_passes():
    """Sphere body face is GeomAbs_Sphere → native, residual ≈ 0."""
    s = Sphere().apply(None, {"radius_mm": 7.5}).body
    r = FitSphere().apply(s, {
        "face_selector": {"kind": "faces_by_area", "min": 0.0},
    })
    fs = r.extras["fit_sphere"]
    assert fs["is_native"] is True
    assert abs(fs["radius_mm"] - 7.5) < 1e-6
    assert fs["max_residual_mm"] < 1e-6
    # Sphere centered at origin by default.
    for c in fs["center"]:
        assert abs(c) < 1e-6
    assert r.body is s


def test_fit_sphere_on_cylinder_face_non_native_has_residual():
    """Cylinder side face is NOT a sphere — algebraic sphere fit has residual."""
    cyl = Cylinder().apply(None, {"radius_mm": 5.0, "height_mm": 20.0}).body
    r = FitSphere().apply(cyl, {"face_selector": _LARGEST_CURVED_FACE})
    fs = r.extras["fit_sphere"]
    assert fs["is_native"] is False
    assert fs["max_residual_mm"] > 0.1
    assert r.body is cyl


# ──────────────────────────────────────────────────────────────────────────────
# fit_cone

def test_fit_cone_native_cone_passes():
    """Cone body lateral face is GeomAbs_Cone → native, residual ≈ 0."""
    cone = Cone().apply(None, {
        "radius_lower_mm": 5.0, "radius_upper_mm": 0.0, "height_mm": 10.0,
    }).body
    r = FitCone().apply(cone, {"face_selector": _LARGEST_CURVED_FACE})
    fc = r.extras["fit_cone"]
    assert fc["is_native"] is True
    # Cone with r=5, h=10 → half-angle = atan(5/10) ≈ 26.565°.
    assert abs(fc["half_angle_deg"] - math.degrees(math.atan(0.5))) < 1e-3
    assert fc["max_residual_mm"] < 1e-6
    # Apex is (0, 0, 10).
    assert abs(fc["apex"][2] - 10.0) < 1e-6
    assert r.body is cone


def test_fit_cone_on_box_top_non_native_has_residual():
    """Box top face is planar — fitting a cone gives a non-zero residual."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = FitCone().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
    })
    fc = r.extras["fit_cone"]
    assert fc["is_native"] is False
    # Planar 20×20 patch — the linear r(t) fit cannot match the radial
    # spread, so residual is at least a millimetre or two.
    assert fc["max_residual_mm"] > 1.0
    assert r.body is box


# ──────────────────────────────────────────────────────────────────────────────
# fit_torus

def test_fit_torus_native_torus_passes():
    """Torus body face is GeomAbs_Torus → native, residual ≈ 0."""
    tor = Torus().apply(None, {
        "major_radius_mm": 10.0, "minor_radius_mm": 2.0,
    }).body
    r = FitTorus().apply(tor, {
        "face_selector": {"kind": "faces_by_area", "min": 0.0},
    })
    ft = r.extras["fit_torus"]
    assert ft["is_native"] is True
    assert abs(ft["major_r_mm"] - 10.0) < 1e-6
    assert abs(ft["minor_r_mm"] - 2.0) < 1e-6
    assert ft["max_residual_mm"] < 1e-6
    assert abs(abs(ft["axis"][2]) - 1.0) < 1e-6
    assert r.body is tor


def test_fit_torus_on_box_top_non_native_has_residual():
    """Box top face is planar — heuristic torus fit gives a non-zero residual."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = FitTorus().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
    })
    ft = r.extras["fit_torus"]
    assert ft["is_native"] is False
    # Planar 20×20 fit to a torus — minor radius residual > 1 mm.
    assert ft["max_residual_mm"] > 0.5
    assert r.body is box


# ──────────────────────────────────────────────────────────────────────────────
# body_present guarantee — every skill must return the same body object.

def test_all_fit_skills_preserve_body():
    """body_present post-condition holds for every fit skill in this pack."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    sel_top = {"face_selector": {"kind": "face_named", "name": "top"}}
    for skill_cls in (FitPlane, FitCylinder, FitSphere, FitCone, FitTorus):
        r = skill_cls().apply(box, sel_top)
        assert r.body is box, f"{skill_cls.__name__} mutated body"
