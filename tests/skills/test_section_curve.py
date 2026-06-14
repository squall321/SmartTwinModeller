"""_section_curve — shared section-curve recovery helper (phase-0, 2026-06-13).

recover_section_loop(shape, plane_origin, plane_normal) sections a solid with a
plane, stitches the resulting section edges into ONE ordered closed loop, and
emits an EXECUTABLE sketch dict (polygon / composite / bspline_closed) that the
modify_pocket / loft skills can consume directly.

byte_identical_proof: N/A — this is a NEW standalone read-only helper; it does
not touch any committed plan / baseline path (cross_section.py and _sketch.py
are imported / reused, never modified). The invariant proven here instead is
the helper's ROUND-TRIP: section a solid → recover the sketch → extrude it →
the prism volume matches height · loop-area within 2% (test_*_roundtrip_volume).
"""
from __future__ import annotations

import math

import pytest
from pydantic import TypeAdapter

from phone_designer.skills.inspect._section_curve import recover_section_loop
from phone_designer.skills.modify_pocket._sketch import SketchSpec
from phone_designer.skills.modify_pocket._sketch_to_solid import (
    _build_planar_face,
    _extrude_face_along_z,
)

_SKETCH = TypeAdapter(SketchSpec)


# ──────────────────────────────────────────────────────────────────────────────
# helpers


def _solid_volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _extrude_recovered(result: dict, height_mm: float) -> float:
    """Validate the recovered sketch dict and extrude it; return prism volume.

    Proves the emitted dict is a CONSUMABLE SketchSpec (pydantic-validates) and
    that the modify_pocket wire builders accept it.
    """
    sketch = _SKETCH.validate_python(result["sketch"])
    face = _build_planar_face(sketch)
    prism = _extrude_face_along_z(face, height_mm, direction_sign=1)
    return _solid_volume(prism)


def _box(length, width, height):
    from build123d import Align
    from build123d import Box as B123dBox

    return B123dBox(length, width, height, align=(Align.CENTER, Align.CENTER, Align.MIN))


# ──────────────────────────────────────────────────────────────────────────────
# (a) box → 4-point rectangle loop, area ~600


def test_box_section_is_closed_rectangle():
    """Section a 20×30×10 box at mid-height → closed 4-point rectangle, ~600."""
    body = _box(20.0, 30.0, 10.0)
    r = recover_section_loop(body, (0.0, 0.0, 5.0), (0.0, 0.0, 1.0))

    assert r["closed"] is True
    assert r["n_loops"] == 1
    assert r["loop_kind"] == "polygon"
    assert r["n_points"] == 4, f"expected 4 corner points, got {r['n_points']}"
    assert r["sketch"]["kind"] == "polygon"
    assert math.isclose(r["area_mm2"], 600.0, rel_tol=0.02), r["area_mm2"]


def test_box_section_roundtrip_volume():
    """Round-trip: feed the recovered rectangle to extrude; prism = h·area."""
    body = _box(20.0, 30.0, 10.0)
    r = recover_section_loop(body, (0.0, 0.0, 5.0), (0.0, 0.0, 1.0))
    h = 7.0
    vol = _extrude_recovered(r, h)
    assert math.isclose(vol, h * r["area_mm2"], rel_tol=0.02), (vol, r["area_mm2"])
    # also matches the true footprint volume 20·30·7
    assert math.isclose(vol, 20.0 * 30.0 * h, rel_tol=0.02), vol


# ──────────────────────────────────────────────────────────────────────────────
# (b) cylinder r=5 → closed loop area ~78.5 (π·25), classified as arc/circle


def test_cylinder_section_is_circle_arclike():
    """Section a r=5 cylinder → closed smooth loop, area ≈ π·25, NOT a polygon
    (the section edge is a circle → emitted as a curved bspline_closed sketch).
    """
    from build123d import Cylinder

    body = Cylinder(5.0, 10.0)  # r=5, h=10, centered at origin
    r = recover_section_loop(body, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))

    assert r["closed"] is True
    assert r["n_loops"] == 1
    # arc/circle section → smooth closed curve, not the straight-only polygon.
    assert r["loop_kind"] == "bspline_closed"
    assert r["sketch"]["kind"] == "bspline_closed"
    # the polyline-shoelace area slightly under-estimates π·25; 2% covers it.
    assert math.isclose(r["area_mm2"], math.pi * 25.0, rel_tol=0.02), r["area_mm2"]


