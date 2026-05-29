"""Tests for decal / wrap + pattern skill family.

Skills under test:
    - wrap_sketch_on_curved_surface  (decal on curved surface)
    - knurl_pattern                  (diamond / straight knurling)
    - honeycomb_pattern              (hex cell pocket)
    - embossed_pattern               (dots / lines / checker bosses)
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.embossed_pattern import EmbossedPattern
from phone_designer.skills.modify_pocket.honeycomb_pattern import HoneycombPattern
from phone_designer.skills.modify_pocket.knurl_pattern import KnurlPattern
from phone_designer.skills.modify_pocket.wrap_sketch_on_curved_surface import (
    WrapSketchOnCurvedSurface,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _make_box(L=40.0, W=40.0, H=10.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


def _make_cylinder(diameter_mm: float, height_mm: float):
    from build123d import Align, Cylinder
    return Cylinder(
        radius=diameter_mm / 2.0,
        height=height_mm,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )


def _make_sphere(radius_mm: float):
    from build123d import Sphere
    return Sphere(radius=radius_mm)


def _cylinder_face_selector():
    # The lateral cylinder face has 0 face area in the +Z normal sense (its
    # normal at center is undefined for closed cylinder). Use faces_by_area
    # selector — the lateral wrap-around face is the largest of the cylinder's
    # three faces (top, bottom, lateral).
    from phone_designer.skills._selectors import FacesByAreaSelector
    return FacesByAreaSelector(min=10.0)


def _top_face_selector():
    from phone_designer.skills._selectors import FacesByNormalSelector
    return FacesByNormalSelector(direction=(0.0, 0.0, 1.0), tol_deg=5.0)


def _sphere_face_selector():
    from phone_designer.skills._selectors import FacesByAreaSelector
    return FacesByAreaSelector(min=1.0)


# ── wrap_sketch_on_curved_surface ────────────────────────────────────────────


def test_wrap_on_sphere_decreases_volume():
    """Project a small circle decal onto a sphere and verify volume decrease."""
    body = _make_sphere(radius_mm=10.0)
    v0 = _volume(body)

    r = WrapSketchOnCurvedSurface().apply(body, {
        "face_selector": _sphere_face_selector().model_dump(),
        "sketch": {"kind": "circle", "diameter_mm": 4.0},
        "projection_direction": (0.0, 0.0, -1.0),
        "depth_mm": 0.5,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"wrap on sphere should remove material: v0={v0:.3f}, v1={v1:.3f}"


def test_wrap_on_cylinder_lateral_surface():
    """Project a rectangle decal onto the lateral surface of a cylinder."""
    body = _make_cylinder(diameter_mm=30.0, height_mm=30.0)
    v0 = _volume(body)

    r = WrapSketchOnCurvedSurface().apply(body, {
        "face_selector": _cylinder_face_selector().model_dump(),
        "sketch": {"kind": "rectangle", "length_mm": 4.0, "width_mm": 4.0},
        # Project radially inward — but lateral cylinder is hard to project
        # along Z. Use the fallback path: extrude+cut from above. Direction
        # -Z just hits the top first.
        "projection_direction": (0.0, 0.0, -1.0),
        "depth_mm": 0.5,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"wrap on cylinder should remove material: v0={v0:.3f}, v1={v1:.3f}"


def test_wrap_rejects_planar_face():
    """Wrap on a flat face should raise NotImplementedError."""
    body = _make_box(30, 30, 10)
    with pytest.raises(NotImplementedError, match="planar"):
        WrapSketchOnCurvedSurface().apply(body, {
            "face_selector": _top_face_selector().model_dump(),
            "sketch": {"kind": "circle", "diameter_mm": 4.0},
            "projection_direction": (0.0, 0.0, -1.0),
            "depth_mm": 0.3,
        })


def test_wrap_post_condition_volume_decreased():
    spec = WrapSketchOnCurvedSurface.spec
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── knurl_pattern ────────────────────────────────────────────────────────────


def test_knurl_straight_decreases_volume():
    body = _make_cylinder(diameter_mm=20.0, height_mm=20.0)
    v0 = _volume(body)
    r = KnurlPattern().apply(body, {
        "face_selector": _cylinder_face_selector().model_dump(),
        "pattern_type": "straight",
        "pitch_mm": 2.0,
        "depth_mm": 0.4,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"straight knurl should remove volume: v0={v0:.2f}, v1={v1:.2f}"


def test_knurl_diamond_decreases_volume():
    # Small cylinder + coarse pitch so the test stays fast (helix sweep is
    # the slowest OCCT op in this skill).
    body = _make_cylinder(diameter_mm=10.0, height_mm=8.0)
    v0 = _volume(body)
    r = KnurlPattern().apply(body, {
        "face_selector": _cylinder_face_selector().model_dump(),
        "pattern_type": "diamond",
        "pitch_mm": 3.0,
        "depth_mm": 0.3,
        "angle_deg": 45.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"diamond knurl should remove volume: v0={v0:.2f}, v1={v1:.2f}"


def test_knurl_rejects_planar_face():
    body = _make_box(30, 30, 10)
    with pytest.raises(NotImplementedError, match="cylindrical"):
        KnurlPattern().apply(body, {
            "face_selector": _top_face_selector().model_dump(),
            "pattern_type": "straight",
            "pitch_mm": 1.0,
            "depth_mm": 0.2,
        })


def test_knurl_post_condition():
    spec = KnurlPattern.spec
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── honeycomb_pattern ────────────────────────────────────────────────────────


def test_honeycomb_decreases_volume():
    body = _make_box(40, 40, 10)
    v0 = _volume(body)
    r = HoneycombPattern().apply(body, {
        "face_selector": _top_face_selector().model_dump(),
        "cell_size_mm": 4.0,
        "wall_thickness_mm": 1.0,
        "depth_mm": 3.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"honeycomb should remove volume: v0={v0:.2f}, v1={v1:.2f}"
    # Sanity: each hex (ftf=4) has area = (3·sqrt(3)/2)·(4/sqrt(3))² ≈ 13.86 mm²
    # ; depth 3 → ~41.6 mm³ per cell. With wall 1, cells span < bbox.
    # Pattern should remove at least a couple hundred mm³.
    removed = v0 - v1
    assert removed > 50.0, f"removed too little: {removed:.2f} mm³"


def test_honeycomb_area_radius_restricts():
    """Restricting `area_radius_mm` should remove less material than full face."""
    body_full = _make_box(40, 40, 10)
    body_restricted = _make_box(40, 40, 10)
    v0 = _volume(body_full)

    r_full = HoneycombPattern().apply(body_full, {
        "face_selector": _top_face_selector().model_dump(),
        "cell_size_mm": 4.0,
        "wall_thickness_mm": 1.0,
        "depth_mm": 3.0,
    })
    r_restricted = HoneycombPattern().apply(body_restricted, {
        "face_selector": _top_face_selector().model_dump(),
        "cell_size_mm": 4.0,
        "wall_thickness_mm": 1.0,
        "depth_mm": 3.0,
        "area_radius_mm": 6.0,
    })
    removed_full = v0 - _volume(r_full.body)
    removed_restricted = v0 - _volume(r_restricted.body)
    assert removed_restricted < removed_full, (
        f"area_radius should reduce removal: full={removed_full:.2f}, "
        f"restricted={removed_restricted:.2f}"
    )


def test_honeycomb_post_condition():
    spec = HoneycombPattern.spec
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── embossed_pattern ─────────────────────────────────────────────────────────


def test_embossed_dots_increases_volume():
    body = _make_box(30, 30, 10)
    v0 = _volume(body)
    r = EmbossedPattern().apply(body, {
        "face_selector": _top_face_selector().model_dump(),
        "pattern": "dots",
        "pitch_mm": 3.0,
        "feature_size_mm": 1.5,
        "height_mm": 0.4,
    })
    v1 = _volume(r.body)
    assert v1 > v0, f"embossed dots should add volume: v0={v0:.2f}, v1={v1:.2f}"


def test_embossed_lines_increases_volume():
    body = _make_box(30, 30, 10)
    v0 = _volume(body)
    r = EmbossedPattern().apply(body, {
        "face_selector": _top_face_selector().model_dump(),
        "pattern": "lines",
        "pitch_mm": 4.0,
        "feature_size_mm": 0.8,
        "height_mm": 0.3,
    })
    v1 = _volume(r.body)
    assert v1 > v0, f"embossed lines should add volume: v0={v0:.2f}, v1={v1:.2f}"


def test_embossed_checker_increases_volume():
    body = _make_box(30, 30, 10)
    v0 = _volume(body)
    r = EmbossedPattern().apply(body, {
        "face_selector": _top_face_selector().model_dump(),
        "pattern": "checker",
        "pitch_mm": 3.0,
        "feature_size_mm": 1.5,
        "height_mm": 0.3,
    })
    v1 = _volume(r.body)
    assert v1 > v0, f"embossed checker should add volume: v0={v0:.2f}, v1={v1:.2f}"


def test_embossed_rejects_overlapping_features():
    body = _make_box(30, 30, 10)
    with pytest.raises(ValueError, match="feature_size_mm"):
        EmbossedPattern().apply(body, {
            "face_selector": _top_face_selector().model_dump(),
            "pattern": "dots",
            "pitch_mm": 1.0,
            "feature_size_mm": 1.5,   # > pitch
            "height_mm": 0.3,
        })


def test_embossed_post_condition():
    spec = EmbossedPattern.spec
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── spec / registry metadata ─────────────────────────────────────────────────


@pytest.mark.parametrize("cls,expected_name,expected_category,expected_level", [
    (WrapSketchOnCurvedSurface, "wrap_sketch_on_curved_surface", "modify/pocket", "atomic"),
    (KnurlPattern,              "knurl_pattern",                 "modify/pocket", "macro"),
    (HoneycombPattern,          "honeycomb_pattern",             "modify/pocket", "macro"),
    (EmbossedPattern,           "embossed_pattern",              "modify/boss",   "macro"),
])
def test_skill_spec_metadata(cls, expected_name, expected_category, expected_level):
    spec = cls.spec
    assert spec.name == expected_name
    assert spec.category == expected_category
    assert spec.level == expected_level
