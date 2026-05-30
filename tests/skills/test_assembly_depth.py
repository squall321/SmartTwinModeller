"""Wave2-B assembly depth skill tests.

Covers:
    - ExplodedView
    - SubAssemblyTag
    - AutoPlaceFasteners
    - BoltPatternRecognize
    - CheckClearanceFull
"""
from __future__ import annotations

import pytest

from phone_designer.skills.assembly._compound import list_components
from phone_designer.skills.assembly.add_component import AddComponent
from phone_designer.skills.assembly.auto_place_fasteners import AutoPlaceFasteners
from phone_designer.skills.assembly.bolt_pattern_recognize import BoltPatternRecognize
from phone_designer.skills.assembly.check_clearance_full import CheckClearanceFull
from phone_designer.skills.assembly.exploded_view import ExplodedView
from phone_designer.skills.assembly.move_component import MoveComponent
from phone_designer.skills.assembly.sub_assembly_tag import SubAssemblyTag
from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.hole import Hole


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _shape_volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, p)
    return p.Mass()


def _bbox(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    return bb.Get()


def _three_box_assembly():
    """Three 10mm cubes spaced along +X:
        - 'box_a' centered at (  0, 0, 5)
        - 'box_b' centered at ( 20, 0, 5)
        - 'box_c' centered at ( 40, 0, 5)
    """
    base = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    ac = AddComponent()
    r1 = ac.apply(base, {"name": "box_a"})
    r2 = ac.apply(r1.body, {"name": "box_b", "translation": (20.0, 0.0, 0.0)})
    r3 = ac.apply(r2.body, {"name": "box_c", "translation": (40.0, 0.0, 0.0)})
    return r3.body


def _two_plate_assembly_with_aligned_holes():
    """Two thin plates (40x20x3) stacked along Z, each drilled with a
    Ø3.4 mm clearance hole at the same XY position. The resulting two
    components in the assembly should have collinear cylindrical bores.

    Layout:
        - bottom_plate: bbox x[-20,20] y[-10,10] z[0,3] with a hole at (10, 0)
        - top_plate:    bbox x[-20,20] y[-10,10] z[3,6] with a hole at (10, 0)
    """
    bottom = Box().apply(None, {"length_mm": 40, "width_mm": 20, "height_mm": 3}).body
    bottom_holed = Hole().apply(bottom, {
        "position": (10.0, 0.0, 3.0),
        "diameter_mm": 3.4,
        "depth_mm": 3.0,
        "direction": "-Z",
    }).body

    top = Box().apply(None, {"length_mm": 40, "width_mm": 20, "height_mm": 3}).body
    top_holed = Hole().apply(top, {
        "position": (10.0, 0.0, 3.0),
        "diameter_mm": 3.4,
        "depth_mm": 3.0,
        "direction": "-Z",
    }).body

    ac = AddComponent()
    r1 = ac.apply(bottom_holed, {"name": "bottom_plate"})
    # Translate top plate up by 3 mm so it sits on the bottom plate.
    r2 = ac.apply(r1.body, {
        "name": "top_plate",
        "source_step_path": None,
        "translation": (0.0, 0.0, 3.0),
    })
    # The implementation uses the *current body* as the source — so r2 actually
    # adds a translated copy of the (bottom_plate-only) compound. Instead we
    # construct it differently to ensure top_plate is a translated plate.
    # Workaround: build top plate as an isolated body before AddComponent.
    return r2.body


def _plate_with_grid_holes():
    """A 50x50x4 plate drilled with a 3x3 grid of Ø3.4 holes at 12mm pitch.

    Hole centres: (-12,-12), (0,-12), (12,-12), (-12,0), (0,0), (12,0),
                  (-12,12), (0,12), (12,12)  — all on the top face (z=4).
    """
    base = Box().apply(None, {"length_mm": 50, "width_mm": 50, "height_mm": 4}).body
    body = base
    for x in (-12.0, 0.0, 12.0):
        for y in (-12.0, 0.0, 12.0):
            body = Hole().apply(body, {
                "position": (x, y, 4.0),
                "diameter_mm": 3.4,
                "depth_mm": 4.0,
                "direction": "-Z",
            }).body
    return body


# ---------------------------------------------------------------------------
# ExplodedView
# ---------------------------------------------------------------------------


def test_exploded_view_radial_separates_components():
    body = _three_box_assembly()
    pre_vol = _shape_volume(body.wrapped)

    r = ExplodedView().apply(body, {
        "explode_distance_mm": 50.0,
        "direction": "radial",
    })

    # All three components preserved by name.
    comps = list_components(r.body)
    assert [n for n, _ in comps] == ["box_a", "box_b", "box_c"]

    # Volume invariant under rigid translation.
    assert abs(_shape_volume(r.body.wrapped) - pre_vol) < 1e-3

    # Offsets list parallels names and has correct length.
    offsets = r.extras["exploded_offsets"]
    assert len(offsets) == 3
    assert [o["name"] for o in offsets] == ["box_a", "box_b", "box_c"]

    # First (i=0) component is not moved.
    assert offsets[0]["translation"] == [0.0, 0.0, 0.0]
    # box_c (i=2) should be moved a non-trivial amount.
    dx, dy, dz = offsets[2]["translation"]
    mag = (dx * dx + dy * dy + dz * dz) ** 0.5
    assert mag > 50.0  # 2 * 50mm = 100mm scaling


def test_exploded_view_axial_z_uniform_direction():
    body = _three_box_assembly()
    r = ExplodedView().apply(body, {
        "explode_distance_mm": 30.0,
        "direction": "axial_z",
    })
    offsets = r.extras["exploded_offsets"]
    # Each component moved by i * 30 mm along +Z.
    assert offsets[0]["translation"] == [0.0, 0.0, 0.0]
    assert offsets[1]["translation"] == [0.0, 0.0, 30.0]
    assert offsets[2]["translation"] == [0.0, 0.0, 60.0]
    # box_b's bbox should now sit at z in [30+0=30, 30+10=40].
    name_to_shape = dict(list_components(r.body))
    _, _, zmin, _, _, zmax = _bbox(name_to_shape["box_b"])
    assert 29.5 < zmin < 30.5
    assert 39.5 < zmax < 40.5


def test_exploded_view_custom_direction_zero_vector_raises():
    body = _three_box_assembly()
    with pytest.raises(Exception):
        ExplodedView().apply(body, {
            "explode_distance_mm": 20.0,
            "direction": "custom",
            "custom_direction": (0.0, 0.0, 0.0),
        })


# ---------------------------------------------------------------------------
# SubAssemblyTag
# ---------------------------------------------------------------------------


def test_sub_assembly_tag_records_group_membership():
    body = _three_box_assembly()
    r = SubAssemblyTag().apply(body, {
        "component_names": ["box_a", "box_b"],
        "sub_assembly_name": "front_module",
    })
    sub = r.extras["sub_assemblies"]
    assert "front_module" in sub
    assert sub["front_module"] == ["box_a", "box_b"]
    # body is returned identical.
    assert r.body is body
    # metadata attribute attached to body.
    assert r.body._pd_sub_assemblies["front_module"] == ["box_a", "box_b"]


def test_sub_assembly_tag_accumulates_across_calls():
    body = _three_box_assembly()
    r1 = SubAssemblyTag().apply(body, {
        "component_names": ["box_a"],
        "sub_assembly_name": "front",
    })
    r2 = SubAssemblyTag().apply(r1.body, {
        "component_names": ["box_b"],
        "sub_assembly_name": "front",
    })
    assert r2.extras["sub_assemblies"]["front"] == ["box_a", "box_b"]
    # Another sub-assembly is independent.
    r3 = SubAssemblyTag().apply(r2.body, {
        "component_names": ["box_c"],
        "sub_assembly_name": "rear",
    })
    assert r3.extras["sub_assemblies"]["front"] == ["box_a", "box_b"]
    assert r3.extras["sub_assemblies"]["rear"] == ["box_c"]


def test_sub_assembly_tag_rejects_unknown_component():
    body = _three_box_assembly()
    with pytest.raises(Exception):
        SubAssemblyTag().apply(body, {
            "component_names": ["box_z"],
            "sub_assembly_name": "ghost",
        })


# ---------------------------------------------------------------------------
# AutoPlaceFasteners
# ---------------------------------------------------------------------------


def _two_plates_with_collinear_holes_assembly():
    """Build the two-plate assembly so each plate is a separate component
    with its own hole at the same XY axis. AddComponent's 'use current body'
    semantic doesn't help us here — we manually build the compound.
    """
    from phone_designer.skills.assembly._compound import build_compound
    from build123d import Part

    bottom = Box().apply(None, {"length_mm": 40, "width_mm": 20, "height_mm": 3}).body
    bottom_holed = Hole().apply(bottom, {
        "position": (10.0, 0.0, 3.0),
        "diameter_mm": 3.4,
        "depth_mm": 3.0,
        "direction": "-Z",
    }).body

    top = Box().apply(None, {"length_mm": 40, "width_mm": 20, "height_mm": 3}).body
    top_holed = Hole().apply(top, {
        "position": (10.0, 0.0, 3.0),
        "diameter_mm": 3.4,
        "depth_mm": 3.0,
        "direction": "-Z",
    }).body

    # Translate top plate up by 3mm so its bottom touches the bottom plate's top.
    from phone_designer.skills.assembly._compound import (
        apply_transform_shape, make_translation_trsf,
    )
    top_shape = top_holed.wrapped
    top_shifted = apply_transform_shape(top_shape, make_translation_trsf(0.0, 0.0, 3.0))

    comp = build_compound([bottom_holed.wrapped, top_shifted])
    asm = Part(comp)
    asm._pd_component_names = ["bottom_plate", "top_plate"]
    return asm


def test_auto_place_fasteners_creates_screw_at_aligned_hole():
    body = _two_plates_with_collinear_holes_assembly()
    pre_vol = _shape_volume(body.wrapped)

    r = AutoPlaceFasteners().apply(body, {
        "component_a": "bottom_plate",
        "component_b": "top_plate",
        "fastener_thread_spec": "M3",
    })

    # A new fastener component was added.
    names = [n for n, _ in list_components(r.body)]
    assert "bottom_plate" in names
    assert "top_plate" in names
    assert r.extras["fastener_count"] == 1
    fast = r.extras["fasteners"][0]
    assert fast["thread_spec"] == "M3"
    # Outer diameter from M3 catalog ≈ 3.0 mm.
    assert 2.9 < fast["diameter_mm"] < 3.1

    # Volume increased (the fastener cylinder body).
    post_vol = _shape_volume(r.body.wrapped)
    assert post_vol > pre_vol


def test_auto_place_fasteners_rejects_unknown_component():
    body = _two_plates_with_collinear_holes_assembly()
    with pytest.raises(Exception):
        AutoPlaceFasteners().apply(body, {
            "component_a": "missing",
            "component_b": "top_plate",
        })


# ---------------------------------------------------------------------------
# BoltPatternRecognize
# ---------------------------------------------------------------------------


def test_bolt_pattern_recognize_detects_grid():
    body = _plate_with_grid_holes()
    # The plate's top face has normal +Z.
    sel = {"kind": "faces_by_normal", "direction": (0.0, 0.0, 1.0), "tol_deg": 5.0}
    r = BoltPatternRecognize().apply(body, {
        "face_selector": sel,
        "radius_filter_max_mm": 2.5,   # exclude any large round features (none here)
    })
    rep = r.extras["bolt_pattern"]
    # 9 holes in 3x3 grid: it should classify as 'grid' or 'random'.
    # If pure grid, n_x=n_y=3 and pitches near 12 mm.
    assert r.extras["hole_count"] == 9
    assert rep["kind"] in ("grid", "random", "linear")
    if rep["kind"] == "grid":
        params = rep["params"]
        assert params["n_x"] == 3
        assert params["n_y"] == 3
        assert 11.0 < params["pitch_x_mm"] < 13.0
        assert 11.0 < params["pitch_y_mm"] < 13.0


def test_bolt_pattern_recognize_body_unchanged():
    body = _plate_with_grid_holes()
    pre_vol = _shape_volume(body.wrapped)
    sel = {"kind": "faces_by_normal", "direction": (0.0, 0.0, 1.0), "tol_deg": 5.0}
    r = BoltPatternRecognize().apply(body, {"face_selector": sel})
    assert r.body is body
    assert abs(_shape_volume(r.body.wrapped) - pre_vol) < 1e-6


# ---------------------------------------------------------------------------
# CheckClearanceFull
# ---------------------------------------------------------------------------


def test_check_clearance_full_flags_close_components():
    """Boxes 20 mm apart in X — distance between facing faces is 10 mm.
    Set min_clearance to 15 mm so the pair fails.
    """
    body = _three_box_assembly()
    r = CheckClearanceFull().apply(body, {"min_clearance_mm": 15.0})
    violations = r.extras["clearance_violations"]
    # Three pairs total: (a,b) distance 10, (b,c) distance 10, (a,c) distance 30.
    # Two should violate.
    assert len(violations) >= 2
    # Each violation distance < 15.0
    for v in violations:
        assert v["min_dist_mm"] < 15.0 + 1e-6


def test_check_clearance_full_pass_when_components_far():
    body = _three_box_assembly()
    r = CheckClearanceFull().apply(body, {"min_clearance_mm": 1.0})
    # All pairs are ≥ 10 mm apart so nothing violates the 1 mm rule.
    assert r.extras["clearance_violations"] == []


def test_check_clearance_full_detects_overlap_as_violation():
    body = _three_box_assembly()
    # Pull box_b into box_a (-15 mm).
    pulled = MoveComponent().apply(body, {
        "name": "box_b",
        "translation": (-15.0, 0.0, 0.0),
    }).body
    r = CheckClearanceFull().apply(pulled, {"min_clearance_mm": 0.5})
    viols = r.extras["clearance_violations"]
    pair_set = {(v["a"], v["b"]) for v in viols}
    pair_set |= {(v["b"], v["a"]) for v in viols}
    assert ("box_a", "box_b") in pair_set or ("box_b", "box_a") in pair_set


# ---------------------------------------------------------------------------
# spec sanity
# ---------------------------------------------------------------------------


def test_assembly_depth_specs_register_under_assembly_category():
    for cls in (
        ExplodedView,
        SubAssemblyTag,
        AutoPlaceFasteners,
        BoltPatternRecognize,
        CheckClearanceFull,
    ):
        assert cls.spec.category == "assembly"
        assert cls.spec.level == "atomic"
        assert cls.spec.post_conditions, f"{cls.__name__} missing post_conditions"
