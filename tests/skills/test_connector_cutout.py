"""connector_cutout_from_catalog — USB-C + audio jack via combined 'usb' family.

Catalog-driven rounded-rectangle / round cutouts on a host shell face using
the consolidated catalogs/connectors/usb.yaml file (contains USB-C, USB-A,
3.5 mm audio jack and Apple Lightning entries).
"""
from __future__ import annotations

import math

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


def test_usb_c_cutout_from_usb_family():
    """USB-C rounded-rect cutout removes the catalog-specified volume."""
    box_result = Box().apply(
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
            "catalog_family": "usb",
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "USB-C cutout must remove material"

    # Rounded-rectangle: w·h - (4 - π)·r², swept by recess_depth.
    w, h, rr, d = 9.0, 3.4, 1.65, 7.35
    rect_area = w * h - (4.0 - math.pi) * (rr ** 2)
    expected = rect_area * d
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02, (
        f"USB-C cutout volume: expected {expected:.2f}, got {actual:.2f}"
    )


def test_audio_jack_cutout_from_usb_family():
    """3.5 mm audio jack — pure round cutout Ø6.2 × 14 mm deep."""
    box_result = Box().apply(
        None, {"length_mm": 25, "width_mm": 25, "height_mm": 20},
    )
    v_before = _volume(box_result.body)

    cut = ConnectorCutoutFromCatalog()
    r = cut.apply(
        box_result.body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "connector_spec": "Audio_Jack_3.5mm",
            "catalog_family": "usb",
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "audio jack cutout must remove material"

    expected = math.pi * (6.2 / 2.0) ** 2 * 14.0
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02, (
        f"audio jack volume: expected {expected:.2f}, got {actual:.2f}"
    )
