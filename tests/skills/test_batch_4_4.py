"""Phase 4 batch 4-4: 고급 곡률 + grille_pattern."""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_curvature.loft_side_profile import LoftSideProfile
from phone_designer.skills.modify_curvature.surface_offset import SurfaceOffset
from phone_designer.skills.modify_curvature.swept_relief import SweptRelief
from phone_designer.skills.modify_curvature.variable_radius_fillet import VariableRadiusFillet
from phone_designer.skills.modify_pocket.grille_pattern import GrillePattern


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


# ── variable_radius_fillet ──────────────────────────────────────────────────


def test_variable_radius_fillet_4_edges():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = VariableRadiusFillet().apply(box, {
        "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
        "radii_mm": [0.5, 1.0, 1.5, 2.0],
    })
    # 4 edge fillet → face 수 늘어남
    from phone_designer.skills._resolvers import _all_faces
    assert len(_all_faces(r.body.wrapped)) > 6


def test_variable_radius_fillet_cycles_radii():
    """edges 6개에 radii 2개 → cycle."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    # Z-axis edges 4개. cycle 2 → 0,1,0,1
    r = VariableRadiusFillet().apply(box, {
        "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
        "radii_mm": [0.5, 1.5],
    })
    assert r.selector_freeze.matched_count == 4


# ── loft_side_profile ───────────────────────────────────────────────────────


def test_loft_circle_to_circle():
    """D=10 → D=6 frustum (cone-like)."""
    r = LoftSideProfile().apply(None, {
        "bottom": {"kind": "circle", "diameter_mm": 10.0, "z_mm": 0.0},
        "top":    {"kind": "circle", "diameter_mm": 6.0,  "z_mm": 8.0},
    })
    # frustum 부피 = π × h × (R1² + R1R2 + R2²) / 3
    # = π × 8 × (25 + 15 + 9) / 3 = π × 8 × 49/3 ≈ 410.5
    expected = 3.14159 * 8 * (25 + 15 + 9) / 3
    assert abs(_volume(r.body) - expected) / expected < 0.05


def test_loft_rectangle_to_rectangle():
    r = LoftSideProfile().apply(None, {
        "bottom": {"kind": "rectangle", "length_mm": 10.0, "width_mm": 10.0, "z_mm": 0.0},
        "top":    {"kind": "rectangle", "length_mm": 6.0, "width_mm": 6.0, "z_mm": 8.0},
    })
    # pyramidal frustum 부피 = h/3 × (A1 + A2 + sqrt(A1*A2)) = 8/3 × (100+36+60) = 522.7
    expected = 8 / 3 * (100 + 36 + 60)
    assert abs(_volume(r.body) - expected) / expected < 0.1


def test_loft_kind_mismatch_raises():
    with pytest.raises(ValueError, match="kind"):
        LoftSideProfile().apply(None, {
            "bottom": {"kind": "circle", "diameter_mm": 10.0, "z_mm": 0.0},
            "top":    {"kind": "rectangle", "length_mm": 8, "width_mm": 8, "z_mm": 5.0},
        })


# ── swept_relief ─────────────────────────────────────────────────────────────


def test_swept_relief_removes_volume():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    v_before = _volume(box)
    r = SweptRelief().apply(box, {
        "start": (-8.0, 0.0, 5.0),
        "end":   (8.0, 0.0, 5.0),
        "width_mm": 2.0,
        "depth_mm": 3.0,
    })
    # cut box: 16 × 2 × 3 = 96
    expected_loss = 16 * 2 * 3
    actual = v_before - _volume(r.body)
    assert abs(actual - expected_loss) / expected_loss < 0.05


def test_swept_relief_non_xy_path_raises():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    with pytest.raises(NotImplementedError, match="XY-plane"):
        SweptRelief().apply(box, {
            "start": (0, 0, 0), "end": (0, 0, 5),     # dz != 0
            "width_mm": 1, "depth_mm": 1,
        })


# ── surface_offset ───────────────────────────────────────────────────────────


def test_surface_offset_positive_expands():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    v_before = _volume(box)
    r = SurfaceOffset().apply(box, {"offset_mm": 0.5})
    assert _volume(r.body) > v_before


def test_surface_offset_negative_shrinks():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    v_before = _volume(box)
    r = SurfaceOffset().apply(box, {"offset_mm": -0.5})
    assert _volume(r.body) < v_before


def test_surface_offset_zero_noop():
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = SurfaceOffset().apply(box, {"offset_mm": 0.0})
    # no-op
    assert abs(_volume(r.body) - _volume(box)) < 1e-6


# ── grille_pattern ───────────────────────────────────────────────────────────


def test_grille_hex_pattern_drills_multiple_holes():
    box = Box().apply(None, {"length_mm": 30, "width_mm": 20, "height_mm": 5}).body
    v_before = _volume(box)
    r = GrillePattern().apply(box, {
        "center": (0.0, 0.0, 5.0),
        "pattern": "hex",
        "window_length_mm": 12.0,
        "window_width_mm": 6.0,
        "hole_diameter_mm": 0.8,
        "spacing_mm": 1.5,
        "depth_mm": None,    # through
        "direction": "-Z",
    })
    # 여러 hole → 부피 감소
    assert _volume(r.body) < v_before


def test_grille_grid_pattern():
    box = Box().apply(None, {"length_mm": 30, "width_mm": 20, "height_mm": 5}).body
    v_before = _volume(box)
    r = GrillePattern().apply(box, {
        "center": (0.0, 0.0, 5.0),
        "pattern": "grid",
        "window_length_mm": 10.0,
        "window_width_mm": 6.0,
        "hole_diameter_mm": 0.8,
        "spacing_mm": 2.0,
        "depth_mm": None,
        "direction": "-Z",
    })
    assert _volume(r.body) < v_before


def test_grille_radial_pattern():
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 5}).body
    v_before = _volume(box)
    r = GrillePattern().apply(box, {
        "center": (0.0, 0.0, 5.0),
        "pattern": "radial",
        "window_length_mm": 16.0,
        "window_width_mm": 16.0,
        "hole_diameter_mm": 0.8,
        "spacing_mm": 2.5,
        "depth_mm": None,
        "direction": "-Z",
    })
    assert _volume(r.body) < v_before


def test_grille_spec_is_macro():
    spec = GrillePattern.spec
    assert spec.level == "macro"
    assert "hole_array" in spec.expansion


# ── manifest ────────────────────────────────────────────────────────────────


def test_manifest_includes_batch_4_4():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    expected = {"variable_radius_fillet", "loft_side_profile", "swept_relief",
                "surface_offset", "grille_pattern"}
    assert expected <= names
