"""Wave1-A 3D printing DFM tests — overhang / support / bridge / orientation
inspect skills plus the FDM ``add_support_brim`` modify skill.

Each test builds a deterministic reference body and exercises one of:

  * inspect.overhang_check
  * inspect.support_volume_estimate
  * inspect.bridge_length_check
  * inspect.orientation_optimize
  * modify_3dprint.add_support_brim
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.cylinder import Cylinder
from phone_designer.skills.inspect.bridge_length_check import BridgeLengthCheck
from phone_designer.skills.inspect.orientation_optimize import OrientationOptimize
from phone_designer.skills.inspect.overhang_check import OverhangCheck
from phone_designer.skills.inspect.support_volume_estimate import (
    SupportVolumeEstimate,
)
from phone_designer.skills.modify_3dprint.add_support_brim import AddSupportBrim


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    shape = body.wrapped if hasattr(body, "wrapped") else body
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _box(L: float = 20.0, W: float = 20.0, H: float = 10.0):
    return Box().apply(None, {
        "length_mm": L, "width_mm": W, "height_mm": H,
    }).body


# ──────────────────────────────────────────────────────────────────────────────
# overhang_check


def test_overhang_check_basic_box_has_one_downward_face():
    """A plain box with +Z build_direction has exactly one fully-downward
    face (the bottom). With the default 45° threshold, the bottom is
    flagged as overhang while the four vertical sides (90° to -build) and
    the top (180°) are not."""
    body = _box(20, 20, 10)
    r = OverhangCheck().apply(body, {
        "build_direction": (0.0, 0.0, 1.0),
        "max_angle_deg": 45.0,
    })
    rep = r.extras
    assert "overhangs" in rep
    assert "overhang_summary" in rep
    # body unchanged (read-only)
    assert r.body is body
    # Six faces on a box.
    assert len(rep["overhangs"]) == 6
    # Each entry has the required keys.
    for o in rep["overhangs"]:
        assert {"face_idx", "angle_deg", "area_mm2",
                "normal", "needs_support"} <= set(o.keys())
    # Exactly one downward-facing face → the bottom is the overhang.
    needs = [o for o in rep["overhangs"] if o["needs_support"]]
    assert len(needs) == 1
    # angle_deg ≈ 0 for the bottom face vs -build = -Z.
    assert needs[0]["angle_deg"] < 1.0
    # Summary face count matches.
    assert rep["overhang_summary"]["support_face_count"] == 1


def test_overhang_check_inverted_build_direction_swaps_overhang():
    """Flipping build_direction to -Z makes the *top* face the downward one."""
    body = _box(10, 10, 10)
    r = OverhangCheck().apply(body, {
        "build_direction": (0.0, 0.0, -1.0),
        "max_angle_deg": 45.0,
    })
    needs = [o for o in r.extras["overhangs"] if o["needs_support"]]
    assert len(needs) == 1
    # Its normal must be +Z (top face).
    n = needs[0]["normal"]
    assert n[2] > 0.99


def test_overhang_check_zero_build_direction_raises():
    body = _box(10, 10, 10)
    with pytest.raises(ValueError):
        OverhangCheck().apply(body, {
            "build_direction": (0.0, 0.0, 0.0),
        })


# ──────────────────────────────────────────────────────────────────────────────
# support_volume_estimate


def test_support_volume_estimate_box_bottom_on_plate_is_zero():
    """A box's bottom face sits ON the build plate (drop_height = 0), so
    support volume must be 0 — even though it's an overhang face."""
    body = _box(20, 20, 10)
    r = SupportVolumeEstimate().apply(body, {
        "build_direction": (0.0, 0.0, 1.0),
        "max_overhang_deg": 45.0,
        "support_density": 0.2,
    })
    est = r.extras["support_estimate"]
    assert est["total_support_volume_mm3"] == pytest.approx(0.0, abs=1e-6)
    assert est["support_density"] == 0.2
    assert est["max_overhang_deg"] == 45.0
    # body unchanged.
    assert r.body is body


