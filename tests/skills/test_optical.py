"""Tests for optical / lighting feature skills (PACK W2-D).

Skills under test:
    - modify_pocket/led_smd_pocket
    - modify_pocket/lens_seat_pocket
    - modify_pocket/light_pipe_routing_channel
    - modify_curvature/optical_window_dome
    - inspect/ray_trace_smoke

Plus the three optical catalogs:
    - catalogs/optical/leds.yaml
    - catalogs/optical/lenses.yaml
    - catalogs/optical/light_pipes.yaml
"""
from __future__ import annotations

import math
import pathlib

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.ray_trace_smoke import RayTraceSmoke
from phone_designer.skills.modify_curvature.optical_window_dome import (
    OpticalWindowDome,
)
from phone_designer.skills.modify_finish.surface_finish_tag import get_finish_tags
from phone_designer.skills.modify_pocket.led_smd_pocket import LedSmdPocket
from phone_designer.skills.modify_pocket.lens_seat_pocket import LensSeatPocket
from phone_designer.skills.modify_pocket.light_pipe_routing_channel import (
    LightPipeRoutingChannel,
)


# ─── helpers ────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _box(L: float = 40.0, W: float = 40.0, H: float = 10.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ─── catalog presence ───────────────────────────────────────────────────────


def _project_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def test_optical_catalogs_present():
    root = _project_root()
    for name in ("leds.yaml", "lenses.yaml", "light_pipes.yaml"):
        assert (root / "catalogs" / "optical" / name).exists(), \
            f"missing catalog: catalogs/optical/{name}"


def test_leds_catalog_has_expected_keys():
    import yaml
    root = _project_root()
    data = yaml.safe_load(
        (root / "catalogs" / "optical" / "leds.yaml").read_text()
    )
    leds = data["leds"]
    for spec in ("0402", "0603", "0805", "1206", "5050", "3535"):
        assert spec in leds, f"missing LED spec: {spec}"
        entry = leds[spec]
        for k in ("length_mm", "width_mm", "height_mm",
                   "pad_size_mm", "pad_spacing_mm"):
            assert k in entry, f"LED {spec} missing key {k}"


def test_lenses_catalog_has_expected_keys():
    import yaml
    root = _project_root()
    data = yaml.safe_load(
        (root / "catalogs" / "optical" / "lenses.yaml").read_text()
    )
    lenses = data["lenses"]
    for spec in ("Plano-Convex_Ø6_f10", "Plano-Convex_Ø12_f15",
                  "Aspheric_Ø8_f8", "Cylindrical_Ø10_f20"):
        assert spec in lenses, f"missing lens: {spec}"
        entry = lenses[spec]
        for k in ("outer_d_mm", "thickness_mm", "focal_length_mm",
                   "surface_type"):
            assert k in entry, f"lens {spec} missing key {k}"


def test_light_pipes_catalog_has_expected_keys():
    import yaml
    root = _project_root()
    data = yaml.safe_load(
        (root / "catalogs" / "optical" / "light_pipes.yaml").read_text()
    )
    pipes = data["pipes"]
    for spec in ("Round_Ø1.2", "Round_Ø1.5", "Round_Ø2.0", "Slot_2x6"):
        assert spec in pipes, f"missing pipe: {spec}"
        entry = pipes[spec]
        assert "bend_radius_min_mm" in entry, \
            f"pipe {spec} missing bend_radius_min_mm"
        if entry.get("cross_section", "round") == "slot":
            assert "length_mm" in entry and "width_mm" in entry
        else:
            assert "cross_section_d_mm" in entry


# ─── led_smd_pocket ─────────────────────────────────────────────────────────


def test_led_smd_pocket_0603_decreases_volume():
    box = _box(20, 20, 5)
    v0 = _volume(box)
    r = LedSmdPocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "led_spec": "0603",
        "with_pad_relief": False,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # 0603: 1.6 × 0.8 × 0.6 + 2*0.1 side clearance per axis
    led_l, led_w, led_h, c = 1.6, 0.8, 0.6, 0.1
    expected = (led_l + 2 * c) * (led_w + 2 * c) * led_h
    actual = v0 - v1
    assert abs(actual - expected) / expected < 0.02


def test_led_smd_pocket_with_pad_relief_removes_more_volume():
    """Enabling with_pad_relief should remove strictly more volume than the
    raw-body pocket alone (the relief adds a wider, shallower step on top)."""
    box_a = _box(20, 20, 5)
    box_b = _box(20, 20, 5)
    r_no = LedSmdPocket().apply(box_a, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "led_spec": "1206",
        "with_pad_relief": False,
    })
    r_yes = LedSmdPocket().apply(box_b, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "led_spec": "1206",
        "with_pad_relief": True,
    })
    v_no = _volume(r_no.body)
    v_yes = _volume(r_yes.body)
    # With relief should leave LESS material → smaller v_yes.
    assert v_yes < v_no


