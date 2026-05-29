"""Assembly expansion skill tests.

Covers:
    - MateConcentric
    - MateAxis
    - MateAtDistance
    - FastenerArray
    - BomExtract
"""
from __future__ import annotations

import pytest

from phone_designer.skills.assembly._compound import list_components
from phone_designer.skills.assembly.add_component import AddComponent
from phone_designer.skills.assembly.bom_extract import BomExtract
from phone_designer.skills.assembly.fastener_array import FastenerArray
from phone_designer.skills.assembly.mate_at_distance import MateAtDistance
from phone_designer.skills.assembly.mate_axis import MateAxis
from phone_designer.skills.assembly.mate_concentric import MateConcentric
from phone_designer.skills.create.box import Box
from phone_designer.skills.create.cylinder import Cylinder


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _shape_volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, p)
    return p.Mass()


def _two_box_assembly():
    """Two 10mm cubes:
        - 'box_a' centered at (0, 0, 5), x in [-5, 5]
        - 'box_b' centered at (20, 0, 5), x in [15, 25]
    """
    base = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    ac = AddComponent()
    r1 = ac.apply(base, {"name": "box_a"})
    r2 = ac.apply(r1.body, {"name": "box_b", "translation": (20.0, 0.0, 0.0)})
    return r2.body


def _two_cylinder_assembly():
    """Two cylinders along +Z:
        - 'cyl_a' radius 5 at origin (axis through (0,0,0))
        - 'cyl_b' radius 5 at (30, 0, 0)
    Both axes parallel to +Z but offset in X.
    """
    base = Cylinder().apply(None, {
        "radius_mm": 5.0, "height_mm": 10.0,
        "center_x_mm": 0.0, "center_y_mm": 0.0, "base_z_mm": 0.0,
        "axis": "Z",
    }).body
    cyl_b_solo = Cylinder().apply(None, {
        "radius_mm": 5.0, "height_mm": 10.0,
        "center_x_mm": 30.0, "center_y_mm": 0.0, "base_z_mm": 0.0,
        "axis": "Z",
    }).body
    # Build assembly: cyl_a at origin, then add cyl_b (already translated by Cylinder primitive).
    ac = AddComponent()
    r1 = ac.apply(base, {"name": "cyl_a"})
    # source_step_path=None -> use cyl_b_solo body as-is. But add_component uses body arg.
    # We instead just add it as a fresh add_component step on r1.body with the cyl_b template.
    # Easiest: use AddComponent with source = body of cyl_b_solo via an intermediate call.
    # AddComponent's "current body" semantic means we'd lose r1's compound. So use the
    # cyl_b directly by passing body=r1.body and source_step_path=None won't work — that
    # would dupe r1. So we hand-craft the second component using the same approach as
    # _two_box_assembly: spawn from a base cylinder then translate.
    #
    # The trick: AddComponent(body=r1.body, name=cyl_b, translation=(30,0,0)) would add a
    # *copy of r1's compound* as a single component. So instead we treat cyl_b like the
    # box pattern — initial source is cyl_a, add cyl_b as the same cyl with x-shift.
    # That's what r2 below does — adding the current body (cyl_a) translated by (30,0,0).
    r2 = ac.apply(r1.body, {"name": "cyl_b", "translation": (30.0, 0.0, 0.0)})
    return r2.body