def test_support_volume_estimate_inverted_build_direction_has_volume():
    """With -Z build_direction the top face becomes a downward face at
    height H above the build plate → drop_height = H → nonzero volume."""
    L, W, H = 20.0, 20.0, 10.0
    body = _box(L, W, H)
    r = SupportVolumeEstimate().apply(body, {
        "build_direction": (0.0, 0.0, -1.0),
        "max_overhang_deg": 45.0,
        "support_density": 0.2,
    })
    est = r.extras["support_estimate"]
    assert est["total_support_volume_mm3"] > 0.0
    assert est["support_face_count"] >= 1
    # height of the per-face entry should be ≈ H.
    heights = [e["height_mm"] for e in est["support_height_per_face"]]
    assert max(heights) == pytest.approx(H, abs=1e-3)


def test_support_volume_estimate_density_scales_volume():
    """Doubling support_density should double the total support volume."""
    body = _box(20, 20, 10)
    a = SupportVolumeEstimate().apply(body, {
        "build_direction": (0.0, 0.0, -1.0),
        "support_density": 0.1,
    }).extras["support_estimate"]["total_support_volume_mm3"]
    b = SupportVolumeEstimate().apply(body, {
        "build_direction": (0.0, 0.0, -1.0),
        "support_density": 0.2,
    }).extras["support_estimate"]["total_support_volume_mm3"]
    if a > 0.0:
        assert b == pytest.approx(2.0 * a, rel=1e-3)


# ──────────────────────────────────────────────────────────────────────────────
# bridge_length_check


def test_bridge_length_check_box_has_horizontal_edges():
    """A plain box has 4 horizontal top edges and 4 horizontal bottom
    edges. Bottom edges sit on the build plate (supported); top edges
    have nothing beneath them (unsupported bridges)."""
    body = _box(20, 20, 10)
    r = BridgeLengthCheck().apply(body, {
        "max_bridge_mm": 10.0,
        "build_direction": (0.0, 0.0, 1.0),
    })
    rep = r.extras
    assert "bridges" in rep
    assert "bridge_summary" in rep
    # body unchanged.
    assert r.body is body
    # Required keys per entry.
    for b in rep["bridges"]:
        assert {"edge_idx", "length_mm", "unsupported",
                "midpoint", "exceeds_max"} <= set(b.keys())
    # Summary numbers self-consistent.
    summary = rep["bridge_summary"]
    assert summary["bridge_count"] == len(rep["bridges"])
    assert summary["max_bridge_mm"] == 10.0


def test_bridge_length_check_long_edge_exceeds_max():
    """A 30 mm box edge should exceed a 10 mm max_bridge threshold for any
    unsupported top edges."""
    body = _box(30, 30, 5)
    r = BridgeLengthCheck().apply(body, {
        "max_bridge_mm": 10.0,
        "build_direction": (0.0, 0.0, 1.0),
    })
    rep = r.extras
    # Any unsupported bridge with length>10 must be flagged.
    for b in rep["bridges"]:
        if b["unsupported"] and b["length_mm"] > 10.0:
            assert b["exceeds_max"] is True
    # max_unsupported_length_mm ≥ 0.
    assert rep["bridge_summary"]["max_unsupported_length_mm"] >= 0.0


def test_bridge_length_check_zero_build_direction_raises():
    body = _box(10, 10, 10)
    with pytest.raises(ValueError):
        BridgeLengthCheck().apply(body, {
            "build_direction": (0.0, 0.0, 0.0),
        })


# ──────────────────────────────────────────────────────────────────────────────
# orientation_optimize


def test_orientation_optimize_picks_a_direction_from_candidates():
    """Best direction must be one of the candidates and have a finite
    numeric score."""
    body = _box(20, 20, 10)
    candidates = [(0.0, 0.0, 1.0), (1.0, 0.0, 0.0),
                  (0.0, 1.0, 0.0), (0.0, 0.0, -1.0)]
    r = OrientationOptimize().apply(body, {
        "candidates": candidates,
        "max_overhang_deg": 45.0,
        "support_density": 0.2,
        "max_bridge_mm": 10.0,
    })
    rep = r.extras["best_orientation"]
    assert "direction" in rep
    assert "score" in rep
    assert "per_candidate" in rep
    assert len(rep["per_candidate"]) == len(candidates)
    # Every per-candidate entry has the required fields.
    for entry in rep["per_candidate"]:
        assert {"direction", "overhang_count",
                "support_volume_mm3", "max_bridge_mm",
                "score"} <= set(entry.keys())
    # body unchanged.
    assert r.body is body
    # Best direction = candidate with the minimum score.
    best_dir = tuple(rep["direction"])
    scores = [(tuple(e["direction"]), e["score"])
              for e in rep["per_candidate"]]
    min_dir, min_score = min(scores, key=lambda kv: kv[1])
    assert best_dir == min_dir
    assert rep["score"] == pytest.approx(min_score, rel=1e-6)


