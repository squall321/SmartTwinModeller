"""Mobile housing pack — sim_tray, display_bezel_step, camera_ring_stepped,
side_button_aperture.

Each skill cuts a recognizable cavity into a host box. We check that the
declared volume_decreased post-condition holds (and, where geometry is simple
enough, that the swept volume matches expectation within a tolerance).
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.camera_ring_stepped import (
    CameraRingStepped,
)
from phone_designer.skills.modify_pocket.display_bezel_step import (
    DisplayBezelStep,
)
from phone_designer.skills.modify_pocket.side_button_aperture import (
    SideButtonAperture,
)
from phone_designer.skills.modify_pocket.sim_tray_pocket import SimTrayPocket


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


# ──────────────────────────── sim_tray_pocket ──────────────────────────────


def test_sim_tray_pocket_removes_material():
    box_result = Box().apply(
        None, {"length_mm": 60, "width_mm": 30, "height_mm": 12},
    )
    v_before = _volume(box_result.body)

    r = SimTrayPocket().apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "tray_l_mm": 12.3,
            "tray_w_mm": 8.8,
            "tray_d_mm": 4.5,
            "eject_hole_d_mm": 0.8,
            "eject_offset_mm": 1.0,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "sim_tray_pocket must remove material"

    # Lower bound: tray cavity volume itself.
    tray_vol = 12.3 * 8.8 * 4.5
    actual = v_before - v_after
    assert actual >= tray_vol * 0.95, (
        f"sim_tray_pocket volume: tray cavity should be at least "
        f"{tray_vol:.2f}, got {actual:.2f}"
    )


def test_sim_tray_pocket_spec_metadata():
    spec = SimTrayPocket.spec
    assert spec.name == "sim_tray_pocket"
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "volume_decreased" in kinds


# ─────────────────────────── display_bezel_step ────────────────────────────


def test_display_bezel_step_removes_material():
    box_result = Box().apply(
        None, {"length_mm": 80, "width_mm": 40, "height_mm": 10},
    )
    v_before = _volume(box_result.body)

    r = DisplayBezelStep().apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "display_l_mm": 50.0,
            "display_w_mm": 25.0,
            "step_depth_mm": 0.6,
            "adhesive_w_mm": 1.5,
            "adhesive_d_mm": 0.2,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "display_bezel_step must remove material"

    # The step floor itself: 50 × 25 × 0.6 = 750.
    step_vol = 50.0 * 25.0 * 0.6
    actual = v_before - v_after
    assert actual >= step_vol * 0.95, (
        f"display_bezel_step volume: step floor should be at least "
        f"{step_vol:.2f}, got {actual:.2f}"
    )


def test_display_bezel_step_rejects_oversize_adhesive():
    box_result = Box().apply(
        None, {"length_mm": 40, "width_mm": 30, "height_mm": 10},
    )
    with pytest.raises(ValueError, match="adhesive_w_mm"):
        DisplayBezelStep().apply(
            box_result.body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "display_l_mm": 6.0,
                "display_w_mm": 6.0,
                "adhesive_w_mm": 4.0,
            },
        )


def test_display_bezel_step_spec_metadata():
    spec = DisplayBezelStep.spec
    assert spec.name == "display_bezel_step"
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "volume_decreased" in kinds


# ──────────────────────── camera_ring_stepped ──────────────────────────────


def test_camera_ring_stepped_removes_material():
    box_result = Box().apply(
        None, {"length_mm": 40, "width_mm": 40, "height_mm": 15},
    )
    v_before = _volume(box_result.body)

    r = CameraRingStepped().apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "base_d_mm": 11.0,
            "top_d_mm": 9.0,
            "ring_count": 2,
            "ring_d_mm": 0.4,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "camera_ring_stepped must remove material"

    # Lower bound: 2 stacked cylinders Ø11×0.4 + Ø9×0.4.
    expected = (math.pi * (11.0 / 2.0) ** 2 * 0.4
                + math.pi * (9.0 / 2.0) ** 2 * 0.4)
    actual = v_before - v_after
    # Allow generous slack — boolean union overlaps the smaller ring within
    # the larger, but our rings don't overlap (they're stacked at different
    # depths), so the sum should be close.
    assert abs(actual - expected) / expected < 0.05, (
        f"camera_ring volume: expected ≈ {expected:.2f}, got {actual:.2f}"
    )


def test_camera_ring_stepped_rejects_inverted_taper():
    box_result = Box().apply(
        None, {"length_mm": 30, "width_mm": 30, "height_mm": 10},
    )
    with pytest.raises(ValueError, match="top_d_mm"):
        CameraRingStepped().apply(
            box_result.body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "base_d_mm": 8.0,
                "top_d_mm": 12.0,
            },
        )


def test_camera_ring_stepped_spec_metadata():
    spec = CameraRingStepped.spec
    assert spec.name == "camera_ring_stepped"
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "volume_decreased" in kinds


# ──────────────────────── side_button_aperture ─────────────────────────────


def test_side_button_aperture_removes_material():
    # Box sized so a +X face is wide enough for an 18-mm Z slot.
    box_result = Box().apply(
        None, {"length_mm": 20, "width_mm": 20, "height_mm": 60},
    )
    v_before = _volume(box_result.body)

    r = SideButtonAperture().apply(
        box_result.body,
        {
            "face_selector": {
                "kind": "faces_by_normal", "direction": (1.0, 0.0, 0.0),
            },
            "position_on_edge_mm": 0.0,
            "button_l_mm": 18.0,
            "button_w_mm": 2.5,
            "corner_r_mm": 0.4,
            "depth_through": True,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "side_button_aperture must remove material"

    # Rounded-rectangle area:
    # w·L - (4 - π)·r²
    L, W, r_ = 18.0, 2.5, 0.4
    area = L * W - (4.0 - math.pi) * (r_ ** 2)
    # Box length along world X (wall traverse) is 20 mm.
    expected = area * 20.0
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02, (
        f"side_button volume: expected ≈ {expected:.2f}, got {actual:.2f}"
    )


def test_side_button_aperture_rejects_oversize_radius():
    box_result = Box().apply(
        None, {"length_mm": 20, "width_mm": 20, "height_mm": 40},
    )
    with pytest.raises(ValueError, match="corner_r_mm"):
        SideButtonAperture().apply(
            box_result.body,
            {
                "face_selector": {
                    "kind": "faces_by_normal", "direction": (1.0, 0.0, 0.0),
                },
                "button_l_mm": 18.0,
                "button_w_mm": 2.5,
                "corner_r_mm": 2.0,   # 2*2.0 > 2.5
            },
        )


def test_side_button_aperture_rejects_blind():
    box_result = Box().apply(
        None, {"length_mm": 20, "width_mm": 20, "height_mm": 40},
    )
    with pytest.raises(NotImplementedError, match="depth_through"):
        SideButtonAperture().apply(
            box_result.body,
            {
                "face_selector": {
                    "kind": "faces_by_normal", "direction": (1.0, 0.0, 0.0),
                },
                "depth_through": False,
            },
        )


def test_side_button_aperture_spec_metadata():
    spec = SideButtonAperture.spec
    assert spec.name == "side_button_aperture"
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "volume_decreased" in kinds
