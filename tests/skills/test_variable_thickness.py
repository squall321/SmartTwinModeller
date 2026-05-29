"""Tests for variable-thickness shell + variable-section sweep skills.

Covers:
    - shell_variable_thickness  (modify/curvature)
    - swept_pocket_variable_section  (modify/pocket)
    - surface_thicken_variable  (create)
"""
from __future__ import annotations

import math

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_curvature.shell_variable_thickness import (
    ShellVariableThickness,
)
from phone_designer.skills.modify_curvature.surface_thicken_variable import (
    SurfaceThickenVariable,
)
from phone_designer.skills.modify_pocket.swept_pocket_variable_section import (
    SweptPocketVariableSection,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


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


def _bbox(body):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(body.wrapped, bb)
    return bb.Get()  # xmin, ymin, zmin, xmax, ymax, zmax


def _make_box(length=30.0, width=30.0, height=10.0):
    return Box().apply(None, {
        "length_mm": length, "width_mm": width, "height_mm": height,
    }).body


def _dome_grid(rows=5, cols=5, x_range=(-10.0, 10.0), y_range=(-10.0, 10.0),
               z_base=0.0, curvature=0.02):
    dx = (x_range[1] - x_range[0]) / (cols - 1)
    dy = (y_range[1] - y_range[0]) / (rows - 1)
    grid = []
    for i in range(rows):
        row = []
        for j in range(cols):
            x = x_range[0] + j * dx
            y = y_range[0] + i * dy
            z = z_base - curvature * (x * x + y * y)
            row.append((x, y, z))
        grid.append(row)
    return grid


# ── shell_variable_thickness ───────────────────────────────────────────────


def test_shell_variable_thickness_open_top_uniform():
    """Open top face removed, all remaining faces at 1.0 mm.

    Verify: volume decreased significantly (close to the hollow-interior
    volume) and face count grew (inner walls added).
    """
    box = _make_box(30, 30, 10)
    v0 = _volume(box)
    fc0 = _face_count(box)

    # default=None means unmatched faces are OPEN. Match every non-top face
    # via the NOT of `face_named=top` so only the top face is open.
    r = ShellVariableThickness().apply(box, {
        "face_thickness_map": [
            {"face_selector": {
                "kind": "not",
                "inner": {"kind": "face_named", "name": "top"},
             },
             "thickness_mm": 1.0},
        ],
        "default_thickness_mm": None,
    })
    v1 = _volume(r.body)
    fc1 = _face_count(r.body)

    # Wall thickness 1mm uniform on 30×30×10 box with open top → hollow
    # interior ≈ 28×28×9 = 7056 mm³.
    removed = v0 - v1
    assert removed > 5000.0, f"shell removed too little material: {removed:.2f}"
    # Inner shell adds faces — at minimum 1 inner wall set.
    assert fc1 > fc0, (
        f"face count should grow with inner shell walls "
        f"(pre={fc0}, post={fc1})"
    )


def test_shell_variable_thickness_per_face_distinct():
    """Map two faces with two distinct thicknesses; default fills the rest.

    The bottom face is thicker (2.0 mm) than the sides (default 1.0 mm).
    Top face is open. Verify the result is a valid solid with a sensible
    volume (less than full box, more than fully-hollow box).
    """
    box = _make_box(30, 30, 10)
    v0 = _volume(box)

    r = ShellVariableThickness().apply(box, {
        "face_thickness_map": [
            {"face_selector": {"kind": "face_named", "name": "bottom"},
             "thickness_mm": 2.0},
        ],
        "default_thickness_mm": 1.0,
    })
    v1 = _volume(r.body)

    # Thicker bottom keeps more material than uniform-1mm shell, but the
    # top is still open so plenty of material is removed.
    removed = v0 - v1
    assert removed > 4000.0, f"shell removed too little: {removed:.2f}"
    assert removed < v0, "shell should not remove the whole body"
    # Body still measurable (post_condition body_present is implicit).
    assert v1 > 1000.0, f"final volume too small: {v1:.2f}"


def test_shell_variable_thickness_post_condition_kind():
    spec = ShellVariableThickness.spec
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── swept_pocket_variable_section ──────────────────────────────────────────


def test_swept_pocket_variable_section_circle_to_rectangle():
    """Polyline path from round entry to rectangular exit.

    Verify: volume decreased, removal > minimum-section · path length
    (would-be-uniform bound) and < maximum-section · path length.
    """
    body = _make_box(50, 50, 12)
    v0 = _volume(body)

    r = SweptPocketVariableSection().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "start_sketch": {
            "kind": "circle", "diameter_mm": 4.0,
        },
        "end_sketch": {
            "kind": "rectangle", "length_mm": 4.0, "width_mm": 4.0,
        },
        # Path goes straight down 6mm — entry at top face, exit 6mm below.
        # Profile is in its local XY at z=0, +Z = tangent. For a -Z pointing
        # tangent, profile local +Z is mapped to world -Z.
        "path_points": [
            (0.0, 0.0, 12.0),
            (0.0, 0.0, 6.0),
        ],
        "interpolation": "linear",
    })
    v1 = _volume(r.body)
    removed = v0 - v1
    assert v1 < v0, "volume should decrease"

    # Section areas: π·2² ≈ 12.57 (start), 4·4 = 16 (end). Path length = 6.
    # Removed ≈ 6 · avg_area ≈ 6 · 14 ≈ 84. Allow a wide band — pipe shell
    # interpolation is non-linear and OCCT may over/under-shoot a bit.
    assert 30.0 < removed < 200.0, (
        f"removed volume {removed:.2f} mm³ outside expected band "
        f"[30, 200] for tapered circle→square sweep"
    )


