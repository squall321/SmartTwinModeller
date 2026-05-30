"""Tests for Wave3-C create skills.

Skills under test:
    - gear_helical_involute
    - worm_thread

Both are atomic create skills (input body = None, post_condition
body_present). Tests verify body validity, volume plausibility, axis-aligned
bounding-box envelope, and a couple of failure-mode rejections.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.gear_helical_involute import GearHelicalInvolute
from phone_designer.skills.create.worm_thread import WormThread


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _bbox(body):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(body.wrapped, bb, True)
    return bb.Get()


# ── gear_helical_involute ─────────────────────────────────────────────────


def test_gear_helical_involute_body_present_zero_helix():
    """β = 0 should degenerate to a straight spur gear extrusion."""
    r = GearHelicalInvolute().apply(None, {
        "module_mm": 1.0,
        "teeth": 20,
        "helix_angle_deg": 0.0,
        "face_width_mm": 5.0,
        "pressure_angle_deg": 20.0,
    })
    assert r.body is not None
    v = _volume(r.body)
    assert v > 0.0

    # m=1, z=20 → pitch_r=10, add_r=11, ded_r=8.75. Volume between annular
    # bounds.
    pitch_r = 10.0
    add_r = 11.0
    ded_r = 8.75
    width = 5.0
    v_lo = math.pi * ded_r * ded_r * width * 0.85
    v_hi = math.pi * add_r * add_r * width * 1.05
    assert v_lo <= v <= v_hi, (
        f"helical gear (β=0) volume {v:.2f} not in [{v_lo:.2f},{v_hi:.2f}]"
    )

    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    assert zmin == pytest.approx(0.0, abs=0.05)
    assert zmax == pytest.approx(width, abs=0.05)
    # Radial envelope ~ addendum radius (slight slack for outline polyline).
    assert xmax <= add_r + 0.3
    assert xmin >= -(add_r + 0.3)


def test_gear_helical_involute_default_args_smoke():
    """All defaults — β = 15°, z = 20, m = 1, W = 10 mm — should produce a
    valid solid with the proper axial extent."""
    r = GearHelicalInvolute().apply(None, {})
    assert r.body is not None
    v = _volume(r.body)
    assert v > 0.0
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    # Face width = 10 mm along Z.
    assert zmax - zmin == pytest.approx(10.0, abs=0.2)


def test_gear_helical_involute_helix_increases_face_count():
    """A helical gear (β > 0) is built from multiple stacked slabs so its
    face count must exceed the straight-extrusion equivalent."""
    straight = GearHelicalInvolute().apply(None, {
        "module_mm": 1.0,
        "teeth": 16,
        "helix_angle_deg": 0.0,
        "face_width_mm": 6.0,
    })
    helical = GearHelicalInvolute().apply(None, {
        "module_mm": 1.0,
        "teeth": 16,
        "helix_angle_deg": 20.0,
        "face_width_mm": 6.0,
    })
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    def count_faces(b) -> int:
        it = TopExp_Explorer(b.wrapped, TopAbs_FACE)
        n = 0
        while it.More():
            n += 1
            it.Next()
        return n

    assert count_faces(helical.body) > count_faces(straight.body)


def test_gear_helical_involute_rejects_too_few_teeth():
    with pytest.raises(Exception):
        GearHelicalInvolute().apply(None, {
            "module_mm": 1.0,
            "teeth": 4,         # < 8
            "face_width_mm": 5.0,
        })


# ── worm_thread ───────────────────────────────────────────────────────────


def test_worm_thread_volume_and_bbox():
    """m=1, γ=5°, Dp=20, L=30 → R=10. Axial pitch px = π·m/cos(5°) ≈ 3.155;
    turns ≈ 9.5. Cut-shaft volume must be slightly less than the raw shaft
    π·R²·L = 9424.78, since material was removed by the thread groove."""
    r = WormThread().apply(None, {
        "module_mm": 1.0,
        "lead_angle_deg": 5.0,
        "pitch_d_mm": 20.0,
        "length_mm": 30.0,
    })
    assert r.body is not None
    v = _volume(r.body)
    R = 10.0
    L = 30.0
    v_full = math.pi * R * R * L
    # Removed volume is a small fraction of the shaft — expect 0.5·v_full
    # ≤ v ≤ v_full (plus tiny OCCT floating-point slack).
    upper = v_full * (1 + 1e-4)
    assert 0.5 * v_full <= v <= upper, (
        f"worm volume {v:.2f} not in [{0.5 * v_full:.2f},{upper:.2f}]"
    )

    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    assert zmin == pytest.approx(0.0, abs=0.05)
    assert zmax == pytest.approx(L, abs=0.05)
    # Radial envelope == shaft radius (cut only removes material).
    assert xmax == pytest.approx(R, abs=0.3)
    assert xmin == pytest.approx(-R, abs=0.3)


def test_worm_thread_default_args_smoke():
    r = WormThread().apply(None, {})
    assert r.body is not None
    assert _volume(r.body) > 0.0


def test_worm_thread_rejects_thread_height_exceeds_radius():
    # Module so large that 2.25·m >= R = Dp/2.
    with pytest.raises(Exception):
        WormThread().apply(None, {
            "module_mm": 5.0,         # 2.25*5 = 11.25 >= R = 5
            "lead_angle_deg": 5.0,
            "pitch_d_mm": 10.0,
            "length_mm": 30.0,
        })


def test_worm_thread_rejects_length_too_short():
    # Axial pitch ≈ π·m/cos(γ). If length < axial pitch, fail.
    with pytest.raises(Exception):
        WormThread().apply(None, {
            "module_mm": 5.0,
            "lead_angle_deg": 5.0,
            "pitch_d_mm": 40.0,
            "length_mm": 5.0,        # px ≈ 15.77; length < px
        })


# ── spec metadata sanity ──────────────────────────────────────────────────


def test_all_wave3c_skills_have_body_present_post_condition():
    from phone_designer.skills._post_conditions import PostCondition
    for cls in (GearHelicalInvolute, WormThread):
        pcs = cls.spec.post_conditions
        assert any(
            isinstance(p, PostCondition) and p.kind == "body_present" for p in pcs
        ), f"{cls.spec.name} missing body_present post_condition"


def test_all_wave3c_skills_are_atomic_create():
    for cls in (GearHelicalInvolute, WormThread):
        assert cls.spec.category == "create"
        assert cls.spec.level == "atomic"