def test_led_smd_pocket_unknown_spec_raises():
    box = _box()
    with pytest.raises(ValueError, match="unknown led_spec"):
        LedSmdPocket().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "led_spec": "9999",
        })


def test_led_smd_pocket_spec_metadata():
    spec = LedSmdPocket.spec
    assert spec.name == "led_smd_pocket"
    assert spec.category == "modify/pocket"
    assert "led_smd_pocket" in spec.produces_features
    assert {pc.kind for pc in spec.post_conditions} >= {"volume_decreased"}


# ─── lens_seat_pocket ───────────────────────────────────────────────────────


def test_lens_seat_pocket_decreases_volume():
    box = _box(30, 30, 10)
    v0 = _volume(box)
    r = LensSeatPocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "lens_spec": "Plano-Convex_Ø6_f10",
        "optical_clearance_mm": 0.3,
    })
    v1 = _volume(r.body)
    assert v1 < v0

    # outer seat (Ø6.6, depth 2.4) fused with inner clear-aperture (Ø5.4,
    # depth 0.3). Total ≈ outer + inner_below (the part below the seat).
    outer_d = 6.0 + 2 * 0.3
    inner_d = 6.0 - 2 * 0.3
    outer_v = math.pi * (outer_d / 2) ** 2 * 2.4
    inner_v = math.pi * (inner_d / 2) ** 2 * 0.3
    expected = outer_v + inner_v
    actual = v0 - v1
    assert abs(actual - expected) / expected < 0.05


def test_lens_seat_pocket_unknown_spec_raises():
    box = _box()
    with pytest.raises(ValueError, match="unknown lens_spec"):
        LensSeatPocket().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "lens_spec": "NotARealLens",
        })


def test_lens_seat_pocket_clearance_out_of_range_raises():
    """Pydantic Field bound (le=2.0): clearance values that would collapse
    the inner clear-aperture are rejected at validation time."""
    from pydantic import ValidationError
    box = _box()
    with pytest.raises(ValidationError):
        LensSeatPocket().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "lens_spec": "Plano-Convex_Ø6_f10",
            "optical_clearance_mm": 5.0,  # > le=2.0 cap
        })


def test_lens_seat_pocket_spec_metadata():
    spec = LensSeatPocket.spec
    assert spec.name == "lens_seat_pocket"
    assert spec.category == "modify/pocket"
    assert "lens_seat_pocket" in spec.produces_features
    assert {pc.kind for pc in spec.post_conditions} >= {"volume_decreased"}


# ─── light_pipe_routing_channel ─────────────────────────────────────────────


def test_light_pipe_routing_channel_round_decreases_volume():
    box = _box(40, 40, 20)
    v0 = _volume(box)
    r = LightPipeRoutingChannel().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "path_points": [(-10.0, 0.0), (10.0, 0.0)],
        "pipe_spec": "Round_Ø1.5",
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Straight tunnel: π * (0.75)² * 20 ≈ 35.3 mm³.
    expected = math.pi * (0.75 ** 2) * 20.0
    actual = v0 - v1
    assert 0.6 * expected < actual < 1.5 * expected


def test_light_pipe_routing_channel_slot_decreases_volume():
    box = _box(40, 40, 20)
    v0 = _volume(box)
    r = LightPipeRoutingChannel().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "path_points": [(-10.0, 0.0), (10.0, 0.0)],
        "pipe_spec": "Slot_2x6",
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Capsule (slot) area = (L-W)*W + π*(W/2)²
    #   L=6, W=2 → (6-2)*2 + π*1 = 8 + π ≈ 11.14 mm².
    # Sweep length ≈ 20 mm → ≈ 222 mm³. Boolean tool clipping at the host
    # face edges may eat into that, so we allow a generous window.
    actual = v0 - v1
    assert 5.0 * 20.0 < actual < 15.0 * 20.0


def test_light_pipe_routing_channel_l_shaped_path():
    box = _box(40, 40, 20)
    v0 = _volume(box)
    r = LightPipeRoutingChannel().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "path_points": [(-8.0, -8.0), (-8.0, 8.0), (8.0, 8.0)],
        "pipe_spec": "Round_Ø1.2",
    })
    v1 = _volume(r.body)
    assert v1 < v0


def test_light_pipe_routing_channel_unknown_spec_raises():
    box = _box()
    with pytest.raises(ValueError, match="unknown pipe_spec"):
        LightPipeRoutingChannel().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "path_points": [(-5.0, 0.0), (5.0, 0.0)],
            "pipe_spec": "Square_5",
        })


def test_light_pipe_routing_channel_spec_metadata():
    spec = LightPipeRoutingChannel.spec
    assert spec.name == "light_pipe_routing_channel"
    assert spec.category == "modify/pocket"
    assert "light_pipe_routing_channel" in spec.produces_features
    assert {pc.kind for pc in spec.post_conditions} >= {"volume_decreased"}


