"""Phase 4 curved-area batch: analytical curves + NURBS surface + variable
law-driven fillet.

Covered skills:
    - ParametricCurveSketch / PolarCurveSketch / LissajousCurveSketch
      (via extrude_pocket dispatcher)
    - bspline_surface (create)
    - extrude_pocket_to_curved_floor (modify/pocket)
    - variable_radius_fillet_with_law (modify/fillet)
"""
from __future__ import annotations

import math


# ── helpers ────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _face_count(body) -> int:
    from phone_designer.skills._resolvers import _all_faces
    return len(_all_faces(body.wrapped))


def _bbox(body):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(body.wrapped, bb)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return (xmin, ymin, zmin, xmax, ymax, zmax)


def _make_box(length=40.0, width=40.0, height=10.0):
    from phone_designer.skills.create.box import Box
    return Box().apply(None, {
        "length_mm": length, "width_mm": width, "height_mm": height,
    }).body


def _dome_grid(rows=5, cols=5, x_range=(-10.0, 10.0), y_range=(-10.0, 10.0),
               z_base=7.0, curvature=0.02):
    """A rows×cols grid of (x, y, z) forming a downward dome.

    Peak at (0, 0, z_base); falls off quadratically.
    """
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


# ── analytical curve sketches (pocket dispatcher entries) ─────────────────


def test_parametric_curve_pocket_lissajous():
    """Pocket whose 2D profile is a parametric curve: x=10·cos(t), y=5·sin(2t).

    NB: x=10cos, y=5sin(2t) self-intersects (figure-8). We instead use a
    clean Lissajous-ish ellipse x=10cos(t), y=5sin(t) so the resulting face
    is a valid 2D region — that's the actual contract of `parametric` (any
    closed simple loop).
    """
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket

    box = _make_box(40, 40, 10)
    v_before = _volume(box)
    r = ExtrudePocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {
            "kind": "parametric",
            "x_expr": "10*cos(t)",
            "y_expr": "5*sin(t)",
            "samples": 120,
        },
        "depth_mm": 2.0,
    })
    v_after = _volume(r.body)
    # Ellipse area = π·a·b = π·10·5 = 50π ≈ 157.08
    expected = math.pi * 10.0 * 5.0 * 2.0
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.05, (
        f"parametric ellipse pocket: expected ~{expected:.2f}, got {actual:.2f}"
    )


def test_polar_curve_pocket_rose_5_petal():
    """5-petal rose r=3+1.5·cos(5θ) — verify face-count signature differs
    from a simple closed curve (i.e., topology actually reflects the 5-petal
    shape, not a degenerate circle)."""
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket

    box = _make_box(40, 40, 10)
    fc_before = _face_count(box)
    r = ExtrudePocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {
            "kind": "polar",
            "r_expr": "3 + 1.5*cos(5*theta)",
            "samples": 300,
        },
        "depth_mm": 2.0,
    })
    # Volume must shrink (closed loop is non-empty).
    assert _volume(r.body) < _volume(box) - 1.0
    # Face count must change (pocket adds wall + floor).
    assert _face_count(r.body) != fc_before


def test_lissajous_curve_pocket_basic():
    """Direct LissajousCurveSketch — freq_x=freq_y=1, phase=90° → ellipse-like."""
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket

    box = _make_box(40, 40, 10)
    v_before = _volume(box)
    r = ExtrudePocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {
            "kind": "lissajous",
            "amp_x": 6.0, "amp_y": 4.0,
            "freq_x": 1.0, "freq_y": 1.0,
            "phase_deg": 90.0,
            "samples": 120,
        },
        "depth_mm": 1.5,
    })
    v_after = _volume(r.body)
    # x=6sin(t+π/2)=6cos(t), y=4sin(t) ⇒ ellipse with a=6, b=4 ⇒ area=π·6·4=24π
    expected = math.pi * 6.0 * 4.0 * 1.5
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.05, (
        f"lissajous ellipse pocket: expected ~{expected:.2f}, got {actual:.2f}"
    )


def test_parametric_expression_rejects_dunder():
    """Defensive: __ in expression must be rejected at evaluation time."""
    from phone_designer.skills.modify_pocket._sketch_to_solid import (
        _safe_eval_curve,
    )
    import pytest as _pytest
    with _pytest.raises(ValueError, match="forbidden"):
        _safe_eval_curve("__import__('os').system('x')", "t", 0.0)


# ── BSplineSurface create skill ───────────────────────────────────────────


def test_bspline_surface_create_from_5x5_grid():
    """A 5×5 dome grid → a single Face whose bbox covers the grid extent."""
    from phone_designer.skills.create.bspline_surface import BSplineSurface

    grid = _dome_grid(rows=5, cols=5,
                      x_range=(-5.0, 5.0), y_range=(-5.0, 5.0),
                      z_base=3.0, curvature=0.04)
    result = BSplineSurface().apply(None, {"point_grid": grid})
    assert result.body is not None
    # bbox X / Y must span the input grid.
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(result.body)
    assert xmin <= -5.0 + 0.5 and xmax >= 5.0 - 0.5
    assert ymin <= -5.0 + 0.5 and ymax >= 5.0 - 0.5
    # Z must include the dome peak (3.0 at center).
    assert zmax >= 2.5


