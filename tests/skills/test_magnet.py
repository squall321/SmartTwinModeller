"""magnet_pocket_axial — catalog-driven axial NdFeB disc magnet pocket.

Validates volume_decreased post-condition, retention clearance, proud_offset
behaviour, and error paths.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.magnet_pocket_axial import (
    MagnetPocketAxial,
)


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _box(length=40.0, width=30.0, height=10.0):
    return Box().apply(
        None,
        {"length_mm": length, "width_mm": width, "height_mm": height},
    ).body


# ─────────────────────── volume / retention ────────────────────────────────


@pytest.mark.parametrize(
    "retention,clearance_mm",
    [("press", -0.02), ("glue", +0.05), ("loose", +0.10)],
)
def test_retention_clearance_drives_pocket_diameter(retention, clearance_mm):
    # N42_D8x3 — 8 × 3 mm magnet.
    body = _box()
    v_before = _volume(body)

    r = MagnetPocketAxial().apply(
        body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "magnet_spec": "N42_D8x3",
            "retention": retention,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "magnet pocket must remove material"

    magnet_d, magnet_t = 8.0, 3.0
    proud = 0.05
    pocket_d = magnet_d + clearance_mm
    pocket_depth = magnet_t - proud
    expected = math.pi * (pocket_d / 2.0) ** 2 * pocket_depth
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.02, (
        f"{retention} pocket volume: expected {expected:.4f}, got "
        f"{actual:.4f}"
    )


# ──────────────────────── proud_offset_mm ──────────────────────────────────


def test_proud_offset_reduces_depth():
    # Larger proud_offset → shallower pocket → less removed volume.
    body_a = _box()
    body_b = _box()
    v0 = _volume(body_a)

    r_default = MagnetPocketAxial().apply(
        body_a,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "magnet_spec": "N42_D6x2",
            "retention": "glue",
        },
    )
    r_extra = MagnetPocketAxial().apply(
        body_b,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "magnet_spec": "N42_D6x2",
            "retention": "glue",
            "proud_offset_mm": 0.5,
        },
    )

    removed_default = v0 - _volume(r_default.body)
    removed_extra = v0 - _volume(r_extra.body)
    assert removed_extra < removed_default, (
        f"larger proud_offset should remove less material: "
        f"default={removed_default:.4f}, extra={removed_extra:.4f}"
    )

    # Spot-check the extra-proud pocket against analytic volume.
    pocket_d = 6.0 + 0.05      # glue clearance
    pocket_depth = 2.0 - 0.5   # proud_offset_mm = 0.5
    expected = math.pi * (pocket_d / 2.0) ** 2 * pocket_depth
    assert abs(removed_extra - expected) / expected < 0.02


# ──────────────────────── position_xy offset ───────────────────────────────


def test_position_xy_offsets_pocket():
    # Offset shouldn't change removed volume (still inside host face).
    body_a = _box(length=40, width=30, height=10)
    body_b = _box(length=40, width=30, height=10)
    v0 = _volume(body_a)

    r0 = MagnetPocketAxial().apply(
        body_a,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "magnet_spec": "N42_D5x2",
            "retention": "press",
        },
    )
    r_off = MagnetPocketAxial().apply(
        body_b,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (10.0, 8.0),
            "magnet_spec": "N42_D5x2",
            "retention": "press",
        },
    )
    removed_centred = v0 - _volume(r0.body)
    removed_offset = v0 - _volume(r_off.body)
    assert abs(removed_centred - removed_offset) / removed_centred < 0.01


# ──────────────────────── error paths ──────────────────────────────────────


def test_unknown_magnet_spec_raises():
    body = _box()
    with pytest.raises(ValueError, match="unknown magnet_spec"):
        MagnetPocketAxial().apply(
            body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "magnet_spec": "NotAReal_Magnet",
            },
        )


def test_max_proud_offset_still_positive_depth():
    # proud_offset = 1.0 mm on a 2 mm magnet → depth = 1.0 mm (still valid).
    body = _box()
    v0 = _volume(body)
    r = MagnetPocketAxial().apply(
        body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "magnet_spec": "N42_D6x2",      # magnet_t = 2.0
            "proud_offset_mm": 1.0,
            "retention": "loose",
        },
    )
    removed = v0 - _volume(r.body)
    pocket_d = 6.0 + 0.10
    pocket_depth = 2.0 - 1.0
    expected = math.pi * (pocket_d / 2.0) ** 2 * pocket_depth
    assert abs(removed - expected) / expected < 0.02


# ──────────────────────── spec metadata ────────────────────────────────────


def test_spec_metadata():
    spec = MagnetPocketAxial.spec
    assert spec.name == "magnet_pocket_axial"
    assert "magnet_pocket_axial" in spec.produces_features
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "volume_decreased" in kinds
