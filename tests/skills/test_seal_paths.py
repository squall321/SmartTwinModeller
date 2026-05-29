"""o_ring_groove_path + foam_seal_groove_racetrack.

Closed-loop face-seal grooves that follow an arbitrary 2D centerline path
(rectangle / racetrack / etc.) on a planar face. Verifies the
``volume_decreased`` post-condition and rough swept-volume magnitudes
against the ISO 3601-2 sizing formulas embedded in the skills.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.foam_seal_groove_racetrack import (
    FoamSealGrooveRacetrack,
)
from phone_designer.skills.modify_pocket.o_ring_groove_path import (
    ORingGroovePath,
)


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


# ────────────────────────── o_ring_groove_path ──────────────────────────────


def test_o_ring_groove_path_rectangle_reduces_volume():
    # 60×40×10 host slab. Path = rounded square loop in face-local XY,
    # 24×24 mm. cs = 2.0 mm → width≈2.72, depth≈1.292 mm.
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 60.0, "width_mm": 40.0, "height_mm": 10.0},
    )
    v_before = _volume(box_result.body)

    path = [
        (-12.0, -12.0),
        ( 12.0, -12.0),
        ( 12.0,  12.0),
        (-12.0,  12.0),
    ]
    skill = ORingGroovePath()
    r = skill.apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "path_points": path,
            "cross_section_diameter_mm": 2.0,
            "squeeze_ratio": 0.15,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "o-ring path groove must remove material"

    # Expected swept volume ≈ centerline perimeter * width * depth.
    cs = 2.0
    width = cs * 1.36
    depth = cs * (1.0 - 0.15) * 0.76
    perimeter = 4 * 24.0
    expected = perimeter * width * depth
    actual = v_before - v_after
    # Generous tolerance — corner offsets (GeomAbs_Arc) round the rectangle
    # corners outward so the actual material removed is slightly different
    # from the perfect-rectangle perimeter approximation.
    assert 0.4 * expected < actual < 1.8 * expected, (
        f"o-ring path swept volume out of range: "
        f"expected≈{expected:.1f}, got {actual:.1f}"
    )


def test_o_ring_groove_path_rejects_too_few_points():
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 40.0, "width_mm": 40.0, "height_mm": 10.0},
    )
    skill = ORingGroovePath()
    with pytest.raises(Exception):
        skill.apply(
            box_result.body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "path_points": [(-5.0, -5.0), (5.0, 5.0)],  # < 3
                "cross_section_diameter_mm": 1.5,
            },
        )


def test_o_ring_groove_path_rejects_bad_squeeze():
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 40.0, "width_mm": 40.0, "height_mm": 10.0},
    )
    skill = ORingGroovePath()
    with pytest.raises(Exception):
        skill.apply(
            box_result.body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "path_points": [
                    (-5.0, -5.0), (5.0, -5.0), (5.0, 5.0), (-5.0, 5.0),
                ],
                "cross_section_diameter_mm": 1.5,
                "squeeze_ratio": 0.6,  # out of range
            },
        )


def test_o_ring_groove_path_metadata():
    assert ORingGroovePath.spec.name == "o_ring_groove_path"
    assert "o_ring_groove" in ORingGroovePath.spec.produces_features
    assert "closed_path_groove" in ORingGroovePath.spec.produces_features


# ─────────────────────── foam_seal_groove_racetrack ─────────────────────────


def test_foam_seal_racetrack_reduces_volume():
    # 80×50×8 host slab. Racetrack centerline = 60×30 with corner R=15
    # (true racetrack — semicircular caps). Groove 3 mm wide × 1 mm deep.
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 80.0, "width_mm": 50.0, "height_mm": 8.0},
    )
    v_before = _volume(box_result.body)

    skill = FoamSealGrooveRacetrack()
    r = skill.apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "length_mm":   60.0,
            "width_mm":    30.0,
            "corner_r_mm": 15.0,
            "groove_w_mm": 3.0,
            "groove_d_mm": 1.0,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "racetrack groove must remove material"

    # Expected centerline length = 2*(L − 2R) + 2πR for racetrack.
    L, W, R = 60.0, 30.0, 15.0
    perim = 2.0 * (L - 2.0 * R) + 2.0 * math.pi * R
    expected = perim * 3.0 * 1.0
    actual = v_before - v_after
    assert 0.4 * expected < actual < 1.8 * expected, (
        f"racetrack swept volume out of range: "
        f"expected≈{expected:.1f}, got {actual:.1f}"
    )


def test_foam_seal_racetrack_rejects_groove_wider_than_band():
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 80.0, "width_mm": 50.0, "height_mm": 8.0},
    )
    skill = FoamSealGrooveRacetrack()
    with pytest.raises(Exception):
        skill.apply(
            box_result.body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "length_mm":   60.0,
                "width_mm":    10.0,
                "corner_r_mm": 5.0,
                "groove_w_mm": 12.0,  # >= width_mm → invalid
                "groove_d_mm": 1.0,
            },
        )


def test_foam_seal_racetrack_rounded_rect_centerline_also_works():
    # corner_r_mm < width_mm/2 → rounded-rect centerline branch.
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 80.0, "width_mm": 50.0, "height_mm": 8.0},
    )
    v_before = _volume(box_result.body)
    skill = FoamSealGrooveRacetrack()
    r = skill.apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "length_mm":   60.0,
            "width_mm":    30.0,
            "corner_r_mm": 4.0,   # < width_mm/2 = 15 → rounded rect
            "groove_w_mm": 2.5,
            "groove_d_mm": 0.8,
        },
    )
    assert _volume(r.body) < v_before


def test_foam_seal_racetrack_metadata():
    assert FoamSealGrooveRacetrack.spec.name == "foam_seal_groove_racetrack"
    assert "foam_seal_groove" in FoamSealGrooveRacetrack.spec.produces_features
    assert "closed_path_groove" in FoamSealGrooveRacetrack.spec.produces_features
