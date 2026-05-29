"""Phase 4 batch: advanced sketches (polygon/slot/bspline/composite/ellipse) +
blended pocket/boss + loft pocket."""
from __future__ import annotations

import math

import pytest


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


# ── PolygonSketch ─────────────────────────────────────────────────────────


def test_polygon_sketch_pocket():
    """Hexagonal pocket in a 40×40×10 box. Volume should drop by the hex prism volume."""
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket

    box = _make_box()
    v_before = _volume(box)
    r_hex = 5.0
    verts = [
        (r_hex * math.cos(math.radians(60 * i)),
         r_hex * math.sin(math.radians(60 * i)))
        for i in range(6)
    ]
    depth = 3.0
    r = ExtrudePocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "polygon", "vertices": verts},
        "depth_mm": depth,
    })
    v_after = _volume(r.body)
    # regular hexagon area = (3*sqrt(3)/2) * r²
    hex_area = (3.0 * math.sqrt(3) / 2.0) * r_hex * r_hex
    expected = hex_area * depth
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.05, (
        f"hex pocket: expected ~{expected:.2f}, got {actual:.2f}"
    )
    # face count: box (6) + 6 wall + 1 floor = 13
    assert _face_count(r.body) >= 12


def test_polygon_sketch_with_vertex_fillet():
    """Hexagon with corner_fillet_r=0.5: topology should differ from sharp hex
    (more faces because each corner becomes a small cylindrical patch)."""
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket

    r_hex = 5.0
    verts = [
        (r_hex * math.cos(math.radians(60 * i)),
         r_hex * math.sin(math.radians(60 * i)))
        for i in range(6)
    ]
    sharp_box = _make_box()
    sharp = ExtrudePocket().apply(sharp_box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "polygon", "vertices": verts},
        "depth_mm": 3.0,
    })
    fc_sharp = _face_count(sharp.body)

    round_box = _make_box()
    rounded = ExtrudePocket().apply(round_box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "polygon", "vertices": verts, "corner_fillet_r": 0.5},
        "depth_mm": 3.0,
    })
    fc_rounded = _face_count(rounded.body)
    # Rounded version: each hex corner → cylindrical strip → more wall faces.
    assert fc_rounded > fc_sharp, (
        f"vertex fillet should add faces; sharp={fc_sharp} rounded={fc_rounded}"
    )


# ── SlotSketch ───────────────────────────────────────────────────────────


def test_slot_sketch_pocket():
    """Slot 12×4 pocket: long axis along X, verify bbox spans expected range."""
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket

    box = _make_box(40, 40, 10)
    v_before = _volume(box)
    L, W, d = 12.0, 4.0, 2.0
    r = ExtrudePocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "slot", "length_mm": L, "width_mm": W},
        "depth_mm": d,
    })
    v_after = _volume(r.body)
    # slot area = rectangle (L-W)·W + π·(W/2)²
    slot_area = (L - W) * W + math.pi * (W / 2.0) ** 2
    expected = slot_area * d
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.05, (
        f"slot pocket: expected ~{expected:.2f}, got {actual:.2f}"
    )


def test_slot_sketch_rotation():
    """Slot rotated 90° → long axis along Y."""
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket

    box = _make_box(40, 40, 10)
    r = ExtrudePocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {
            "kind": "slot", "length_mm": 12.0, "width_mm": 4.0,
            "rotation_deg": 90.0,
        },
        "depth_mm": 2.0,
    })
    assert r.body is not None  # rotation builds + cuts cleanly


# ── BSplineSketch ────────────────────────────────────────────────────────


def test_bspline_sketch_pocket():
    """6-point closed B-spline pocket → smooth oval-ish shape, volume reduces."""
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket

    box = _make_box(40, 40, 10)
    v_before = _volume(box)
    pts = [
        (6.0, 0.0), (4.0, 4.0), (-3.0, 5.0),
        (-6.0, 0.0), (-3.0, -5.0), (4.0, -4.0),
    ]
    r = ExtrudePocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "bspline_closed", "fit_points": pts},
        "depth_mm": 2.0,
    })
    v_after = _volume(r.body)
    assert v_after < v_before
    # rough sanity: enclosed area is at most bbox of points = 12*10 = 120,
    # and at least ~30 (very-loose). depth 2 → loss in [60, 240].
    loss = v_before - v_after
    assert 30 < loss < 300


# ── CompositeSketch ──────────────────────────────────────────────────────


