"""assembly skill 단위 테스트.

multi-body compound 워크플로우:
    AddComponent → MoveComponent → InterferenceCheck → MatePlanar
"""
from __future__ import annotations

import pytest

from phone_designer.skills.assembly._compound import list_components
from phone_designer.skills.assembly.add_component import AddComponent
from phone_designer.skills.assembly.interference_check import InterferenceCheck
from phone_designer.skills.assembly.mate_planar import MatePlanar
from phone_designer.skills.assembly.move_component import MoveComponent
from phone_designer.skills.create.box import Box


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
    """Returns an assembly body with two 10mm cube components:
        - 'box_a' at origin (bbox center 0,0,5)
        - 'box_b' translated by (+20, 0, 0)
    No overlap.
    """
    base = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    ac = AddComponent()
    r1 = ac.apply(base, {"name": "box_a"})
    r2 = ac.apply(r1.body, {"name": "box_b", "translation": (20.0, 0.0, 0.0)})
    return r2.body


# ---------------------------------------------------------------------------
# add_component
# ---------------------------------------------------------------------------

def test_add_component_starts_assembly_from_solid():
    base = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = AddComponent().apply(base, {"name": "box_a"})
    assert r.extras["component_names"] == ["box_a"]
    comps = list_components(r.body)
    assert [n for n, _ in comps] == ["box_a"]
    # total volume preserved
    assert abs(_shape_volume(r.body.wrapped) - 1000.0) < 1e-3


def test_add_component_appends_with_translation():
    body = _two_box_assembly()
    comps = list_components(body)
    assert [n for n, _ in comps] == ["box_a", "box_b"]
    # 2 disjoint 1000mm³ boxes
    assert abs(_shape_volume(body.wrapped) - 2000.0) < 1e-3


def test_add_component_rejects_duplicate_name():
    base = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r1 = AddComponent().apply(base, {"name": "box_a"})
    with pytest.raises(Exception):
        AddComponent().apply(r1.body, {"name": "box_a"})


# ---------------------------------------------------------------------------
# move_component
# ---------------------------------------------------------------------------

def test_move_component_translates_named_component():
    body = _two_box_assembly()
    # Move box_b by (+5, 0, 0) — now centered at (25, 0, 5).
    r = MoveComponent().apply(body, {"name": "box_b", "translation": (5.0, 0.0, 0.0)})
    comps = list_components(r.body)
    assert [n for n, _ in comps] == ["box_a", "box_b"]
    # Total volume must be invariant under rigid transform.
    assert abs(_shape_volume(r.body.wrapped) - 2000.0) < 1e-3
    # box_b shifted: check via bbox
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    name_to_shape = dict(comps)
    bb = Bnd_Box()
    BRepBndLib.Add_s(name_to_shape["box_b"], bb)
    xmin, _, _, xmax, _, _ = bb.Get()
    # box_b was at x in [15, 25] after first translate; after another +5 → [20, 30]
    assert 19.9 < xmin < 20.1
    assert 29.9 < xmax < 30.1


def test_move_component_rejects_unknown_name():
    body = _two_box_assembly()
    with pytest.raises(Exception):
        MoveComponent().apply(body, {"name": "nope", "translation": (1, 0, 0)})


def test_move_component_rotation_axis():
    """Rotate box_b by 90° about Z — bbox in XY should swap."""
    body = _two_box_assembly()
    # box_b is currently at x in [15,25], y in [-5,5] (10x10 footprint).
    # After 90° rotation about origin Z-axis, those points map to
    #     (x', y') = (-y, x)  => new x in [-5, 5], new y in [15, 25]
    r = MoveComponent().apply(
        body, {"name": "box_b", "rotation_axis_deg": (0.0, 0.0, 1.0, 90.0)}
    )
    name_to_shape = dict(list_components(r.body))
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(name_to_shape["box_b"], bb)
    xmin, ymin, _, xmax, ymax, _ = bb.Get()
    assert -5.5 < xmin < -4.5 and 4.5 < xmax < 5.5
    assert 14.5 < ymin < 15.5 and 24.5 < ymax < 25.5


# ---------------------------------------------------------------------------
# interference_check
# ---------------------------------------------------------------------------

def test_interference_check_disjoint_assembly_no_overlap():
    body = _two_box_assembly()
    r = InterferenceCheck().apply(body, {})
    assert r.extras["interferences"] == []
    assert set(r.extras["component_names"]) == {"box_a", "box_b"}