def test_swept_pocket_variable_section_spline_path():
    """Spline path with two different-size profiles."""
    body = _make_box(50, 50, 12)
    v0 = _volume(body)

    r = SweptPocketVariableSection().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "start_sketch": {
            "kind": "circle", "diameter_mm": 3.0,
        },
        "end_sketch": {
            "kind": "circle", "diameter_mm": 5.0,
        },
        # gentle curve down inside the box (stays well above the floor)
        "path_points": [
            (-10.0, 0.0, 12.0),
            (-5.0, 0.0, 10.0),
            (0.0, 0.0, 9.0),
            (5.0, 0.0, 10.0),
        ],
        "interpolation": "spline",
    })
    v1 = _volume(r.body)
    assert v1 < v0, "spline-path variable sweep must remove material"


def test_swept_pocket_variable_section_post_condition_kind():
    spec = SweptPocketVariableSection.spec
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── surface_thicken_variable ───────────────────────────────────────────────


def test_surface_thicken_variable_basic():
    """Uniform thickness 2mm across a flat-ish 5×5 grid → solid with bbox
    Z-extent ≈ 2 mm and positive volume."""
    grid = _dome_grid(rows=5, cols=5,
                      x_range=(-5.0, 5.0), y_range=(-5.0, 5.0),
                      z_base=0.0, curvature=0.0)
    # Constant 2.0 thickness everywhere
    thk = [[2.0] * 5 for _ in range(5)]

    r = SurfaceThickenVariable().apply(None, {
        "point_grid": grid,
        "thickness_map": thk,
    })
    assert r.body is not None

    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    # For flat base at z=0, normal=+Z, offset surface at z=2. bbox z-extent ≈ 2.
    z_extent = zmax - zmin
    assert 1.5 < z_extent < 2.5, (
        f"flat surface thickened by 2mm should have Z-extent ≈ 2, got {z_extent:.3f}"
    )
    v = _volume(r.body)
    # Volume ≈ 10 · 10 · 2 = 200 mm³.
    assert v > 100.0, f"volume too small: {v:.2f}"


def test_surface_thicken_variable_varying_thickness():
    """Thickness varies across the grid: 1.0 mm at corners, 3.0 mm in
    the center. The resulting solid should have a Z-extent of AT LEAST
    the maximum thickness (modulo NURBS smoothing).
    """
    grid = _dome_grid(rows=5, cols=5,
                      x_range=(-5.0, 5.0), y_range=(-5.0, 5.0),
                      z_base=0.0, curvature=0.0)

    # Center thicker than edges
    thk = [
        [1.0, 1.0, 1.5, 1.0, 1.0],
        [1.0, 2.0, 2.5, 2.0, 1.0],
        [1.5, 2.5, 3.0, 2.5, 1.5],
        [1.0, 2.0, 2.5, 2.0, 1.0],
        [1.0, 1.0, 1.5, 1.0, 1.0],
    ]

    r = SurfaceThickenVariable().apply(None, {
        "point_grid": grid,
        "thickness_map": thk,
    })
    assert r.body is not None

    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    z_extent = zmax - zmin
    # The thickest point is 3.0 mm; NURBS smoothing may slightly reduce the
    # extent (offset surface is fit through smoothed points). Accept a band.
    assert 2.4 < z_extent < 3.6, (
        f"variable-thickness solid Z-extent should be ≈ 3 (max thickness), "
        f"got {z_extent:.3f}"
    )
    # Volume should also be greater than the flat 2 mm case below (10×10×~2)
    # and less than the 10×10×3 upper bound.
    v = _volume(r.body)
    assert 100.0 < v < 300.0, (
        f"variable-thickness volume {v:.2f} mm³ outside expected band"
    )


def test_surface_thicken_variable_validation_mismatched_shape():
    """thickness_map must match point_grid shape — pydantic should reject."""
    import pytest as _pytest

    grid = _dome_grid(rows=4, cols=4,
                      x_range=(-5.0, 5.0), y_range=(-5.0, 5.0),
                      z_base=0.0, curvature=0.0)
    # 3×3 thickness for 4×4 grid → mismatch
    thk = [[1.0] * 3 for _ in range(3)]
    with _pytest.raises(Exception):
        SurfaceThickenVariable.Args.model_validate({
            "point_grid": grid,
            "thickness_map": thk,
        })


def test_surface_thicken_variable_post_condition_kind():
    spec = SurfaceThickenVariable.spec
    assert any(pc.kind == "body_present" for pc in spec.post_conditions)