def test_composite_sketch_round_corner_rectangle():
    """Build a rounded rectangle via 4 lines + 4 arcs in composite; volume
    should approximately match a RoundedRectSketch with same dimensions."""
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket

    L, W, r_corner = 10.0, 6.0, 1.0

    # Composite: trace the perimeter CCW starting at bottom-left straight (after corner)
    # Corner arc centers are at (±(L/2-r), ±(W/2-r)). Tangent points are at
    # offsets (±(L/2-r), ±W/2) on horizontal edges and (±L/2, ±(W/2-r)) on vertical.
    a = L / 2 - r_corner
    b = W / 2 - r_corner
    segments = [
        # bottom line: left to right
        {"kind": "line", "start": (-a, -W / 2), "end": (a, -W / 2)},
        # bottom-right arc: (a,-W/2) → (L/2,-b), ccw
        {"kind": "arc", "start": (a, -W / 2), "end": (L / 2, -b),
         "radius": r_corner, "ccw": True},
        # right line: bottom to top
        {"kind": "line", "start": (L / 2, -b), "end": (L / 2, b)},
        # top-right arc
        {"kind": "arc", "start": (L / 2, b), "end": (a, W / 2),
         "radius": r_corner, "ccw": True},
        # top line: right to left
        {"kind": "line", "start": (a, W / 2), "end": (-a, W / 2)},
        # top-left arc
        {"kind": "arc", "start": (-a, W / 2), "end": (-L / 2, b),
         "radius": r_corner, "ccw": True},
        # left line: top to bottom
        {"kind": "line", "start": (-L / 2, b), "end": (-L / 2, -b)},
        # bottom-left arc
        {"kind": "arc", "start": (-L / 2, -b), "end": (-a, -W / 2),
         "radius": r_corner, "ccw": True},
    ]

    box_a = _make_box()
    v0 = _volume(box_a)
    r_comp = ExtrudePocket().apply(box_a, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "composite", "segments": segments},
        "depth_mm": 2.0,
    })
    loss_comp = v0 - _volume(r_comp.body)

    # rounded rectangle area = L*W - (4-π)*r² (square corners removed, quarter-circles added)
    area = L * W - (4 - math.pi) * r_corner * r_corner
    expected = area * 2.0
    assert abs(loss_comp - expected) / expected < 0.05, (
        f"composite rounded-rect pocket loss {loss_comp:.2f}, expected {expected:.2f}"
    )


# ── EllipseSketch ────────────────────────────────────────────────────────


def test_ellipse_sketch_pocket():
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket

    box = _make_box(40, 40, 10)
    v_before = _volume(box)
    r = ExtrudePocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "ellipse", "radius_x_mm": 6.0, "radius_y_mm": 3.0},
        "depth_mm": 2.0,
    })
    v_after = _volume(r.body)
    expected = math.pi * 6.0 * 3.0 * 2.0
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.05


# ── RoundedRectSketch (now honors corner_r) ──────────────────────────────


def test_rounded_rect_sketch_now_honors_corner_r():
    """Previously degenerate (rectangle only) — now should produce rounded corners
    via MakeFillet2d. Face count of result with R>0 > face count with R=0 sketch."""
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket

    sharp = ExtrudePocket().apply(_make_box(), {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "rectangle", "length_mm": 10.0, "width_mm": 6.0},
        "depth_mm": 2.0,
    })
    rounded = ExtrudePocket().apply(_make_box(), {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {
            "kind": "rounded_rect",
            "length_mm": 10.0, "width_mm": 6.0, "corner_r_mm": 1.0,
        },
        "depth_mm": 2.0,
    })
    assert _face_count(rounded.body) > _face_count(sharp.body)


# ── extrude_pocket_blended ───────────────────────────────────────────────


def test_extrude_pocket_blended_floor_fillet():
    """Circular pocket with floor_blend_r=0.3 → extra face(s) for the toroidal blend."""
    from phone_designer.skills.modify_pocket.extrude_pocket_blended import (
        ExtrudePocketBlended,
    )
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket

    sharp = ExtrudePocket().apply(_make_box(), {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "circle", "diameter_mm": 8.0},
        "depth_mm": 3.0,
    })
    fc_sharp = _face_count(sharp.body)

    blended = ExtrudePocketBlended().apply(_make_box(), {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "circle", "diameter_mm": 8.0},
        "depth_mm": 3.0,
        "floor_blend_r_mm": 0.3,
    })
    fc_blended = _face_count(blended.body)
    assert fc_blended > fc_sharp, (
        f"floor fillet should add face(s); sharp={fc_sharp} blended={fc_blended}"
    )


def test_extrude_pocket_blended_opening_fillet():
    """Same but opening fillet — also adds topology."""
    from phone_designer.skills.modify_pocket.extrude_pocket_blended import (
        ExtrudePocketBlended,
    )
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket

    sharp = ExtrudePocket().apply(_make_box(), {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "circle", "diameter_mm": 8.0},
        "depth_mm": 3.0,
    })
    fc_sharp = _face_count(sharp.body)

    blended = ExtrudePocketBlended().apply(_make_box(), {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "circle", "diameter_mm": 8.0},
        "depth_mm": 3.0,
        "opening_blend_r_mm": 0.3,
    })
    fc_blended = _face_count(blended.body)
    assert fc_blended > fc_sharp