def test_cylinder_section_roundtrip_volume():
    """Round-trip the recovered circle: the interpolated prism ≈ π·25·h."""
    from build123d import Cylinder

    body = Cylinder(5.0, 10.0)
    r = recover_section_loop(body, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    h = 3.0
    vol = _extrude_recovered(r, h)
    assert math.isclose(vol, math.pi * 25.0 * h, rel_tol=0.02), vol


# ──────────────────────────────────────────────────────────────────────────────
# (c) L-shaped + rounded-slab bodies → right area within 2%


def test_l_shape_section_area_and_polygon():
    """An L-shaped (non-convex) body sections to a straight polygon whose area
    matches the true footprint within 2%."""
    from build123d import Align
    from build123d import Box as B123dBox
    from build123d import Part
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    b1 = B123dBox(20.0, 10.0, 8.0, align=(Align.MIN, Align.MIN, Align.MIN))
    b2 = B123dBox(10.0, 20.0, 8.0, align=(Align.MIN, Align.MIN, Align.MIN))
    fuse = BRepAlgoAPI_Fuse(b1.wrapped, b2.wrapped)
    fuse.Build()
    body = Part(fuse.Shape())

    # true L footprint area = 20·10 + 10·20 − 10·10 (overlap) = 300
    r = recover_section_loop(body, (0.0, 0.0, 4.0), (0.0, 0.0, 1.0))
    assert r["closed"] is True
    assert r["loop_kind"] == "polygon"
    assert r["n_points"] == 6, f"L outline has 6 corners, got {r['n_points']}"
    assert math.isclose(r["area_mm2"], 300.0, rel_tol=0.02), r["area_mm2"]

    # round-trip volume
    h = 5.0
    vol = _extrude_recovered(r, h)
    assert math.isclose(vol, 300.0 * h, rel_tol=0.02), vol


def test_rounded_slab_section_area_and_composite():
    """A rounded slab (filleted vertical edges) sections to a mixed line+arc
    loop → composite sketch, area within 2% of the true rounded-rect area."""
    from phone_designer.skills.create.rounded_slab import RoundedSlab

    body = RoundedSlab().apply(None, {
        "length_mm": 40.0, "width_mm": 30.0, "height_mm": 10.0, "corner_r_mm": 5.0,
    }).body

    r = recover_section_loop(body, (0.0, 0.0, 5.0), (0.0, 0.0, 1.0))
    assert r["closed"] is True
    assert r["loop_kind"] == "composite"
    assert r["sketch"]["kind"] == "composite"

    # rounded-rect footprint area = L·W − (4·r² − π·r²) for 4 quarter-circle
    # corners replacing 4 square corners.
    true_area = 40.0 * 30.0 - (4.0 * 25.0 - math.pi * 25.0)
    assert math.isclose(r["area_mm2"], true_area, rel_tol=0.02), (r["area_mm2"], true_area)

    # round-trip volume within 2%
    h = 5.0
    vol = _extrude_recovered(r, h)
    assert math.isclose(vol, true_area * h, rel_tol=0.02), (vol, true_area * h)


# ──────────────────────────────────────────────────────────────────────────────
# (d) round-trip invariant is the headline contract — assert it on every kind


@pytest.mark.parametrize(
    "build,origin,normal,height",
    [
        # box → polygon
        ("box", (0.0, 0.0, 5.0), (0.0, 0.0, 1.0), 6.0),
        # box X-section (non-Z plane) → polygon, exercises the (u,v) frame
        ("box_x", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 4.0),
        # cylinder → bspline_closed
        ("cyl", (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 3.0),
    ],
)
def test_roundtrip_volume_matches_height_times_area(build, origin, normal, height):
    """prism volume == height · recovered-area within 2% for every loop kind."""
    if build in ("box", "box_x"):
        body = _box(20.0, 30.0, 10.0)
    else:
        from build123d import Cylinder

        body = Cylinder(5.0, 10.0)

    r = recover_section_loop(body, origin, normal)
    assert r["sketch"] is not None
    assert r["closed"] is True
    vol = _extrude_recovered(r, height)
    assert math.isclose(vol, height * r["area_mm2"], rel_tol=0.02), (
        build, vol, height * r["area_mm2"]
    )


# ──────────────────────────────────────────────────────────────────────────────
# multi-loop + guards (largest-by-area selection, face-count guard, empty)


def test_multi_loop_returns_largest_by_area():
    """A box with a through hole sections into two loops (outer rect + inner
    circle); the helper returns the LARGEST by area and notes n_loops."""
    from build123d import Cylinder, Part
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    big = _box(40.0, 40.0, 10.0)
    hole = Cylinder(5.0, 30.0)  # through hole on the Z axis
    cut = BRepAlgoAPI_Cut(big.wrapped, hole.wrapped)
    cut.Build()
    holed = Part(cut.Shape())

    r = recover_section_loop(holed, (0.0, 0.0, 5.0), (0.0, 0.0, 1.0))
    assert r["n_loops"] == 2, r["n_loops"]
    # largest loop is the 40×40 outer rectangle, area 1600 (NOT the π·25 hole).
    assert math.isclose(r["area_mm2"], 1600.0, rel_tol=0.02), r["area_mm2"]


def test_face_count_guard_skips_without_hanging():
    """max_face_count below the body's face count → skipped, never runs Section
    (a 4000-face KR600 section must not hang)."""
    body = _box(20.0, 30.0, 10.0)  # 6 faces
    r = recover_section_loop(body, (0.0, 0.0, 5.0), (0.0, 0.0, 1.0), max_face_count=2)
    assert r["skipped"] is True
    assert r["reason"] == "too_big"
    assert r["sketch"] is None
    assert r["face_count"] == 6


def test_plane_missing_body_returns_empty():
    """A plane that doesn't intersect the body returns an empty result."""
    body = _box(20.0, 30.0, 10.0)
    r = recover_section_loop(body, (0.0, 0.0, 500.0), (0.0, 0.0, 1.0))
    assert r.get("empty") is True
    assert r["sketch"] is None
    assert r["area_mm2"] == 0.0


def test_zero_normal_rejected():
    """A degenerate (zero) plane normal raises — gp_Dir requires non-zero."""
    body = _box(20.0, 30.0, 10.0)
    with pytest.raises(ValueError):
        recover_section_loop(body, (0.0, 0.0, 5.0), (0.0, 0.0, 0.0))