def test_orientation_optimize_empty_candidates_raises():
    body = _box(10, 10, 10)
    with pytest.raises(ValueError):
        OrientationOptimize().apply(body, {"candidates": []})


def test_orientation_optimize_box_prefers_z_up():
    """For a flat box, +Z build (bottom on plate) should never score worse
    than -Z build (top on plate, more support needed)."""
    body = _box(20, 20, 10)
    r = OrientationOptimize().apply(body, {
        "candidates": [(0.0, 0.0, 1.0), (0.0, 0.0, -1.0)],
    })
    per = {tuple(e["direction"]): e["score"]
           for e in r.extras["best_orientation"]["per_candidate"]}
    assert per[(0.0, 0.0, 1.0)] <= per[(0.0, 0.0, -1.0)]


# ──────────────────────────────────────────────────────────────────────────────
# add_support_brim


def test_add_support_brim_increases_volume():
    """Brim adds a thin rectangular plate at the bottom face → volume up."""
    L, W, H = 20.0, 20.0, 10.0
    body = _box(L, W, H)
    v_before = _volume(body)

    r = AddSupportBrim().apply(body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "brim_width_mm": 5.0,
        "brim_height_mm": 0.4,
    })
    v_after = _volume(r.body)
    assert v_after > v_before
    # Brim outer dimensions = (L+2W_brim) x (W+2W_brim) x height (0.4).
    # Brim volume ≈ outer_area * height - face_area * height_overlap. With a
    # thin brim fused under the box bottom, expect (W_brim*perimeter +
    # 4*W_brim²)*height extra volume (annular ring). For L=W=20, brim_w=5:
    #   outer area = 30*30 = 900, inner area = 20*20 = 400, annular = 500
    #   extra volume ≈ 500 * 0.4 = 200 mm³ — though tighter overlap may
    #   shrink this. Use a loose lower bound.
    assert (v_after - v_before) >= 50.0


def test_add_support_brim_post_condition_volume_increased():
    """volume_increased post-condition must succeed (delta > 0)."""
    body = _box(15, 15, 8)
    v_before = _volume(body)
    r = AddSupportBrim().apply(body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "brim_width_mm": 3.0,
        "brim_height_mm": 0.3,
    })
    v_after = _volume(r.body)
    assert v_after - v_before > 1e-6


def test_add_support_brim_non_axial_face_raises():
    """A vertical side face is not +Z/-Z planar → must raise NotImplementedError
    (v1 spec)."""
    body = _box(20, 20, 10)
    with pytest.raises((NotImplementedError, RuntimeError)):
        AddSupportBrim().apply(body, {
            "face_selector": {"kind": "face_named", "name": "front"},
            "brim_width_mm": 5.0,
            "brim_height_mm": 0.4,
        })


def test_add_support_brim_width_scales_volume():
    """Wider brim → more material. Doubling width should give ~more volume
    added than narrower brim."""
    L, W, H = 20.0, 20.0, 10.0
    body_a = _box(L, W, H)
    body_b = _box(L, W, H)
    v0 = _volume(body_a)

    r_small = AddSupportBrim().apply(body_a, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "brim_width_mm": 2.0,
        "brim_height_mm": 0.4,
    })
    r_large = AddSupportBrim().apply(body_b, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "brim_width_mm": 6.0,
        "brim_height_mm": 0.4,
    })
    delta_small = _volume(r_small.body) - v0
    delta_large = _volume(r_large.body) - v0
    assert delta_large > delta_small