# ─── optical_window_dome ────────────────────────────────────────────────────


def test_optical_window_dome_increases_volume():
    box = _box(30, 30, 5)
    v0 = _volume(box)
    r = OpticalWindowDome().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "window_d_mm": 6.0,
        "dome_height_mm": 0.3,
        "polish_tag": False,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    # Spherical-cap volume: V = π h² (3R - h) / 3, with R = (a²+h²)/(2h).
    a = 3.0
    h = 0.3
    R = (a * a + h * h) / (2 * h)
    expected = math.pi * h * h * (3 * R - h) / 3.0
    actual = v1 - v0
    assert abs(actual - expected) / expected < 0.05


def test_optical_window_dome_polish_tag_attaches_finish():
    box = _box(30, 30, 5)
    r = OpticalWindowDome().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "window_d_mm": 8.0,
        "dome_height_mm": 0.4,
        "polish_tag": True,
    })
    tags = get_finish_tags(r.body)
    assert "polish" in tags
    assert len(tags["polish"]) >= 1
    record = tags["polish"][-1]
    assert record["target_ra_um"] == pytest.approx(0.05)
    # Center should sit at the dome apex Z = 5.0 + 0.4 = 5.4
    assert record["center"][2] == pytest.approx(5.4, abs=1e-3)


def test_optical_window_dome_height_too_large_raises():
    box = _box()
    with pytest.raises(ValueError, match="dome_height_mm"):
        OpticalWindowDome().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "window_d_mm": 4.0,
            "dome_height_mm": 3.0,  # >= radius 2.0
        })


def test_optical_window_dome_spec_metadata():
    spec = OpticalWindowDome.spec
    assert spec.name == "optical_window_dome"
    assert spec.category == "modify/curvature"
    assert "optical_window_dome" in spec.produces_features
    assert {pc.kind for pc in spec.post_conditions} >= {"volume_changed"}


# ─── ray_trace_smoke ────────────────────────────────────────────────────────


def test_ray_trace_smoke_head_on_hit_top_face():
    box = _box(20, 20, 5)
    r = RayTraceSmoke().apply(box, {
        "light_source_xyz": (0.0, 0.0, 20.0),
        "light_direction": (0.0, 0.0, -1.0),
        "target_face_selector": {"kind": "face_named", "name": "top"},
    })
    hit = r.extras["ray_hit"]
    assert hit["hit"] is True
    assert hit["distance_mm"] == pytest.approx(15.0, abs=1e-3)
    assert hit["on_target"] is True
    # Head-on ray → incidence angle 0°.
    assert hit["incidence_angle_deg"] == pytest.approx(0.0, abs=1e-3)


def test_ray_trace_smoke_miss():
    box = _box(20, 20, 5)
    r = RayTraceSmoke().apply(box, {
        "light_source_xyz": (100.0, 100.0, 100.0),
        "light_direction": (1.0, 1.0, 1.0),
        "target_face_selector": {"kind": "face_named", "name": "top"},
    })
    hit = r.extras["ray_hit"]
    assert hit["hit"] is False
    assert hit["distance_mm"] is None
    assert hit["on_target"] is False


def test_ray_trace_smoke_hits_non_target_face():
    """Ray from below should hit bottom face → on_target is False even
    though body is hit."""
    box = _box(20, 20, 5)
    r = RayTraceSmoke().apply(box, {
        "light_source_xyz": (0.0, 0.0, -20.0),
        "light_direction": (0.0, 0.0, 1.0),
        "target_face_selector": {"kind": "face_named", "name": "top"},
    })
    hit = r.extras["ray_hit"]
    assert hit["hit"] is True
    assert hit["on_target"] is False


def test_ray_trace_smoke_zero_direction_raises():
    box = _box()
    with pytest.raises(ValueError, match="light_direction"):
        RayTraceSmoke().apply(box, {
            "light_source_xyz": (0.0, 0.0, 10.0),
            "light_direction": (0.0, 0.0, 0.0),
            "target_face_selector": {"kind": "face_named", "name": "top"},
        })


def test_ray_trace_smoke_body_unchanged():
    """Read-only — output body is the same shape (volume preserved)."""
    box = _box(15, 15, 4)
    v0 = _volume(box)
    r = RayTraceSmoke().apply(box, {
        "light_source_xyz": (0.0, 0.0, 10.0),
        "light_direction": (0.0, 0.0, -1.0),
        "target_face_selector": {"kind": "face_named", "name": "top"},
    })
    v1 = _volume(r.body)
    assert v1 == pytest.approx(v0, rel=1e-9)


def test_ray_trace_smoke_spec_metadata():
    spec = RayTraceSmoke.spec
    assert spec.name == "ray_trace_smoke"
    assert spec.category == "inspect"
    assert "ray_hit_report" in spec.produces_features
    assert {pc.kind for pc in spec.post_conditions} >= {"body_present"}
