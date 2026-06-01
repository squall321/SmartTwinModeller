"""Audio-deep pack (W2-C): waveguide horn, flared bass-reflex port,
anechoic chamber liner mount, MEMS mic boot pocket, speaker baffle window.

Each skill is exercised on a simple Box and verified:
  - volume invariants (volume_decreased / volume_increased / body_present),
  - manifest-level spec attributes (name, category, post_conditions),
  - skill-specific knobs (catalog wiring, profile choice, error rejection).
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_curvature.waveguide_horn_profile import (
    WaveguideHornProfile,
)
from phone_designer.skills.modify_pocket.anechoic_chamber_liner_mount import (
    AnechoicChamberLinerMount,
)
from phone_designer.skills.modify_pocket.bass_reflex_port_flared import (
    BassReflexPortFlared,
)
from phone_designer.skills.modify_pocket.mems_mic_boot_pocket import (
    MemsMicBootPocket,
)
from phone_designer.skills.modify_pocket.speaker_baffle_window import (
    SpeakerBaffleWindow,
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _make_box(L: float = 80.0, W: float = 80.0, H: float = 20.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ── waveguide_horn_profile ──────────────────────────────────────────────────


def test_waveguide_horn_profile_tractrix_body_present():
    # Create skill — input body is None, the horn shell is produced fresh.
    r = WaveguideHornProfile().apply(None, {
        "throat_d_mm": 20.0,
        "mouth_d_mm": 80.0,
        "length_mm": 60.0,
        "profile": "tractrix",
    })
    assert r.body is not None
    v = _volume(r.body)
    # The shell volume must be strictly positive — well above OCCT tolerance.
    assert v > 1.0


def test_waveguide_horn_profile_conical_volume_matches_frustum():
    r = WaveguideHornProfile().apply(None, {
        "throat_d_mm": 20.0,
        "mouth_d_mm": 80.0,
        "length_mm": 60.0,
        "profile": "conical",
        "wall_t_mm": 1.5,
    })
    assert r.body is not None
    v = _volume(r.body)
    # Analytic outer-frustum vol = π·L/3·(r1² + r1·r2 + r2²).
    r1 = 10.0; r2 = 40.0; L = 60.0
    v_outer = math.pi * L / 3.0 * (r1 * r1 + r1 * r2 + r2 * r2)
    # Inner frustum r1-1.5, r2-1.5.
    r1i = r1 - 1.5; r2i = r2 - 1.5
    v_inner = math.pi * L / 3.0 * (r1i * r1i + r1i * r2i + r2i * r2i)
    expected_shell = v_outer - v_inner
    # Allow generous slop for poly-line approximation.
    assert 0.6 * expected_shell < v < 1.4 * expected_shell


def test_waveguide_horn_profile_exponential():
    r = WaveguideHornProfile().apply(None, {
        "throat_d_mm": 20.0,
        "mouth_d_mm": 80.0,
        "length_mm": 60.0,
        "profile": "exponential",
    })
    assert r.body is not None
    assert _volume(r.body) > 1.0


def test_waveguide_horn_profile_throat_geq_mouth_rejected():
    with pytest.raises(ValueError, match="< mouth_d"):
        WaveguideHornProfile().apply(None, {
            "throat_d_mm": 80.0,
            "mouth_d_mm": 20.0,
            "length_mm": 60.0,
            "profile": "tractrix",
        })


def test_waveguide_horn_profile_spec_metadata():
    spec = WaveguideHornProfile.spec
    assert spec.name == "waveguide_horn_profile"
    assert spec.category == "modify/curvature"
    assert any(pc.kind == "body_present" for pc in spec.post_conditions)


# ── bass_reflex_port_flared ─────────────────────────────────────────────────


def test_bass_reflex_port_flared_decreases_volume_radius():
    box = _make_box(80, 80, 100)
    v0 = _volume(box)
    r = BassReflexPortFlared().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "throat_d_mm": 20.0,
        "mouth_d_mm": 40.0,
        "length_mm": 60.0,
        "flare_profile": "radius",
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Removed volume must be at least the throat-cylinder volume:
    #   π·(10)²·60 ≈ 18850 mm³.
    throat_cyl = math.pi * (10.0 ** 2) * 60.0
    assert (v0 - v1) > 0.5 * throat_cyl


def test_bass_reflex_port_flared_exponential_decreases_volume():
    box = _make_box(80, 80, 100)
    v0 = _volume(box)
    r = BassReflexPortFlared().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "throat_d_mm": 15.0,
        "mouth_d_mm": 35.0,
        "length_mm": 50.0,
        "flare_profile": "exponential",
    })
    assert _volume(r.body) < v0


def test_bass_reflex_port_flared_throat_geq_mouth_rejected():
    box = _make_box(60, 60, 60)
    with pytest.raises(ValueError, match="< mouth_d"):
        BassReflexPortFlared().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "throat_d_mm": 30.0,
            "mouth_d_mm": 25.0,
            "length_mm": 30.0,
        })


def test_bass_reflex_port_flared_spec_metadata():
    spec = BassReflexPortFlared.spec
    assert spec.name == "bass_reflex_port_flared"
    assert spec.category == "modify/pocket"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── anechoic_chamber_liner_mount ────────────────────────────────────────────


def test_anechoic_chamber_liner_mount_increases_volume():
    box = _make_box(300, 300, 20)
    v0 = _volume(box)
    r = AnechoicChamberLinerMount().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "foam_thickness_mm": 25.0,
        "mount_pin_d_mm": 3.0,
        "pin_pitch_mm": 80.0,
        "margin_mm": 5.0,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    # Each pin adds π * (1.5)² * 25 ≈ 176.7 mm³. With margin 5 + pin_r 1.5 =
    # 6.5 mm inset, 300-2*6.5 = 287 mm usable → ⌊287/80⌋+1 = 4 pins per row,
    # 4×4 = 16 pins → ≥ 14 worth of added material.
    one = math.pi * (1.5 ** 2) * 25.0
    assert (v1 - v0) > 14 * one


def test_anechoic_chamber_liner_mount_face_too_small_rejected():
    box = _make_box(30, 30, 20)
    with pytest.raises(ValueError, match="face too small"):
        AnechoicChamberLinerMount().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "foam_thickness_mm": 25.0,
            "mount_pin_d_mm": 3.0,
            "pin_pitch_mm": 80.0,
            "margin_mm": 20.0,   # 30/2 - 20 = -5 → too small
        })


def test_anechoic_chamber_liner_mount_pin_d_ge_pitch_rejected():
    box = _make_box(300, 300, 20)
    with pytest.raises(ValueError, match="pin_d"):
        AnechoicChamberLinerMount().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "foam_thickness_mm": 25.0,
            "mount_pin_d_mm": 80.0,
            "pin_pitch_mm": 80.0,
        })


def test_anechoic_chamber_liner_mount_spec_metadata():
    spec = AnechoicChamberLinerMount.spec
    assert spec.name == "anechoic_chamber_liner_mount"
    assert spec.category == "modify/pocket"
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── mems_mic_boot_pocket ────────────────────────────────────────────────────


def test_mems_mic_boot_pocket_sph0645_decreases_volume():
    box = _make_box(20, 20, 8)
    v0 = _volume(box)
    r = MemsMicBootPocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "mic_kind": "sph0645",
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Package recess volume = 3.5 × 2.65 × 0.98 ≈ 9.09 mm³ — the dominant cut.
    pkg_vol = 3.5 * 2.65 * 0.98
    assert (v0 - v1) > 0.5 * pkg_vol


def test_mems_mic_boot_pocket_im69d130_uses_catalog():
    box = _make_box(20, 20, 8)
    v0 = _volume(box)
    r = MemsMicBootPocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "mic_kind": "im69d130",
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # IM69D130 package 4 × 3 × 1.2 ≈ 14.4 mm³.
    pkg_vol = 4.0 * 3.0 * 1.2
    assert (v0 - v1) > 0.5 * pkg_vol


def test_mems_mic_boot_pocket_mp34dt06_uses_catalog():
    box = _make_box(20, 20, 8)
    v0 = _volume(box)
    r = MemsMicBootPocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "mic_kind": "mp34dt06",
    })
    v1 = _volume(r.body)
    assert v1 < v0


def test_mems_mic_boot_pocket_spec_metadata():
    spec = MemsMicBootPocket.spec
    assert spec.name == "mems_mic_boot_pocket"
    assert spec.category == "modify/pocket"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── speaker_baffle_window ───────────────────────────────────────────────────


def test_speaker_baffle_window_decreases_volume():
    box = _make_box(80, 80, 10)
    v0 = _volume(box)
    r = SpeakerBaffleWindow().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "baffle_w_mm": 30.0,
        "baffle_h_mm": 20.0,
        "corner_r_mm": 2.0,
        "grille_pattern_inset_mm": 2.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Through-cut alone removes 30 × 20 × 10 = 6000 mm³ (rough — corners
    # round it but rounding is small).
    window_through = 30.0 * 20.0 * 10.0
    assert (v0 - v1) > 0.5 * window_through


def test_speaker_baffle_window_corner_r_too_large_rejected():
    box = _make_box(80, 80, 10)
    with pytest.raises(ValueError, match="corner_r"):
        SpeakerBaffleWindow().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "baffle_w_mm": 20.0,
            "baffle_h_mm": 20.0,
            "corner_r_mm": 15.0,    # > min_half (10)
            "grille_pattern_inset_mm": 1.0,
        })


def test_speaker_baffle_window_zero_corner_radius():
    box = _make_box(80, 80, 10)
    v0 = _volume(box)
    r = SpeakerBaffleWindow().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "baffle_w_mm": 20.0,
        "baffle_h_mm": 15.0,
        "corner_r_mm": 0.0,
        "grille_pattern_inset_mm": 2.0,
    })
    assert _volume(r.body) < v0


def test_speaker_baffle_window_spec_metadata():
    spec = SpeakerBaffleWindow.spec
    assert spec.name == "speaker_baffle_window"
    assert spec.category == "modify/pocket"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)
