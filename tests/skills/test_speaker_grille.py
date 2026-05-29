"""speaker_grille_curved_array — concentric-ring grille smoke test."""
from __future__ import annotations

import math

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pattern.speaker_grille_curved_array import (
    SpeakerGrilleCurvedArray,
)


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _make_box(L: float = 40.0, W: float = 40.0, H: float = 5.0):
    return Box().apply(
        None, {"length_mm": L, "width_mm": W, "height_mm": H},
    ).body


def test_speaker_grille_curved_array_decreases_volume():
    box = _make_box(40, 40, 5)
    v0 = _volume(box)
    r = SpeakerGrilleCurvedArray().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "center_xy": (0.0, 0.0),
        "outer_d_mm": 12.0,
        "inner_d_mm": 4.0,
        "ring_count": 3,
        "hole_d_mm": 0.8,
        "holes_per_ring": 24,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # 3 rings × 24 holes = 72 through-holes of d=0.8 through H=5.
    # Each removes π * 0.4² * 5 ≈ 2.513 mm³ → ≥ ~50% of nominal total.
    one = math.pi * (0.4 ** 2) * 5.0
    assert (v0 - v1) > 0.5 * 72.0 * one

    spec = SpeakerGrilleCurvedArray.spec
    assert spec.name == "speaker_grille_curved_array"
    assert spec.category == "modify/pattern"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)
