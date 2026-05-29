"""bearing_bore + o_ring_groove_radial_spec + o_ring_groove_axial_spec.

Catalog-driven housing seat (608 bearing) and O-ring grooves (AS568) on a
simple cylindrical / box body. Verifies volume_decreased post-condition and
checks expected swept-volume magnitudes against catalog dimensions.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.cylinder import Cylinder
from phone_designer.skills.modify_pocket.bearing_bore import BearingBore
from phone_designer.skills.modify_pocket.o_ring_groove_axial_spec import (
    ORingGrooveAxialSpec,
)
from phone_designer.skills.modify_pocket.o_ring_groove_radial_spec import (
    ORingGrooveRadialSpec,
)


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


# ───────────────────────────── bearing_bore ─────────────────────────────────


def test_bearing_bore_608_reduces_volume_by_catalog_geometry():
    # Housing block big enough for a 608 seat (outer 22, width 7).
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 40, "width_mm": 40, "height_mm": 20},
    )
    v_before = _volume(box_result.body)

    bore = BearingBore()
    r = bore.apply(
        box_result.body,
        {
            "axis_origin": (0.0, 0.0, 20.0),
            "axis_direction": (0.0, 0.0, -1.0),
            "bearing_spec": "608",
            "with_shoulder": False,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "bearing bore must remove material"

    # 608: outer 22 mm, width 7 mm -> π·11²·7 ≈ 2661 mm³
    expected = math.pi * 11.0 ** 2 * 7.0
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02, (
        f"608 bore volume: expected {expected:.1f}, got {actual:.1f}"
    )


def test_bearing_bore_with_shoulder_removes_more_than_plain_seat():
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 40, "width_mm": 40, "height_mm": 20},
    )

    bore = BearingBore()
    plain = bore.apply(
        box_result.body,
        {
            "axis_origin": (0.0, 0.0, 20.0),
            "axis_direction": (0.0, 0.0, -1.0),
            "bearing_spec": "608",
            "with_shoulder": False,
        },
    )
    shouldered = bore.apply(
        box_result.body,
        {
            "axis_origin": (0.0, 0.0, 20.0),
            "axis_direction": (0.0, 0.0, -1.0),
            "bearing_spec": "608",
            "with_shoulder": True,
            "shoulder_height_mm": 2.0,
        },
    )
    assert _volume(shouldered.body) < _volume(plain.body), (
        "shoulder must remove additional material past the seat"
    )


def test_bearing_bore_rejects_unknown_spec():
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 40, "width_mm": 40, "height_mm": 20},
    )
    bore = BearingBore()
    with pytest.raises(Exception):
        bore.apply(
            box_result.body,
            {
                "axis_origin": (0.0, 0.0, 20.0),
                "axis_direction": (0.0, 0.0, -1.0),
                "bearing_spec": "9999-not-a-bearing",
            },
        )


def test_bearing_bore_spec_metadata():
    assert BearingBore.spec.name == "bearing_bore"
    assert "bearing_seat" in BearingBore.spec.produces_features


# ───────────────────── o_ring_groove_radial_spec ────────────────────────────


def test_o_ring_radial_groove_reduces_volume_on_shaft():
    # Shaft Ø 16 along +Z, height 30, base at z=0. AS568-013 od ≈ 14.38 mm.
    # The groove is sized by the catalog (od/2 radial center). Use a shaft
    # whose OUTER radius ≥ od/2 so the revolved tool intersects the shaft.
    cyl_inst = Cylinder()
    cyl_result = cyl_inst.apply(
        None,
        {"radius_mm": 8.0, "height_mm": 30.0, "base_z_mm": 0.0, "axis": "Z"},
    )
    v_before = _volume(cyl_result.body)

    skill = ORingGrooveRadialSpec()
    r = skill.apply(
        cyl_result.body,
        {
            "axis_origin": (0.0, 0.0, 0.0),
            "axis_direction": (0.0, 0.0, 1.0),
            "o_ring_spec": "AS568-013",
            "axial_position_mm": 15.0,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "radial groove must remove material from shaft"


def test_o_ring_radial_groove_rejects_unknown_spec():
    cyl_inst = Cylinder()
    cyl_result = cyl_inst.apply(
        None,
        {"radius_mm": 8.0, "height_mm": 30.0, "base_z_mm": 0.0, "axis": "Z"},
    )
    skill = ORingGrooveRadialSpec()
    with pytest.raises(Exception):
        skill.apply(
            cyl_result.body,
            {
                "axis_origin": (0.0, 0.0, 0.0),
                "axis_direction": (0.0, 0.0, 1.0),
                "o_ring_spec": "AS568-999",
                "axial_position_mm": 15.0,
            },
        )


def test_o_ring_radial_spec_metadata():
    assert ORingGrooveRadialSpec.spec.name == "o_ring_groove_radial_spec"
    assert "o_ring_groove" in ORingGrooveRadialSpec.spec.produces_features


# ───────────────────── o_ring_groove_axial_spec ─────────────────────────────


def test_o_ring_axial_face_groove_reduces_volume():
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 40, "width_mm": 40, "height_mm": 10},
    )
    v_before = _volume(box_result.body)

    skill = ORingGrooveAxialSpec()
    r = skill.apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "o_ring_spec": "AS568-013",
            "center_xy": (0.0, 0.0),
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "face-seal groove must remove material"


def test_o_ring_axial_rejects_unknown_spec():
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 40, "width_mm": 40, "height_mm": 10},
    )
    skill = ORingGrooveAxialSpec()
    with pytest.raises(Exception):
        skill.apply(
            box_result.body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "o_ring_spec": "AS568-NOPE",
                "center_xy": (0.0, 0.0),
            },
        )


def test_o_ring_axial_spec_metadata():
    assert ORingGrooveAxialSpec.spec.name == "o_ring_groove_axial_spec"
    assert "o_ring_groove" in ORingGrooveAxialSpec.spec.produces_features