def test_extrude_pocket_blended_both_fillets():
    from phone_designer.skills.modify_pocket.extrude_pocket_blended import (
        ExtrudePocketBlended,
    )
    r = ExtrudePocketBlended().apply(_make_box(), {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "rectangle", "length_mm": 8.0, "width_mm": 5.0},
        "depth_mm": 2.5,
        "floor_blend_r_mm": 0.3,
        "opening_blend_r_mm": 0.2,
    })
    assert r.body is not None
    assert _volume(r.body) < _volume(_make_box())


# ── extrude_boss_blended ─────────────────────────────────────────────────


def test_extrude_boss_blended_top_fillet():
    """Circular boss with top fillet — volume up, more faces than sharp boss."""
    from phone_designer.skills.modify_boss.extrude_boss_blended import (
        ExtrudeBossBlended,
    )
    from phone_designer.skills.modify_pocket.extrude_plateau import ExtrudePlateau

    sharp = ExtrudePlateau().apply(_make_box(), {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "circle", "diameter_mm": 6.0},
        "height_mm": 2.0,
    })
    fc_sharp = _face_count(sharp.body)

    blended = ExtrudeBossBlended().apply(_make_box(), {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "circle", "diameter_mm": 6.0},
        "height_mm": 2.0,
        "top_blend_r_mm": 0.3,
    })
    fc_blended = _face_count(blended.body)
    assert _volume(blended.body) > _volume(_make_box())
    assert fc_blended > fc_sharp


def test_extrude_boss_blended_base_fillet():
    from phone_designer.skills.modify_boss.extrude_boss_blended import (
        ExtrudeBossBlended,
    )
    r = ExtrudeBossBlended().apply(_make_box(), {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "rectangle", "length_mm": 6.0, "width_mm": 4.0},
        "height_mm": 2.0,
        "base_blend_r_mm": 0.4,
    })
    assert _volume(r.body) > _volume(_make_box())


# ── loft_pocket_between_sketches ─────────────────────────────────────────


def test_loft_pocket_taper():
    """Upper sketch 8×8, lower sketch 4×4 — tapered pocket. Volume = trapezoid
    prism = (A_top + A_bot + sqrt(A_top*A_bot))/3 * h."""
    from phone_designer.skills.modify_pocket.loft_pocket_between_sketches import (
        LoftPocketBetweenSketches,
    )

    box = _make_box(40, 40, 10)
    v_before = _volume(box)
    depth = 3.0
    A_top = 8.0 * 8.0
    A_bot = 4.0 * 4.0
    expected = (A_top + A_bot + math.sqrt(A_top * A_bot)) / 3.0 * depth

    r = LoftPocketBetweenSketches().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "upper_sketch": {"kind": "rectangle", "length_mm": 8.0, "width_mm": 8.0},
        "lower_sketch": {"kind": "rectangle", "length_mm": 4.0, "width_mm": 4.0},
        "depth_mm": depth,
    })
    actual = v_before - _volume(r.body)
    assert abs(actual - expected) / expected < 0.10, (
        f"loft pocket volume: expected {expected:.2f}, got {actual:.2f}"
    )


# ── validation tests for new sketch types ────────────────────────────────


def test_polygon_rejects_two_vertices():
    from pydantic import ValidationError
    from phone_designer.skills.modify_pocket._sketch import PolygonSketch
    with pytest.raises(ValidationError):
        PolygonSketch(vertices=[(0, 0), (1, 0)])


def test_composite_rejects_open_loop():
    from pydantic import ValidationError
    from phone_designer.skills.modify_pocket._sketch import CompositeSketch
    with pytest.raises(ValidationError):
        CompositeSketch(segments=[
            {"kind": "line", "start": (0, 0), "end": (1, 0)},
            {"kind": "line", "start": (1, 0), "end": (1, 1)},
        ])


def test_slot_rejects_length_less_than_width():
    from pydantic import ValidationError
    from phone_designer.skills.modify_pocket._sketch import SlotSketch
    with pytest.raises(ValidationError):
        SlotSketch(length_mm=2.0, width_mm=5.0)


def test_arc_rejects_chord_too_long():
    from pydantic import ValidationError
    from phone_designer.skills.modify_pocket._sketch import ArcSegment
    with pytest.raises(ValidationError):
        ArcSegment(start=(0, 0), end=(10, 0), radius=1.0)


# ── manifest sanity ──────────────────────────────────────────────────────


def test_manifest_includes_new_skills():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    expected = {
        "extrude_pocket_blended",
        "extrude_boss_blended",
        "loft_pocket_between_sketches",
    }
    assert expected <= names, f"missing: {expected - names}"
