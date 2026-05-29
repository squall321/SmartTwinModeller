"""Tests for the gear + spring create skills.

Skills under test:
    - gear_external_involute
    - gear_internal_involute
    - helical_spring
    - coil_spring_rectangular

All four are atomic create skills (input body = None, post_condition
body_present). Tests verify:
    1. body is non-None and SkillResult.body has positive volume
    2. volume is in a plausible analytic range (closed-form ± 30 %)
    3. bounding box matches expected envelope
    4. tooth-count plausibility for gears (face count grows with n_teeth)
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.coil_spring_rectangular import CoilSpringRectangular
from phone_designer.skills.create.gear_external_involute import GearExternalInvolute
from phone_designer.skills.create.gear_internal_involute import GearInternalInvolute
from phone_designer.skills.create.helical_spring import HelicalSpring


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


def _face_count(body) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopTools import TopTools_MapOfShape
    seen = TopTools_MapOfShape()
    n = 0
    it = TopExp_Explorer(body.wrapped, TopAbs_FACE)
    while it.More():
        f = it.Current()
        if not seen.Contains(f):
            seen.Add(f)
            n += 1
        it.Next()
    return n


# ── gear_external_involute ────────────────────────────────────────────────


def test_gear_external_volume_and_bbox():
    """m=1 mm, z=20 → pitch_r=10, add_r=11. Volume ≈ π·R_eff² · W where
    R_eff is between dedendum and addendum (call it pitch_r). Tolerate ±30 %."""
    module = 1.0
    z = 20
    width = 5.0
    pitch_r = module * z / 2.0
    add_r = pitch_r + module

    r = GearExternalInvolute().apply(None, {
        "module_mm": module,
        "n_teeth": z,
        "pressure_angle_deg": 20.0,
        "width_mm": width,
    })
    assert r.body is not None
    v = _volume(r.body)
    assert v > 0.0, f"gear volume must be positive: {v}"

    # Bounded between dedendum-disc and addendum-disc volume.
    ded_r = pitch_r - 1.25 * module
    v_lo = math.pi * ded_r * ded_r * width * 0.9
    v_hi = math.pi * add_r * add_r * width * 1.05
    assert v_lo <= v <= v_hi, (
        f"gear volume {v:.2f} not in plausible range [{v_lo:.2f},{v_hi:.2f}]"
    )

    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    # bbox radial extent should not exceed addendum + tiny slack.
    assert xmax <= add_r + 0.2 and xmin >= -(add_r + 0.2), \
        f"bbox X out of range: {xmin}..{xmax}, expected ±{add_r:.2f}"
    assert zmin == pytest.approx(0.0, abs=0.05)
    assert zmax == pytest.approx(width, abs=0.05)


def test_gear_external_face_count_scales_with_teeth():
    """A 30-tooth gear should have more faces than a 12-tooth gear (each
    tooth contributes additional flank/root/tip faces). Topology heuristic
    for tooth count."""
    g_small = GearExternalInvolute().apply(None, {
        "module_mm": 1.0, "n_teeth": 12, "width_mm": 3.0,
    })
    g_big = GearExternalInvolute().apply(None, {
        "module_mm": 1.0, "n_teeth": 30, "width_mm": 3.0,
    })
    f_small = _face_count(g_small.body)
    f_big = _face_count(g_big.body)
    assert f_big > f_small, (
        f"face count should grow with n_teeth: {f_small} (z=12) vs {f_big} (z=30)"
    )
    # Each tooth on the polygonal approximation contributes at least one face;
    # over 30 teeth we expect well over 20 faces.
    assert f_big >= 20, f"face count too low for z=30: {f_big}"


def test_gear_external_with_bore_reduces_volume():
    no_bore = GearExternalInvolute().apply(None, {
        "module_mm": 1.0, "n_teeth": 20, "width_mm": 5.0,
    })
    with_bore = GearExternalInvolute().apply(None, {
        "module_mm": 1.0, "n_teeth": 20, "width_mm": 5.0,
        "bore_diameter_mm": 4.0,
    })
    v0 = _volume(no_bore.body)
    v1 = _volume(with_bore.body)
    expected_removed = math.pi * 4.0 * 5.0   # π·(D/2)²·H = π·4·5
    assert v1 < v0
    removed = v0 - v1
    assert removed == pytest.approx(expected_removed, rel=0.05), \
        f"bore volume removed {removed:.2f}, expected {expected_removed:.2f}"


# ── gear_internal_involute ────────────────────────────────────────────────


def test_gear_internal_volume_and_bbox():
    """m=1, z=20, outer_d=30 → pitch_r=10, ded_r=11.25, outer_r=15.
    Annular ring volume ≈ π(outer_r² − pitch_r²) · width, roughly. ± 40 %."""
    module = 1.0
    z = 20
    width = 5.0
    outer_d = 30.0
    pitch_r = module * z / 2.0
    outer_r = outer_d / 2.0

    r = GearInternalInvolute().apply(None, {
        "module_mm": module,
        "n_teeth": z,
        "pressure_angle_deg": 20.0,
        "width_mm": width,
        "outer_diameter_mm": outer_d,
    })
    assert r.body is not None
    v = _volume(r.body)
    assert v > 0.0

    # Compare to annulus between outer_r and addendum_r (smallest inner radius).
    add_r = pitch_r - module
    v_full = math.pi * outer_r * outer_r * width
    v_hole_max = math.pi * add_r * add_r * width    # if inner boundary were at add_r
    v_lo = (v_full - v_hole_max) * 0.7
    v_hi = v_full
    assert v_lo <= v <= v_hi, \
        f"ring gear volume {v:.2f} not in plausible range [{v_lo:.2f},{v_hi:.2f}]"

    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    assert xmax == pytest.approx(outer_r, abs=0.1)
    assert xmin == pytest.approx(-outer_r, abs=0.1)
    assert zmin == pytest.approx(0.0, abs=0.05)
    assert zmax == pytest.approx(width, abs=0.05)


def test_gear_internal_face_count_scales_with_teeth():
    g_small = GearInternalInvolute().apply(None, {
        "module_mm": 1.0, "n_teeth": 12, "width_mm": 3.0,
        "outer_diameter_mm": 20.0,
    })
    g_big = GearInternalInvolute().apply(None, {
        "module_mm": 1.0, "n_teeth": 30, "width_mm": 3.0,
        "outer_diameter_mm": 40.0,
    })
    assert _face_count(g_big.body) > _face_count(g_small.body)


def test_gear_internal_rejects_outer_diameter_too_small():
    # outer_r must exceed dedendum_r = pitch_r + 1.25 m.
    # For m=1, z=20: ded_r = 11.25 → outer_d must be > 22.5.
    with pytest.raises(Exception):
        GearInternalInvolute().apply(None, {
            "module_mm": 1.0,
            "n_teeth": 20,
            "width_mm": 3.0,
            "outer_diameter_mm": 20.0,   # 10 < 11.25 — should fail
        })


# ── helical_spring ────────────────────────────────────────────────────────


def test_helical_spring_volume_and_bbox():
    """coil_r=10, wire_r=1, pitch=4, turns=5.
    Helix arc length L per turn ≈ √((2π·R)² + pitch²) = √((62.83)² + 16) ≈ 62.96.
    Total ≈ 5 · 62.96 ≈ 314.8.  Volume ≈ L · π·r_wire² ≈ 314.8 · π ≈ 989.
    """
    coil_r = 10.0
    wire_r = 1.0
    pitch = 4.0
    turns = 5.0

    r = HelicalSpring().apply(None, {
        "coil_radius_mm": coil_r,
        "wire_radius_mm": wire_r,
        "pitch_mm": pitch,
        "turns": turns,
    })
    assert r.body is not None
    v = _volume(r.body)
    L = turns * math.sqrt((2.0 * math.pi * coil_r) ** 2 + pitch ** 2)
    expected = L * math.pi * wire_r * wire_r
    assert v == pytest.approx(expected, rel=0.15), \
        f"helical_spring volume {v:.2f}, expected ~{expected:.2f}"

    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    # Radial envelope: coil_r + wire_r. OCCT's Bnd_Box for a swept B-spline
    # pipe inflates slightly beyond the true geometry envelope (same looseness
    # as torus) — bound by coil_r + 3*wire_r as a generous upper limit.
    radial_true = coil_r + wire_r
    radial_loose = coil_r + 3.0 * wire_r
    assert radial_true - 0.2 <= xmax <= radial_loose
    assert -radial_loose <= xmin <= -(radial_true - 0.2)
    # Axial extent: pitch · turns + ~2·wire_r (caps at each end). OCCT's
    # Bnd_Box for swept pipes loosens by another wire_diameter in the axial
    # direction. True envelope is pitch·turns + 2·wire_r ≈ 22; allow up to
    # +3·wire_r looseness.
    height = pitch * turns
    axial = zmax - zmin
    assert height + 2 * wire_r - 0.2 <= axial <= height + 5.0 * wire_r


def test_helical_spring_axis_oriented_x():
    """When axis_direction = +X, the spring should extend along X, not Z."""
    r = HelicalSpring().apply(None, {
        "coil_radius_mm": 5.0,
        "wire_radius_mm": 0.5,
        "pitch_mm": 2.0,
        "turns": 3.0,
        "axis_origin": (0.0, 0.0, 0.0),
        "axis_direction": (1.0, 0.0, 0.0),
    })
    assert r.body is not None
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    # Length along X should match pitch·turns + 2·wire_r ± OCCT looseness.
    length_x = xmax - xmin
    axial_true = 2.0 * 3.0 + 2 * 0.5
    assert axial_true - 0.2 <= length_x <= axial_true + 5.0 * 0.5
    # Y and Z extents are radial coil_r + wire_r ≈ 5.5 each side.
    radial_true = 2 * (5.0 + 0.5)
    assert radial_true - 0.2 <= (ymax - ymin) <= radial_true + 5.0 * 0.5


def test_helical_spring_rejects_wire_too_thick():
    with pytest.raises(Exception):
        HelicalSpring().apply(None, {
            "coil_radius_mm": 2.0,
            "wire_radius_mm": 2.5,   # > coil radius
            "pitch_mm": 5.0,
            "turns": 2.0,
        })


def test_helical_spring_rejects_pitch_too_small():
    with pytest.raises(Exception):
        HelicalSpring().apply(None, {
            "coil_radius_mm": 5.0,
            "wire_radius_mm": 1.0,
            "pitch_mm": 1.0,         # < 2·wire_r
            "turns": 2.0,
        })


# ── coil_spring_rectangular ───────────────────────────────────────────────


def test_coil_spring_rectangular_volume_and_bbox():
    """coil_r=10, w=2, t=1, pitch=4, turns=4. Helix length L ≈ 4·√((20π)² + 16)
    ≈ 251.8. Volume ≈ L · w · t = 503.6.
    """
    coil_r = 10.0
    width = 2.0
    thick = 1.0
    pitch = 4.0
    turns = 4.0

    r = CoilSpringRectangular().apply(None, {
        "coil_radius_mm": coil_r,
        "wire_width_mm": width,
        "wire_thickness_mm": thick,
        "pitch_mm": pitch,
        "turns": turns,
    })
    assert r.body is not None
    v = _volume(r.body)
    L = turns * math.sqrt((2.0 * math.pi * coil_r) ** 2 + pitch ** 2)
    expected = L * width * thick
    assert v == pytest.approx(expected, rel=0.20), \
        f"rect coil volume {v:.2f}, expected ~{expected:.2f}"

    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    # Radial envelope: coil_r + thick/2. Allow OCCT Bnd_Box looseness up to
    # +thick beyond true envelope.
    rad_true = coil_r + thick / 2.0
    assert rad_true - 0.3 <= xmax <= rad_true + 2.0 * thick
    assert -(rad_true + 2.0 * thick) <= xmin <= -(rad_true - 0.3)
    # Axial extent: pitch · turns + (a fraction of width). Width direction
    # is in the profile plane perpendicular to the helix tangent (not pure
    # +Z) so the axial projection of "width" is width·cos(helix_angle).
    # Lower bound: pitch · turns; upper bound: pitch · turns + 3·width.
    height = pitch * turns
    axial = zmax - zmin
    assert height - 0.3 <= axial <= height + 3.0 * width


def test_coil_spring_rectangular_rejects_thickness_too_large():
    with pytest.raises(Exception):
        CoilSpringRectangular().apply(None, {
            "coil_radius_mm": 2.0,
            "wire_width_mm": 1.0,
            "wire_thickness_mm": 5.0,   # > 2 · coil_r
            "pitch_mm": 5.0,
            "turns": 2.0,
        })


def test_coil_spring_rectangular_rejects_pitch_lt_width():
    with pytest.raises(Exception):
        CoilSpringRectangular().apply(None, {
            "coil_radius_mm": 5.0,
            "wire_width_mm": 3.0,
            "wire_thickness_mm": 1.0,
            "pitch_mm": 1.0,            # < width
            "turns": 2.0,
        })


# ── spec metadata sanity ──────────────────────────────────────────────────


def test_all_new_skills_have_body_present_post_condition():
    from phone_designer.skills._post_conditions import PostCondition
    for cls in (
        GearExternalInvolute,
        GearInternalInvolute,
        HelicalSpring,
        CoilSpringRectangular,
    ):
        pcs = cls.spec.post_conditions
        assert any(
            isinstance(p, PostCondition) and p.kind == "body_present" for p in pcs
        ), f"{cls.spec.name} missing body_present post_condition"


def test_all_new_skills_are_atomic_create():
    for cls in (
        GearExternalInvolute,
        GearInternalInvolute,
        HelicalSpring,
        CoilSpringRectangular,
    ):
        assert cls.spec.category == "create"
        assert cls.spec.level == "atomic"