def test_interference_check_detects_overlap_after_move():
    body = _two_box_assembly()
    # Pull box_b in by -15 → centered now at (5, 0, 5), overlapping box_a (at 0,0,5)
    body2 = MoveComponent().apply(
        body, {"name": "box_b", "translation": (-15.0, 0.0, 0.0)}
    ).body
    r = InterferenceCheck().apply(body2, {})
    inter = r.extras["interferences"]
    assert len(inter) == 1
    assert {inter[0]["a"], inter[0]["b"]} == {"box_a", "box_b"}
    # Overlap = 10×10×5 = 500 mm³
    assert 499.0 < inter[0]["overlap_volume"] < 501.0


def test_interference_check_respects_pair_filter():
    body = _two_box_assembly()
    # Move box_b into box_a.
    body2 = MoveComponent().apply(
        body, {"name": "box_b", "translation": (-15.0, 0.0, 0.0)}
    ).body
    # filter excludes the only real pair
    r = InterferenceCheck().apply(body2, {"pair_filter": []})
    assert r.extras["interferences"] == []
    # filter explicitly requests it
    r2 = InterferenceCheck().apply(body2, {"pair_filter": [("box_a", "box_b")]})
    assert len(r2.extras["interferences"]) == 1


# ---------------------------------------------------------------------------
# mate_planar
# ---------------------------------------------------------------------------

def test_mate_planar_aligns_faces_with_offset():
    """Apply mate: box_a's +X face against box_b's -X face, offset=0.

    box_a sits at x in [-5, 5] (its +X face at x=5, normal +X).
    box_b sits at x in [15, 25] (its -X face at x=15, normal -X).
    After mate, box_a should be translated so its +X face touches box_b's -X face.
    box_a's bbox should end up at x in [5, 15] (touching box_b).
    """
    body = _two_box_assembly()

    sel_a = {"kind": "faces_by_normal", "direction": (1.0, 0.0, 0.0), "tol_deg": 5.0}
    sel_b = {"kind": "faces_by_normal", "direction": (-1.0, 0.0, 0.0), "tol_deg": 5.0}

    r = MatePlanar().apply(body, {
        "component_a": "box_a",
        "face_selector_a": sel_a,
        "component_b": "box_b",
        "face_selector_b": sel_b,
        "offset_mm": 0.0,
    })
    name_to_shape = dict(list_components(r.body))
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(name_to_shape["box_a"], bb)
    xmin, _, _, xmax, _, _ = bb.Get()
    # box_a's +X face should now lie at x=15 (where box_b's -X face is) at offset=0.
    assert 14.5 < xmax < 15.5
    assert 4.5 < xmin < 5.5
    # box_b untouched.
    bb2 = Bnd_Box()
    BRepBndLib.Add_s(name_to_shape["box_b"], bb2)
    xmin_b, _, _, xmax_b, _, _ = bb2.Get()
    assert 14.5 < xmin_b < 15.5
    assert 24.5 < xmax_b < 25.5


def test_mate_planar_offset_separates_faces():
    """Same as above but offset_mm=3 — box_a's +X face should sit 3mm away from box_b's -X face."""
    body = _two_box_assembly()
    sel_a = {"kind": "faces_by_normal", "direction": (1.0, 0.0, 0.0), "tol_deg": 5.0}
    sel_b = {"kind": "faces_by_normal", "direction": (-1.0, 0.0, 0.0), "tol_deg": 5.0}

    r = MatePlanar().apply(body, {
        "component_a": "box_a",
        "face_selector_a": sel_a,
        "component_b": "box_b",
        "face_selector_b": sel_b,
        "offset_mm": 3.0,
    })
    name_to_shape = dict(list_components(r.body))
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(name_to_shape["box_a"], bb)
    xmin, _, _, xmax, _, _ = bb.Get()
    # +X face at x=15 - 3 = 12. box_a length 10 → x in [2, 12].
    assert 11.5 < xmax < 12.5
    assert 1.5 < xmin < 2.5


def test_mate_planar_rejects_unknown_component():
    body = _two_box_assembly()
    sel = {"kind": "faces_by_normal", "direction": (1.0, 0.0, 0.0), "tol_deg": 5.0}
    with pytest.raises(Exception):
        MatePlanar().apply(body, {
            "component_a": "missing",
            "face_selector_a": sel,
            "component_b": "box_b",
            "face_selector_b": sel,
        })


# ---------------------------------------------------------------------------
# spec metadata sanity
# ---------------------------------------------------------------------------

def test_assembly_specs_register_under_assembly_category():
    for cls in (AddComponent, MoveComponent, InterferenceCheck, MatePlanar):
        assert cls.spec.category == "assembly"
        assert cls.spec.level == "atomic"
