"""Wave1-B mold depth — cooling channels, gating, runner, slide, ejector
pin pattern."""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.gating_position_candidates import (
    GatingPositionCandidates,
)
from phone_designer.skills.modify_mold.cooling_channel_path import (
    CoolingChannelPath,
)
from phone_designer.skills.modify_mold.ejector_pin_pattern import (
    EjectorPinPattern,
)
from phone_designer.skills.modify_mold.runner_diameter_calc import (
    RunnerDiameterCalc,
)
from phone_designer.skills.modify_mold.slide_action_geometry import (
    SlideActionGeometry,
)


def _volume(shape_or_part) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    shape = shape_or_part.wrapped if hasattr(shape_or_part, "wrapped") else shape_or_part
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, p)
    return p.Mass()


def _make_box(length=40.0, width=30.0, height=20.0):
    return Box().apply(
        None,
        {"length_mm": length, "width_mm": width, "height_mm": height},
    ).body


# --------------------------------------------------------- cooling_channel_path


def test_cooling_channel_path_cuts_two_segments():
    body = _make_box(60.0, 40.0, 30.0)
    v_before = _volume(body)
    pts = [(-20.0, 0.0, 5.0), (20.0, 0.0, 5.0), (20.0, 10.0, 5.0)]
    r = CoolingChannelPath().apply(
        body,
        {
            "channel_d_mm": 6.0,
            "target_distance_to_cavity_mm": 10.0,
            "path_seed_points": pts,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before
    report = r.extras["cooling_report"]
    assert report["channel_count"] == 2
    assert report["channel_d_mm"] == 6.0
    assert report["target_distance_to_cavity_mm"] == 10.0
    # total length ≈ 40 + 10 = 50 mm
    assert abs(report["total_length_mm"] - 50.0) < 1e-3


def test_cooling_channel_path_volume_loss_matches_cylinder_geometry():
    body = _make_box(60.0, 40.0, 30.0)
    v_before = _volume(body)
    # single straight segment along +X, length 30, d=4 → expected loss
    # = pi * 2^2 * 30 = 377.0 mm³ (within the body).
    pts = [(-15.0, 0.0, 5.0), (15.0, 0.0, 5.0)]
    r = CoolingChannelPath().apply(
        body,
        {
            "channel_d_mm": 4.0,
            "target_distance_to_cavity_mm": 5.0,
            "path_seed_points": pts,
        },
    )
    v_after = _volume(r.body)
    expected = math.pi * (2.0 ** 2) * 30.0
    actual = v_before - v_after
    # within 5%
    assert abs(actual - expected) / expected < 0.05


def test_cooling_channel_path_rejects_single_point():
    body = _make_box()
    with pytest.raises(Exception):
        CoolingChannelPath().apply(
            body,
            {
                "channel_d_mm": 5.0,
                "target_distance_to_cavity_mm": 10.0,
                "path_seed_points": [(0.0, 0.0, 5.0)],
            },
        )


def test_cooling_channel_path_spec_metadata():
    spec = CoolingChannelPath.spec
    assert spec.name == "cooling_channel_path"
    assert spec.category == "modify/mold"
    assert "cooling_channels" in spec.produces_features


# ---------------------------------------------------- gating_position_candidates


def test_gating_position_candidates_returns_n_results():
    body = _make_box(40.0, 30.0, 20.0)
    r = GatingPositionCandidates().apply(
        body,
        {"material": "abs", "num_candidates": 3},
    )
    cands = r.extras["gate_candidates"]
    assert len(cands) == 3
    for c in cands:
        assert "face_idx" in c
        assert "position" in c and len(c["position"]) == 3
        assert "flow_length_mm" in c and c["flow_length_mm"] >= 0.0
        assert "on_cosmetic" in c
        assert "score" in c
    # body unchanged
    assert _volume(r.body) == pytest.approx(_volume(body), rel=1e-6)


def test_gating_position_candidates_ranked_descending():
    body = _make_box(40.0, 30.0, 20.0)
    r = GatingPositionCandidates().apply(
        body,
        {"material": "pc", "num_candidates": 4},
    )
    scores = [c["score"] for c in r.extras["gate_candidates"]]
    assert scores == sorted(scores, reverse=True)


def test_gating_position_candidates_material_recorded():
    body = _make_box()
    r = GatingPositionCandidates().apply(body, {"material": "pa"})
    assert r.extras["gate_material"] == "pa"


def test_gating_position_candidates_spec_metadata():
    spec = GatingPositionCandidates.spec
    assert spec.name == "gating_position_candidates"
    assert spec.category == "inspect"


# --------------------------------------------------------- runner_diameter_calc


def test_runner_diameter_calc_returns_recommendation():
    body = _make_box(40.0, 30.0, 20.0)
    v = _volume(body)
    r = RunnerDiameterCalc().apply(
        body,
        {
            "part_volume_mm3": v,
            "flow_length_mm": 100.0,
            "material": "abs",
        },
    )
    rec = r.extras["runner_recommendation"]
    assert rec["material"] == "abs"
    assert rec["diameter_mm"] > 0.0
    assert rec["L_over_D"] > 0.0
    assert rec["mass_g"] > 0.0
    assert rec["density_g_per_cm3"] == pytest.approx(1.05, rel=1e-3)


def test_runner_diameter_calc_formula_matches_hand_calc():
    # part_volume = 10_000 mm³ ABS (density 1.05 g/cm3)
    # mass_g = 10000 * 1.05 / 1000 = 10.5 g
    # L = 80 mm
    # D = 0.2654 * sqrt(10.5) * 80^0.148
    body = _make_box(10.0, 10.0, 10.0)
    r = RunnerDiameterCalc().apply(
        body,
        {
            "part_volume_mm3": 10_000.0,
            "flow_length_mm": 80.0,
            "material": "abs",
        },
    )
    rec = r.extras["runner_recommendation"]
    expected = 0.2654 * math.sqrt(10.5) * (80.0 ** 0.148)
    assert rec["diameter_mm"] == pytest.approx(expected, rel=1e-3)


def test_runner_diameter_calc_body_unchanged():
    body = _make_box(20.0, 20.0, 20.0)
    v_before = _volume(body)
    r = RunnerDiameterCalc().apply(
        body,
        {"part_volume_mm3": 8000.0, "flow_length_mm": 50.0, "material": "pc"},
    )
    assert _volume(r.body) == pytest.approx(v_before, rel=1e-6)


def test_runner_diameter_calc_spec_metadata():
    spec = RunnerDiameterCalc.spec
    assert spec.name == "runner_diameter_calc"
    assert spec.category == "modify/mold"


# ------------------------------------------------------- slide_action_geometry


def test_slide_action_geometry_builds_block():
    body = _make_box(40.0, 30.0, 20.0)
    # face_idx 0 should be a valid face on the box
    r = SlideActionGeometry().apply(
        body,
        {
            "undercut_face_selector": {"face_idx": 0},
            "pull_direction": (0.0, 0.0, 1.0),
            "slide_clearance_mm": 0.5,
        },
    )
    # slide block volume > 0
    v_block = _volume(r.body)
    assert v_block > 0.0
    extras = r.extras
    assert extras["slide_block_volume_mm3"] > 0.0
    pp = extras["parting_plane"]
    assert "origin" in pp and "normal" in pp
    assert len(pp["normal"]) == 3
    # normal should be unit length
    n = pp["normal"]
    nmag = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
    assert abs(nmag - 1.0) < 1e-3


def test_slide_action_geometry_clearance_offsets_block():
    body = _make_box(40.0, 30.0, 20.0)
    r_small = SlideActionGeometry().apply(
        body,
        {
            "undercut_face_selector": {"face_idx": 0},
            "pull_direction": (0.0, 0.0, 1.0),
            "slide_clearance_mm": 0.1,
        },
    )
    r_big = SlideActionGeometry().apply(
        body,
        {
            "undercut_face_selector": {"face_idx": 0},
            "pull_direction": (0.0, 0.0, 1.0),
            "slide_clearance_mm": 5.0,
        },
    )
    # Parting plane origin moves by ≈ (5 - 0.1) mm along the slide normal.
    o_s = r_small.extras["parting_plane"]["origin"]
    o_b = r_big.extras["parting_plane"]["origin"]
    delta = math.sqrt(sum((o_b[i] - o_s[i]) ** 2 for i in range(3)))
    assert abs(delta - 4.9) < 0.05


def test_slide_action_geometry_rejects_bad_face_idx():
    body = _make_box()
    with pytest.raises(Exception):
        SlideActionGeometry().apply(
            body,
            {
                "undercut_face_selector": {"face_idx": 9999},
                "pull_direction": (0.0, 0.0, 1.0),
                "slide_clearance_mm": 0.5,
            },
        )


def test_slide_action_geometry_spec_metadata():
    spec = SlideActionGeometry.spec
    assert spec.name == "slide_action_geometry"
    assert spec.category == "modify/mold"
    assert "slide_block" in spec.produces_features


# ----------------------------------------------------------- ejector_pin_pattern


def test_ejector_pin_pattern_returns_target_count_positions():
    body = _make_box(40.0, 30.0, 20.0)
    r = EjectorPinPattern().apply(
        body,
        {
            "face_selector": {"face_idx": 0},
            "pin_diameter_mm": 2.0,
            "target_count": 4,
            "min_edge_distance_mm": 3.0,
            "random_seed": 7,
        },
    )
    positions = r.extras["ejector_positions"]
    assert len(positions) == 4
    assert r.extras["ejector_pin_diameter_mm"] == 2.0
    assert r.extras["ejector_face_idx"] == 0


def test_ejector_pin_pattern_body_unchanged():
    body = _make_box(40.0, 30.0, 20.0)
    v_before = _volume(body)
    r = EjectorPinPattern().apply(
        body,
        {
            "face_selector": {"face_idx": 0},
            "pin_diameter_mm": 2.0,
            "target_count": 3,
            "min_edge_distance_mm": 3.0,
        },
    )
    assert _volume(r.body) == pytest.approx(v_before, rel=1e-6)


def test_ejector_pin_pattern_deterministic_with_seed():
    body = _make_box(40.0, 30.0, 20.0)
    r1 = EjectorPinPattern().apply(
        body,
        {
            "face_selector": {"face_idx": 0},
            "target_count": 5,
            "min_edge_distance_mm": 3.0,
            "random_seed": 123,
        },
    )
    r2 = EjectorPinPattern().apply(
        body,
        {
            "face_selector": {"face_idx": 0},
            "target_count": 5,
            "min_edge_distance_mm": 3.0,
            "random_seed": 123,
        },
    )
    assert r1.extras["ejector_positions"] == r2.extras["ejector_positions"]


def test_ejector_pin_pattern_rejects_oversize_edge_distance():
    body = _make_box(20.0, 20.0, 10.0)
    with pytest.raises(Exception):
        EjectorPinPattern().apply(
            body,
            {
                "face_selector": {"face_idx": 0},
                "target_count": 4,
                # 50mm margin on a 20mm face → no valid placement region
                "min_edge_distance_mm": 50.0,
            },
        )


def test_ejector_pin_pattern_spec_metadata():
    spec = EjectorPinPattern.spec
    assert spec.name == "ejector_pin_pattern"
    assert spec.category == "modify/mold"
    assert "ejector_pin_pattern" in spec.produces_features