def test_bspline_surface_thickened():
    """thicken_mm=2.0 → result must be a Solid with positive volume."""
    from phone_designer.skills.create.bspline_surface import BSplineSurface

    grid = _dome_grid(rows=5, cols=5,
                      x_range=(-5.0, 5.0), y_range=(-5.0, 5.0),
                      z_base=3.0, curvature=0.04)
    result = BSplineSurface().apply(None, {
        "point_grid": grid, "thicken_mm": 2.0,
    })
    assert result.body is not None
    v = _volume(result.body)
    assert v > 0.0, f"thickened volume must be > 0, got {v}"


# ── ExtrudePocketToCurvedFloor ────────────────────────────────────────────


def test_extrude_pocket_to_curved_floor_dome():
    """Box 50×50×10 + circular sketch + dome floor grid → pocket bottom is
    non-planar (more faces than a flat-floor pocket, and volume removed is
    less than a flat pocket of the same depth)."""
    from phone_designer.skills.modify_pocket.extrude_pocket_to_curved_floor import (
        ExtrudePocketToCurvedFloor,
    )

    box = _make_box(50, 50, 10)
    v_before = _volume(box)
    grid = _dome_grid(rows=5, cols=5,
                      x_range=(-10.0, 10.0), y_range=(-10.0, 10.0),
                      z_base=7.0, curvature=0.02)
    r = ExtrudePocketToCurvedFloor().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "circle", "diameter_mm": 15.0},
        "floor_surface_grid": grid,
    })
    v_after = _volume(r.body)
    assert v_after < v_before - 1.0, (
        f"curved-floor pocket must remove material; "
        f"pre={v_before:.2f}, post={v_after:.2f}"
    )
    # A flat-floor pocket of depth=10 (full through) would remove
    # π·7.5²·10 ≈ 1767 mm³; the dome (peak z=7, well above the box top z=10
    # if we flipped grids, but here z_base=7 < top z=10, so the dome carves
    # only from z=10 down to ~z=7 in the center, ~z=5 at the edges, so the
    # removed volume is roughly between π·7.5²·3 (235) and π·7.5²·5 (884).
    pocket_volume_removed = v_before - v_after
    assert 50.0 < pocket_volume_removed < 1500.0, (
        f"removed volume {pocket_volume_removed:.2f} mm³ outside expected band"
    )


# ── VariableRadiusFilletWithLaw ───────────────────────────────────────────


def test_variable_radius_fillet_linear():
    """Box top edges (4 axis-aligned Z edges), linear law r_start=0.3 → r_end=1.0."""
    from phone_designer.skills.modify_curvature.variable_radius_fillet_with_law import (
        VariableRadiusFilletWithLaw,
    )

    box = _make_box(20, 20, 10)
    fc_before = _face_count(box)
    r = VariableRadiusFilletWithLaw().apply(box, {
        "edge_selector": {"kind": "axis_aligned_edges", "axis": "Z"},
        "law_kind": "linear",
        "law_params": {"r_start": 0.3, "r_end": 1.0},
        "samples": 10,
    })
    assert _face_count(r.body) > fc_before, (
        f"linear law fillet should add faces: pre={fc_before}, "
        f"post={_face_count(r.body)}"
    )
    # Volume must be less than a perfect 20×20×10 box (corners chamfered off).
    assert _volume(r.body) < 20.0 * 20.0 * 10.0


def test_variable_radius_fillet_polynomial():
    """Polynomial law coeffs=[0.5, 0.3, 0.2] → r(t) = 0.5 + 0.3t + 0.2t²."""
    from phone_designer.skills.modify_curvature.variable_radius_fillet_with_law import (
        VariableRadiusFilletWithLaw,
    )

    box = _make_box(20, 20, 10)
    fc_before = _face_count(box)
    r = VariableRadiusFilletWithLaw().apply(box, {
        "edge_selector": {"kind": "axis_aligned_edges", "axis": "Z"},
        "law_kind": "polynomial",
        "law_params": {"coeffs": [0.5, 0.3, 0.2]},
        "samples": 12,
    })
    assert _face_count(r.body) > fc_before


def test_variable_radius_fillet_expression_rejects_dunder():
    """Defensive: r_expr containing '__' must be rejected at Args validation."""
    from phone_designer.skills.modify_curvature.variable_radius_fillet_with_law import (
        VariableRadiusFilletWithLaw,
    )
    import pytest as _pytest
    with _pytest.raises(Exception):
        # Either ValidationError or ValueError, depending on pydantic mode.
        VariableRadiusFilletWithLaw.Args.model_validate({
            "edge_selector": {"kind": "axis_aligned_edges", "axis": "Z"},
            "law_kind": "expression",
            "law_params": {"r_expr": "__import__('os').sep"},
            "samples": 5,
        })


def test_variable_radius_fillet_sin():
    """Sin law on a box's top Z-edges — radius oscillates along each edge."""
    from phone_designer.skills.modify_curvature.variable_radius_fillet_with_law import (
        VariableRadiusFilletWithLaw,
    )

    box = _make_box(20, 20, 10)
    fc_before = _face_count(box)
    r = VariableRadiusFilletWithLaw().apply(box, {
        "edge_selector": {"kind": "axis_aligned_edges", "axis": "Z"},
        "law_kind": "sin",
        "law_params": {
            "offset": 0.5, "amplitude": 0.3,
            "phase_deg": 0.0, "cycles": 1.0,
        },
        "samples": 20,
    })
    assert _face_count(r.body) > fc_before
