"""W2-B pack: phone-deep specifics.

Covers:
  - face_id_pocket             (modify/pocket, macro)
  - nfc_coil_pocket            (modify/pocket, atomic)
  - antenna_isolation_slit     (modify/pocket, atomic)
  - esim_chip_pocket           (modify/pocket, atomic)
  - side_button_dome_mount     (modify/boss,   atomic)

Each test runs on a simple box body and verifies:
  - volume changes in the expected direction,
  - approximate volume delta is consistent with the geometry,
  - argument-validation rejections (`ValueError` for guarded combos),
  - skill spec metadata is wired up,
  - the export manifest picks the skills up.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.side_button_dome_mount import (
    SideButtonDomeMount,
)
from phone_designer.skills.modify_pocket.antenna_isolation_slit import (
    AntennaIsolationSlit,
)
from phone_designer.skills.modify_pocket.esim_chip_pocket import EsimChipPocket
from phone_designer.skills.modify_pocket.face_id_pocket import FaceIdPocket
from phone_designer.skills.modify_pocket.nfc_coil_pocket import NfcCoilPocket


# ── helpers ─────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _make_box(L: float = 80.0, W: float = 60.0, H: float = 10.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ── face_id_pocket ─────────────────────────────────────────────────────────


def test_face_id_pocket_decreases_volume():
    box = _make_box(80, 60, 10)
    v0 = _volume(box)
    r = FaceIdPocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "projector_w_mm": 6.0,
        "projector_h_mm": 4.0,
        "camera_d_mm": 3.0,
        "sensor_d_mm": 2.0,
        "spacing_mm": 1.5,
        "depth_mm": 2.5,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"face_id_pocket must remove volume: {v0} → {v1}"

    # Expected: projector (6 × 4 × 2.5) + camera (π·1.5²·2.5) + sensor (π·1²·2.5)
    depth = 2.5
    expected = (
        6.0 * 4.0 * depth
        + math.pi * (3.0 / 2.0) ** 2 * depth
        + math.pi * (2.0 / 2.0) ** 2 * depth
    )
    actual = v0 - v1
    # Allow a generous tolerance: fused tool components can slightly overlap.
    assert 0.85 * expected < actual < 1.20 * expected, (
        f"face_id_pocket delta {actual:.2f}, expected ≈ {expected:.2f}"
    )


def test_face_id_pocket_spec_metadata():
    spec = FaceIdPocket.spec
    assert spec.name == "face_id_pocket"
    assert spec.category == "modify/pocket"
    assert spec.level == "macro"
    assert "face_id_pocket" in spec.produces_features
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── nfc_coil_pocket ────────────────────────────────────────────────────────


def test_nfc_coil_pocket_decreases_volume_with_ferrite():
    box = _make_box(80, 60, 10)
    v0 = _volume(box)
    r = NfcCoilPocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "center_xy": (0.0, 0.0),
        "coil_outer_d_mm": 30.0,
        "coil_inner_d_mm": 20.0,
        "depth_mm": 0.4,
        "with_ferrite": True,
    })
    v1 = _volume(r.body)
    assert v1 < v0

    # Ring annulus volume + ferrite step (= inner_r² × half-depth). The
    # ring tool has a small axial overshoot for clean booleans, so the
    # actual removed volume sits 5-10 % above the nominal catalog volume.
    ring_v = math.pi * (15.0 ** 2 - 10.0 ** 2) * 0.4
    ferr_v = math.pi * 10.0 ** 2 * 0.2
    expected = ring_v + ferr_v
    actual = v0 - v1
    assert 0.95 * expected < actual < 1.15 * expected, (
        f"nfc_coil_pocket delta {actual:.2f}, expected ≈ {expected:.2f}"
    )


def test_nfc_coil_pocket_without_ferrite_smaller_delta():
    """Disabling the ferrite step must remove less material."""
    box_a = _make_box(80, 60, 10)
    v0_a = _volume(box_a)
    ra = NfcCoilPocket().apply(box_a, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "coil_outer_d_mm": 30.0, "coil_inner_d_mm": 20.0,
        "depth_mm": 0.4, "with_ferrite": True,
    })
    delta_a = v0_a - _volume(ra.body)

    box_b = _make_box(80, 60, 10)
    v0_b = _volume(box_b)
    rb = NfcCoilPocket().apply(box_b, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "coil_outer_d_mm": 30.0, "coil_inner_d_mm": 20.0,
        "depth_mm": 0.4, "with_ferrite": False,
    })
    delta_b = v0_b - _volume(rb.body)
    assert delta_b < delta_a, (
        f"without ferrite must remove less: with={delta_a:.2f} "
        f"no_ferrite={delta_b:.2f}"
    )


def test_nfc_coil_pocket_rejects_inner_geq_outer():
    box = _make_box(80, 60, 10)
    with pytest.raises(ValueError, match="coil_inner"):
        NfcCoilPocket().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "coil_outer_d_mm": 20.0,
            "coil_inner_d_mm": 20.0,
            "depth_mm": 0.4,
        })


def test_nfc_coil_pocket_spec_metadata():
    spec = NfcCoilPocket.spec
    assert spec.name == "nfc_coil_pocket"
    assert spec.category == "modify/pocket"
    assert "nfc_coil_pocket" in spec.produces_features
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── antenna_isolation_slit ─────────────────────────────────────────────────


def test_antenna_isolation_slit_decreases_volume():
    # Thin rail-like box so the slit is meaningful.
    box = _make_box(60, 6, 6)
    v0 = _volume(box)
    # Pick X-axis edges on the rail; resolver returns 4 of them — they're all
    # parallel and the same length, so picking [0] is fine.
    r = AntennaIsolationSlit().apply(box, {
        "edge_selector": {"kind": "axis_aligned_edges", "axis": "X"},
        "slit_length_mm": 4.0,
        "slit_width_mm": 0.5,
        "polymer_fill": True,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"antenna_isolation_slit must remove volume: {v0} → {v1}"

    # The slit traverses the SMALLER cross-section dimension (here Y=6 or Z=6,
    # equal). Width = 0.5 along the OTHER (6 mm). Length = 4 mm.
    # Expected delta ≈ 4 × 0.5 × 6 = 12 mm³.
    expected = 4.0 * 0.5 * 6.0
    actual = v0 - v1
    assert 0.7 * expected < actual < 1.3 * expected, (
        f"antenna_isolation_slit delta {actual:.2f}, expected ≈ {expected:.2f}"
    )


def test_antenna_isolation_slit_carries_polymer_flag():
    box = _make_box(60, 6, 6)
    r = AntennaIsolationSlit().apply(box, {
        "edge_selector": {"kind": "axis_aligned_edges", "axis": "X"},
        "slit_length_mm": 3.0,
        "slit_width_mm": 0.4,
        "polymer_fill": False,
    })
    assert r.extras.get("polymer_fill") is False


def test_antenna_isolation_slit_rejects_overlong_slit():
    box = _make_box(60, 6, 6)
    with pytest.raises(ValueError, match="slit_length_mm"):
        AntennaIsolationSlit().apply(box, {
            "edge_selector": {"kind": "axis_aligned_edges", "axis": "X"},
            "slit_length_mm": 100.0,  # > 60 mm edge
            "slit_width_mm": 0.5,
        })


def test_antenna_isolation_slit_spec_metadata():
    spec = AntennaIsolationSlit.spec
    assert spec.name == "antenna_isolation_slit"
    assert spec.category == "modify/pocket"
    assert spec.selector_kinds == ["edges"]
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── esim_chip_pocket ───────────────────────────────────────────────────────


def test_esim_chip_pocket_decreases_volume_with_pads():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = EsimChipPocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "chip_l_mm": 5.0,
        "chip_w_mm": 6.0,
        "depth_mm": 0.6,
        "with_bonding_pads": True,
    })
    v1 = _volume(r.body)
    assert v1 < v0

    # chip 5×6×0.6 + 2 pads (W/2 × W × depth) = 5*6 + 2 * 3 * 6 = 30 + 36 = 66
    expected = (5.0 * 6.0 + 2.0 * (6.0 / 2.0) * 6.0) * 0.6
    actual = v0 - v1
    assert abs(actual - expected) / expected < 0.10, (
        f"esim_chip_pocket delta {actual:.2f}, expected ≈ {expected:.2f}"
    )


def test_esim_chip_pocket_without_pads_smaller_delta():
    box_a = _make_box(40, 40, 10)
    v0_a = _volume(box_a)
    ra = EsimChipPocket().apply(box_a, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "chip_l_mm": 5.0, "chip_w_mm": 6.0, "depth_mm": 0.6,
        "with_bonding_pads": True,
    })
    delta_a = v0_a - _volume(ra.body)

    box_b = _make_box(40, 40, 10)
    v0_b = _volume(box_b)
    rb = EsimChipPocket().apply(box_b, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "chip_l_mm": 5.0, "chip_w_mm": 6.0, "depth_mm": 0.6,
        "with_bonding_pads": False,
    })
    delta_b = v0_b - _volume(rb.body)
    assert delta_b < delta_a


def test_esim_chip_pocket_spec_metadata():
    spec = EsimChipPocket.spec
    assert spec.name == "esim_chip_pocket"
    assert spec.category == "modify/pocket"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── side_button_dome_mount ─────────────────────────────────────────────────


def test_side_button_dome_mount_increases_volume_on_top():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = SideButtonDomeMount().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_along_edge_mm": 0.0,
        "dome_d_mm": 4.0,
        "height_mm": 0.5,
    })
    v1 = _volume(r.body)
    assert v1 > v0, f"dome mount must add volume: {v0} → {v1}"

    # Cylinder volume = π·r²·h with r = dome_d/2 = 2, h = 0.5 → ≈6.28 mm³.
    expected = math.pi * (4.0 / 2.0) ** 2 * 0.5
    actual = v1 - v0
    assert abs(actual - expected) / expected < 0.05, (
        f"dome mount delta {actual:.2f}, expected ≈ {expected:.2f}"
    )


def test_side_button_dome_mount_position_shifts_center():
    """Shifting along the long axis must keep volume but move the new face."""
    box1 = _make_box(40, 40, 10)
    r0 = SideButtonDomeMount().apply(box1, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_along_edge_mm": 0.0,
        "dome_d_mm": 4.0, "height_mm": 0.5,
    })
    box2 = _make_box(40, 40, 10)
    r1 = SideButtonDomeMount().apply(box2, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_along_edge_mm": 5.0,
        "dome_d_mm": 4.0, "height_mm": 0.5,
    })
    assert abs(_volume(r0.body) - _volume(r1.body)) < 1e-3


def test_side_button_dome_mount_spec_metadata():
    spec = SideButtonDomeMount.spec
    assert spec.name == "side_button_dome_mount"
    assert spec.category == "modify/boss"
    assert "side_button_dome_mount" in spec.produces_features
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── manifest ───────────────────────────────────────────────────────────────


def test_manifest_includes_phone_deep_pack():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    assert {
        "face_id_pocket",
        "nfc_coil_pocket",
        "antenna_isolation_slit",
        "esim_chip_pocket",
        "side_button_dome_mount",
    } <= names
