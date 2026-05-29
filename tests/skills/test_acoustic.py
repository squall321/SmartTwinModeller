"""Acoustic pack: speaker_acoustic_chamber, helmholtz_port_tube,
acoustic_grille_pattern.

Each skill runs on a simple box body and we verify:
  - volume strictly decreases (post_conditions: volume_decreased),
  - manifest-level spec attributes (category, post_conditions),
  - skill-specific knobs (catalog wiring, Helmholtz length retuning, hex/square
    point counts).
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pattern.acoustic_grille_pattern import (
    AcousticGrillePattern,
)
from phone_designer.skills.modify_pocket.helmholtz_port_tube import (
    HelmholtzPortTube,
)
from phone_designer.skills.modify_pocket.speaker_acoustic_chamber import (
    SpeakerAcousticChamber,
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _make_box(L: float = 40.0, W: float = 40.0, H: float = 20.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ── speaker_acoustic_chamber ────────────────────────────────────────────────


def test_speaker_acoustic_chamber_11x15_decreases_volume():
    box = _make_box(40, 40, 20)
    v0 = _volume(box)
    r = SpeakerAcousticChamber().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "speaker_spec": "Speaker_11x15",
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # main chamber alone ≈ π * (15/2)² * 11 ≈ 1944 mm³ — at least ~half of that
    # should be removed even after the stepped-shelf overlap.
    expected_chamber = math.pi * (15.0 / 2.0) ** 2 * 11.0
    delta = v0 - v1
    assert delta > 0.5 * expected_chamber


def test_speaker_acoustic_chamber_8x12_uses_catalog_dims():
    box = _make_box(30, 30, 15)
    v0 = _volume(box)
    r = SpeakerAcousticChamber().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "speaker_spec": "Speaker_8x12",
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # main chamber ≈ π * 6² * 8 ≈ 905 mm³
    expected_chamber = math.pi * (12.0 / 2.0) ** 2 * 8.0
    delta = v0 - v1
    assert delta > 0.5 * expected_chamber


def test_speaker_acoustic_chamber_unknown_spec_rejected():
    box = _make_box(30, 30, 15)
    with pytest.raises(ValueError, match="unknown speaker_spec"):
        SpeakerAcousticChamber().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "speaker_spec": "DoesNotExist",
        })


def test_speaker_acoustic_chamber_spec_metadata():
    spec = SpeakerAcousticChamber.spec
    assert spec.name == "speaker_acoustic_chamber"
    assert spec.category == "modify/pocket"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── helmholtz_port_tube ─────────────────────────────────────────────────────


def test_helmholtz_port_tube_decreases_volume():
    box = _make_box(30, 30, 15)
    v0 = _volume(box)
    r = HelmholtzPortTube().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "port_diameter_mm": 3.0,
        "port_length_mm": 8.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Approximate port cylinder volume = π * 1.5² * 8 ≈ 56.5 mm³.
    expected = math.pi * (1.5 ** 2) * 8.0
    delta = v0 - v1
    assert 0.7 * expected < delta < 1.4 * expected


def test_helmholtz_port_tube_retunes_from_fb_and_volume():
    """If target_fb_hz and cabinet_volume_mm3 are given, length is overridden.

    Use a small cabinet so the analytic L is comfortably above the explicit
    length the user passed, then check the removed volume matches the tuned L.
    """
    box = _make_box(40, 40, 25)
    v0 = _volume(box)
    # Inputs
    d = 3.0
    fb = 800.0          # Hz — relatively high so L is modest
    V = 1500.0          # mm³ — small cabinet
    # Analytic tuned length (matches the skill's formula).
    r = d / 2.0
    A = math.pi * r * r
    L_eff = (343_000.0 ** 2) * A / (4.0 * math.pi * math.pi * fb * fb * V)
    L_tuned = L_eff - 1.7 * r
    assert L_tuned > 0.0

    res = HelmholtzPortTube().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "port_diameter_mm": d,
        "port_length_mm": 1.0,   # caller passes something — should be ignored
        "target_fb_hz": fb,
        "cabinet_volume_mm3": V,
    })
    v1 = _volume(res.body)
    delta = v0 - v1
    expected = math.pi * r * r * L_tuned
    # Allow some slop for the overshoot & boolean tolerance.
    assert 0.7 * expected < delta < 1.5 * expected


def test_helmholtz_port_tube_rejects_nonpositive_tuned_length():
    """Tiny port + huge V + high fb drives L_eff below the end-correction
    1.7·r, so the tuned length goes non-positive → ValueError.
    """
    box = _make_box(30, 30, 15)
    with pytest.raises(ValueError, match="non-positive"):
        HelmholtzPortTube().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "port_diameter_mm": 0.5,
            "port_length_mm": 1.0,
            "target_fb_hz": 20000.0,
            "cabinet_volume_mm3": 1_000_000_000.0,
        })


def test_helmholtz_port_tube_spec_metadata():
    spec = HelmholtzPortTube.spec
    assert spec.name == "helmholtz_port_tube"
    assert spec.category == "modify/pocket"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── acoustic_grille_pattern ─────────────────────────────────────────────────


def test_acoustic_grille_pattern_hex_drills_many_holes():
    box = _make_box(40, 40, 5)
    v0 = _volume(box)
    r = AcousticGrillePattern().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "region_w_mm": 12.0,
        "region_h_mm": 10.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Each through-hole removes π * (0.4)² * 5 ≈ 2.51 mm³.
    # Hex pitch 1.5 in 12x10 → many points; expect at least 20 holes worth.
    one = math.pi * (0.4 ** 2) * 5.0
    assert (v0 - v1) > 20.0 * one


def test_acoustic_grille_pattern_square_drills_holes():
    box = _make_box(40, 40, 5)
    v0 = _volume(box)
    r = AcousticGrillePattern().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "region_w_mm": 9.0,
        "region_h_mm": 9.0,
        "hole_d_mm": 0.8,
        "pitch_mm": 1.5,
        "kind": "square",
    })
    v1 = _volume(r.body)
    assert v1 < v0


def test_acoustic_grille_pattern_rejects_hole_geq_pitch():
    box = _make_box(40, 40, 5)
    with pytest.raises(ValueError, match="overlap"):
        AcousticGrillePattern().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "region_w_mm": 10.0,
            "region_h_mm": 10.0,
            "hole_d_mm": 2.0,
            "pitch_mm": 2.0,
        })


def test_acoustic_grille_pattern_spec_metadata():
    spec = AcousticGrillePattern.spec
    assert spec.name == "acoustic_grille_pattern"
    assert spec.category == "modify/pattern"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)