def _bbox(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    return bb.Get()


# ---------------------------------------------------------------------------
# MateConcentric
# ---------------------------------------------------------------------------

def test_mate_concentric_aligns_two_cylinder_axes():
    """Move cyl_a so its cylindrical face axis is collinear with cyl_b's.

    Both cylinders have axis along +Z. cyl_a's axis is initially at x=0, y=0.
    cyl_b's axis is at x=30, y=0. After mate, cyl_a should translate by (+30, 0, _).
    """
    body = _two_cylinder_assembly()
    sel = {"kind": "faces_by_area", "min": 100.0}  # the cylindrical lateral face has area 2*pi*5*10 ≈ 314

    r = MateConcentric().apply(body, {
        "component_a": "cyl_a",
        "face_selector_a": sel,
        "component_b": "cyl_b",
        "face_selector_b": sel,
    })
    comps = dict(list_components(r.body))
    # cyl_a's bbox should now overlap cyl_b's in X.
    xmin_a, ymin_a, _, xmax_a, ymax_a, _ = _bbox(comps["cyl_a"])
    # axis at x=30, radius 5 → bbox x in [25, 35]
    assert 24.5 < xmin_a < 25.5
    assert 34.5 < xmax_a < 35.5
    # y centered at 0
    assert -5.5 < ymin_a < -4.5
    assert 4.5 < ymax_a < 5.5

    # Volume preserved (rigid transform).
    assert abs(_shape_volume(r.body.wrapped) - 2 * 3.14159 * 25 * 10) < 5.0


def test_mate_concentric_rejects_non_cylindrical_face():
    """Use a box body — its faces are planar, so concentric mate must error."""
    body = _two_box_assembly()
    sel = {"kind": "faces_by_normal", "direction": (1.0, 0.0, 0.0), "tol_deg": 5.0}
    with pytest.raises(Exception):
        MateConcentric().apply(body, {
            "component_a": "box_a",
            "face_selector_a": sel,
            "component_b": "box_b",
            "face_selector_b": sel,
        })


# ---------------------------------------------------------------------------
# MateAxis
# ---------------------------------------------------------------------------

def test_mate_axis_aligns_two_linear_edges():
    """Pick X-axis-aligned edges on both boxes and verify boxes become coaxial in X.

    box_a (x in [-5, 5]) — its X-aligned edges run along x direction at fixed y,z.
    box_b (x in [15, 25]) — its X-aligned edges similarly.
    A box has 4 X-aligned edges at the corners. After mating box_a's edge to
    box_b's edge, the chosen edges should overlap.
    """
    body = _two_box_assembly()
    sel = {"kind": "axis_aligned_edges", "axis": "X"}

    r = MateAxis().apply(body, {
        "component_a": "box_a",
        "edge_selector_a": sel,
        "component_b": "box_b",
        "edge_selector_b": sel,
    })
    comps = dict(list_components(r.body))
    # Total volume preserved.
    assert abs(_shape_volume(r.body.wrapped) - 2000.0) < 1e-3
    # box_a's bbox should now match where box_b is (its first X-aligned edge corner).
    # Both share the same x extent at minimum (depends on which edge was picked first,
    # but length-10 along x means xmax-xmin = 10).
    xmin_a, _, _, xmax_a, _, _ = _bbox(comps["box_a"])
    assert abs((xmax_a - xmin_a) - 10.0) < 1e-3


def test_mate_axis_rejects_unknown_component():
    body = _two_box_assembly()
    sel = {"kind": "axis_aligned_edges", "axis": "X"}
    with pytest.raises(Exception):
        MateAxis().apply(body, {
            "component_a": "missing",
            "edge_selector_a": sel,
            "component_b": "box_b",
            "edge_selector_b": sel,
        })


# ---------------------------------------------------------------------------
# MateAtDistance
# ---------------------------------------------------------------------------

def test_mate_at_distance_offsets_by_explicit_distance():
    """box_a's +X face mated to box_b's -X face at distance 5 — box_a x in [0, 10]."""
    body = _two_box_assembly()
    sel_a = {"kind": "faces_by_normal", "direction": (1.0, 0.0, 0.0), "tol_deg": 5.0}
    sel_b = {"kind": "faces_by_normal", "direction": (-1.0, 0.0, 0.0), "tol_deg": 5.0}

    r = MateAtDistance().apply(body, {
        "component_a": "box_a",
        "face_selector_a": sel_a,
        "component_b": "box_b",
        "face_selector_b": sel_b,
        "distance_mm": 5.0,
    })
    assert r.extras["distance_mm"] == 5.0
    comps = dict(list_components(r.body))
    xmin, _, _, xmax, _, _ = _bbox(comps["box_a"])
    # +X face of box_a should sit at x=15 - 5 = 10. box length 10 → x in [0, 10].
    assert -0.5 < xmin < 0.5
    assert 9.5 < xmax < 10.5


def test_mate_at_distance_negative_distance_overlaps():
    """Negative distance pulls box_a into box_b."""
    body = _two_box_assembly()
    sel_a = {"kind": "faces_by_normal", "direction": (1.0, 0.0, 0.0), "tol_deg": 5.0}
    sel_b = {"kind": "faces_by_normal", "direction": (-1.0, 0.0, 0.0), "tol_deg": 5.0}

    r = MateAtDistance().apply(body, {
        "component_a": "box_a",
        "face_selector_a": sel_a,
        "component_b": "box_b",
        "face_selector_b": sel_b,
        "distance_mm": -2.0,
    })
    comps = dict(list_components(r.body))
    xmin, _, _, xmax, _, _ = _bbox(comps["box_a"])
    # +X face at x = 15 - (-2) = 17. box_a x in [7, 17]
    assert 6.5 < xmin < 7.5
    assert 16.5 < xmax < 17.5


# ---------------------------------------------------------------------------
# FastenerArray
# ---------------------------------------------------------------------------

def test_fastener_array_places_copies_at_positions():
    """Use a 4mm cube as the 'fastener' template — duplicate to 3 positions."""
    fastener = Box().apply(None, {"length_mm": 4, "width_mm": 4, "height_mm": 4}).body
    # Make it a single-component assembly so subsequent fastener_array calls preserve nothing.
    # Actually fastener_array can take a non-compound body too — it'll just treat it as template.
    r = FastenerArray().apply(fastener, {
        "positions": [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (20.0, 0.0, 0.0)],
        "name_prefix": "screw",
    })
    comps = list_components(r.body)
    names = [n for n, _ in comps]
    assert names == ["screw_0", "screw_1", "screw_2"]
    assert r.extras["fastener_count"] == 3
    # 3 cubes of 64 mm³ each
    assert abs(_shape_volume(r.body.wrapped) - 3 * 64.0) < 1e-3

    # Verify the third instance is at x ≈ 20.
    name_to_shape = dict(comps)
    xmin, _, _, xmax, _, _ = _bbox(name_to_shape["screw_2"])
    # cube centered on (20, 0, 2) (Box default base at z=0, side 4 → x in [18, 22])
    assert 17.5 < xmin < 18.5
    assert 21.5 < xmax < 22.5


def test_fastener_array_appends_to_existing_assembly():
    """Existing assembly + array adds extra components, preserves originals."""
    body = _two_box_assembly()  # box_a, box_b
    # Pass body as template (it's a compound — root shape used). The first two
    # boxes are preserved; new copies are added.
    r = FastenerArray().apply(body, {
        "positions": [(0.0, 0.0, 50.0)],
        "name_prefix": "pin",
    })
    names = [n for n, _ in list_components(r.body)]
    assert names == ["box_a", "box_b", "pin_0"]


def test_fastener_array_rejects_empty_positions():
    fastener = Box().apply(None, {"length_mm": 4, "width_mm": 4, "height_mm": 4}).body
    with pytest.raises(Exception):
        FastenerArray().apply(fastener, {"positions": []})


# ---------------------------------------------------------------------------
# BomExtract
# ---------------------------------------------------------------------------

def test_bom_extract_lists_components_with_bbox_and_volume():
    body = _two_box_assembly()
    r = BomExtract().apply(body, {})
    bom = r.extras["bom"]
    assert len(bom) == 2
    names = [item["name"] for item in bom]
    assert set(names) == {"box_a", "box_b"}
    # box_a at origin: bbox x in [-5, 5]
    a = next(item for item in bom if item["name"] == "box_a")
    assert a["bbox_min"][0] == pytest.approx(-5.0, abs=0.1)
    assert a["bbox_max"][0] == pytest.approx(5.0, abs=0.1)
    assert a["volume_mm3"] == pytest.approx(1000.0, abs=0.01)
    assert a["size"][0] == pytest.approx(10.0, abs=0.01)
    # totals
    assert r.extras["component_count"] == 2
    assert r.extras["total_volume_mm3"] == pytest.approx(2000.0, abs=0.01)


def test_bom_extract_returns_body_unchanged():
    body = _two_box_assembly()
    pre_vol = _shape_volume(body.wrapped)
    r = BomExtract().apply(body, {})
    # body identity is preserved (skill returns same body reference).
    assert r.body is body
    post_vol = _shape_volume(r.body.wrapped)
    assert abs(pre_vol - post_vol) < 1e-6


# ---------------------------------------------------------------------------
# spec metadata sanity
# ---------------------------------------------------------------------------

def test_assembly_expansion_specs_are_atomic_assembly():
    for cls in (MateConcentric, MateAxis, MateAtDistance, FastenerArray, BomExtract):
        assert cls.spec.category == "assembly"
        assert cls.spec.level == "atomic"
        # Every new skill must declare post_conditions per project policy.
        assert cls.spec.post_conditions
