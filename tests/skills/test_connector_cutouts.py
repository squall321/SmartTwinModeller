"""connector_cutout_from_catalog — USB-C + 3.5 mm audio jack.

Catalog-driven rounded-rectangle / round cutouts on a host shell face.
Verifies volume_decreased post-condition and checks swept-volume magnitudes
against catalog dimensions.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.connector_cutout_from_catalog import (
    ConnectorCutoutFromCatalog,
)


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


# ───────────────────────────── USB-C ───────────────────────────────────────


def test_usb_c_cutout_removes_catalog_volume():
    # Host shell large enough for a USB-C receptacle + 7.35 mm recess.
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 40, "width_mm": 30, "height_mm": 12},
    )
    v_before = _volume(box_result.body)

    cut = ConnectorCutoutFromCatalog()
    r = cut.apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "connector_spec": "USB_C_Receptacle_v2",
            "catalog_family": "usb_c",
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "USB-C cutout must remove material"

    # Rounded-rectangle area: w·h - (4 - π)·r²   (square corners replaced by quarter-disks)
    w, h, rr, d = 9.0, 3.4, 1.65, 7.35
    rect_area = w * h - (4.0 - math.pi) * (rr ** 2)
    expected = rect_area * d
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02, (
        f"USB-C cutout volume: expected {expected:.2f}, got {actual:.2f}"
    )


def test_usb_c_cutout_respects_depth_override():
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 40, "width_mm": 30, "height_mm": 12},
    )
    v_before = _volume(box_result.body)

    cut = ConnectorCutoutFromCatalog()
    r = cut.apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "connector_spec": "USB_C_Receptacle_v2",
            "catalog_family": "usb_c",
            "recess_depth_override_mm": 3.0,
        },
    )
    v_after = _volume(r.body)
    w, h, rr, d = 9.0, 3.4, 1.65, 3.0
    rect_area = w * h - (4.0 - math.pi) * (rr ** 2)
    expected = rect_area * d
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02


# ───────────────────────────── audio jack ──────────────────────────────────


def test_audio_jack_round_cutout_removes_catalog_volume():
    # Host block big enough for a 14 mm-deep barrel cut.
    box_inst = Box()
    box_result = box_inst.apply(
        None, {"length_mm": 25, "width_mm": 25, "height_mm": 20},
    )
    v_before = _volume(box_result.body)

    cut = ConnectorCutoutFromCatalog()
    r = cut.apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "connector_spec": "Audio_Jack_3.5mm",
            "catalog_family": "audio_jack_35",
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "audio jack cutout must remove material"

    # Ø 6.2 mm × 14 mm cylinder: π · 3.1² · 14 ≈ 422.7
    expected = math.pi * (6.2 / 2.0) ** 2 * 14.0
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02, (
        f"audio jack volume: expected {expected:.2f}, got {actual:.2f}"
    )


# ───────────────────────────── error paths ─────────────────────────────────


def test_unknown_connector_spec_raises():
    box_result = Box().apply(
        None, {"length_mm": 30, "width_mm": 30, "height_mm": 10},
    )
    cut = ConnectorCutoutFromCatalog()
    with pytest.raises(ValueError, match="unknown connector_spec"):
        cut.apply(
            box_result.body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "connector_spec": "NotAReal_Connector",
                "catalog_family": "usb_c",
            },
        )


def test_spec_metadata():
    spec = ConnectorCutoutFromCatalog.spec
    assert spec.name == "connector_cutout_from_catalog"
    assert "connector_cutout" in spec.produces_features
    # post-condition must include volume_decreased
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "volume_decreased" in kinds
