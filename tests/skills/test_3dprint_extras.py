"""PACK W1-A 3D printing extras — tests for raft_add, support_tree_path,
infill_region_tag, and slicer_hints.

Builds simple deterministic bodies (a plain box) and exercises:

  * modify_3dprint.raft_add
  * modify_3dprint.support_tree_path
  * modify_3dprint.infill_region_tag
  * inspect.slicer_hints
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.slicer_hints import SlicerHints
from phone_designer.skills.modify_3dprint.infill_region_tag import InfillRegionTag
from phone_designer.skills.modify_3dprint.raft_add import RaftAdd
from phone_designer.skills.modify_3dprint.support_tree_path import SupportTreePath


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
# raft_add


def test_raft_add_increases_volume():
    """Raft is fused under the box bottom → result volume > original."""
    L, W, H = 20.0, 20.0, 10.0
    body = _box(L, W, H)
    v0 = _volume(body)
    r = RaftAdd().apply(body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "raft_height_mm": 0.6,
        "raft_overhang_mm": 5.0,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    # Wide pad ~ (L+2*overhang)*(W+2*overhang)*height ≈ 30*30*0.6 = 540 mm³
    # Once fused under the existing box footprint the annular added volume
    # is closer to (outer_area - inner_area)*h = (900 - 400)*0.6 = 300 mm³.
    # The actual fused result must add at least the annular ring volume.
    assert (v1 - v0) >= 100.0


def test_raft_add_overhang_width_scales_volume():
    """Bigger raft_overhang_mm → more material added."""
    L, W, H = 20.0, 20.0, 10.0
    body_a = _box(L, W, H)
    body_b = _box(L, W, H)
    v0 = _volume(body_a)
    r_small = RaftAdd().apply(body_a, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "raft_height_mm": 0.6,
        "raft_overhang_mm": 3.0,
    })
    r_large = RaftAdd().apply(body_b, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "raft_height_mm": 0.6,
        "raft_overhang_mm": 8.0,
    })
    delta_small = _volume(r_small.body) - v0
    delta_large = _volume(r_large.body) - v0
    assert delta_large > delta_small


def test_raft_add_non_axial_face_raises():
    """A vertical side face is not +Z/-Z planar → v1 must raise."""
    body = _box(20, 20, 10)
    with pytest.raises((NotImplementedError, RuntimeError)):
        RaftAdd().apply(body, {
            "face_selector": {"kind": "face_named", "name": "front"},
            "raft_height_mm": 0.6,
            "raft_overhang_mm": 5.0,
        })


# ──────────────────────────────────────────────────────────────────────────────
# support_tree_path


def test_support_tree_path_drops_to_plate():
    """Each tip drops straight down along -build_direction to the build
    plate (= body min Z). For a 20x20x10 box with +Z build, the plate is at
    z=0; a tip at z=10 drops 10 mm."""
    body = _box(20, 20, 10)
    r = SupportTreePath().apply(body, {
        "overhang_points": [(0.0, 0.0, 10.0)],
        "build_direction": (0.0, 0.0, 1.0),
        "tree_branch_d_mm": 2.0,
    })
    rep = r.extras["support_tree"]
    assert "branches" in rep
    assert len(rep["branches"]) == 1
    br = rep["branches"][0]
    # Tip preserved.
    assert br["tip"] == [0.0, 0.0, 10.0]
    # Base sits on plate z=0.
    assert br["base"][2] == pytest.approx(0.0, abs=1e-3)
    assert br["length_mm"] == pytest.approx(10.0, abs=1e-3)
    # body unchanged.
    assert r.body is body


def test_support_tree_path_branches_merge():
    """Two tips at the same (x, y) at different heights drop to the same
    plate point — their bases coincide so they merge."""
    body = _box(20, 20, 10)
    r = SupportTreePath().apply(body, {
        "overhang_points": [
            (0.0, 0.0, 10.0),
            (0.1, 0.1, 8.0),     # very close in xy
        ],
        "build_direction": (0.0, 0.0, 1.0),
        "tree_branch_d_mm": 2.0,
        "merge_factor": 2.5,    # merge_dist = 5 mm
    })
    rep = r.extras["support_tree"]
    # Second branch merges into first.
    assert rep["merge_count"] == 1
    branches = {b["id"]: b for b in rep["branches"]}
    assert branches[1]["merges_into"] == 0
    assert 1 in branches[0]["children"]


def test_support_tree_path_far_tips_do_not_merge():
    """Tips far apart in xy land far apart on the plate — no merge."""
    body = _box(40, 40, 10)
    r = SupportTreePath().apply(body, {
        "overhang_points": [
            (-15.0, -15.0, 10.0),
            (+15.0, +15.0, 10.0),
        ],
        "build_direction": (0.0, 0.0, 1.0),
        "tree_branch_d_mm": 2.0,
        "merge_factor": 2.5,    # merge_dist = 5 mm
    })
    rep = r.extras["support_tree"]
    assert rep["merge_count"] == 0
    for br in rep["branches"]:
        assert br["merges_into"] is None


def test_support_tree_path_empty_points_raises():
    body = _box(10, 10, 10)
    with pytest.raises(ValueError):
        SupportTreePath().apply(body, {
            "overhang_points": [],
        })


def test_support_tree_path_zero_build_direction_raises():
    body = _box(10, 10, 10)
    with pytest.raises(ValueError):
        SupportTreePath().apply(body, {
            "overhang_points": [(0.0, 0.0, 5.0)],
            "build_direction": (0.0, 0.0, 0.0),
        })


# ──────────────────────────────────────────────────────────────────────────────
# infill_region_tag


def test_infill_region_tag_attaches_to_body():
    """Tag is written to body._pd_infill and reported in extras['infill_tag']."""
    body = _box(20, 20, 10)
    sel = {"kind": "face_named", "name": "top"}
    r = InfillRegionTag().apply(body, {
        "face_selector_or_body_region": sel,
        "infill_pct": 25.0,
        "pattern": "gyroid",
    })
    tag = r.extras["infill_tag"]
    assert tag["pct"] == 25.0
    assert tag["pattern"] == "gyroid"
    assert tag["region"]["kind"] == "face_named"
    # Tag attached to body.
    assert hasattr(r.body, "_pd_infill")
    # body itself unchanged (no volume change).
    assert r.body is body


def test_infill_region_tag_multiple_regions_accumulate():
    """Calling twice on the same body should accumulate distinct infill tags."""
    body = _box(20, 20, 10)
    InfillRegionTag().apply(body, {
        "face_selector_or_body_region": {"kind": "face_named", "name": "top"},
        "infill_pct": 20.0,
        "pattern": "grid",
    })
    InfillRegionTag().apply(body, {
        "face_selector_or_body_region": {"kind": "face_named", "name": "bottom"},
        "infill_pct": 40.0,
        "pattern": "honeycomb"})
    infill = body._pd_infill
    assert isinstance(infill, list)
    assert len(infill) == 2
    patterns = {entry["pattern"] for entry in infill}
    assert patterns == {"grid", "honeycomb"}


def test_infill_region_tag_pattern_validation():
    """Unknown pattern → pydantic validation error."""
    body = _box(10, 10, 10)
    with pytest.raises(Exception):
        InfillRegionTag().apply(body, {
            "face_selector_or_body_region": {"kind": "face_named", "name": "top"},
            "infill_pct": 20.0,
            "pattern": "spaghetti",   # not in allowed Literal
        })


def test_infill_region_tag_pct_validation():
    """pct > 100 should be rejected by pydantic."""
    body = _box(10, 10, 10)
    with pytest.raises(Exception):
        InfillRegionTag().apply(body, {
            "face_selector_or_body_region": {"kind": "face_named", "name": "top"},
            "infill_pct": 250.0,
            "pattern": "gyroid",
        })


# ──────────────────────────────────────────────────────────────────────────────
# slicer_hints


def test_slicer_hints_layer_count_matches_height():
    """build_height / layer_height rounded up → expected layer count."""
    L, W, H = 20.0, 20.0, 10.0
    body = _box(L, W, H)
    r = SlicerHints().apply(body, {
        "layer_height_mm": 0.2,
        "line_width_mm": 0.4,
        "build_direction": (0.0, 0.0, 1.0),
    })
    rep = r.extras["slicer_hints"]
    # H=10, layer_h=0.2 → 50 layers. OCCT bbox can pad by 1 layer's worth, so
    # accept 50 or 51.
    assert rep["estimated_layer_count"] in (50, 51)
    assert rep["build_height_mm"] == pytest.approx(H, abs=0.1)
    # body unchanged
    assert r.body is body


def test_slicer_hints_footprint_area_matches_bbox_cross_section():
    """+Z build → footprint area ≈ L * W for an axis-aligned box."""
    L, W, H = 25.0, 15.0, 8.0
    body = _box(L, W, H)
    r = SlicerHints().apply(body, {
        "layer_height_mm": 0.2,
        "line_width_mm": 0.4,
        "build_direction": (0.0, 0.0, 1.0),
    })
    rep = r.extras["slicer_hints"]
    assert rep["footprint_area_mm2"] == pytest.approx(L * W, abs=1e-3)
    # Longest-layer perimeter ≈ 2*(L+W).
    assert rep["longest_layer_perimeter_mm"] == pytest.approx(2.0 * (L + W),
                                                              abs=1e-3)


def test_slicer_hints_print_time_increases_with_volume():
    """Bigger body → more material → more print time."""
    small = SlicerHints().apply(_box(10, 10, 10), {
        "layer_height_mm": 0.2, "line_width_mm": 0.4,
        "build_direction": (0.0, 0.0, 1.0),
    }).extras["slicer_hints"]["estimated_print_time_min"]
    big = SlicerHints().apply(_box(20, 20, 20), {
        "layer_height_mm": 0.2, "line_width_mm": 0.4,
        "build_direction": (0.0, 0.0, 1.0),
    }).extras["slicer_hints"]["estimated_print_time_min"]
    assert big > small


def test_slicer_hints_different_build_direction_changes_layers():
    """Flipping the build axis from +Z to +X on a non-cube box changes the
    layer count (build_height changes)."""
    L, W, H = 25.0, 25.0, 10.0
    body = _box(L, W, H)
    rz = SlicerHints().apply(body, {
        "layer_height_mm": 0.2, "line_width_mm": 0.4,
        "build_direction": (0.0, 0.0, 1.0),
    }).extras["slicer_hints"]
    rx = SlicerHints().apply(body, {
        "layer_height_mm": 0.2, "line_width_mm": 0.4,
        "build_direction": (1.0, 0.0, 0.0),
    }).extras["slicer_hints"]
    # H=10 → 50 layers along Z; L=25 → 125 layers along X. OCCT bbox may
    # pad by one layer so we allow the next integer up.
    assert rz["estimated_layer_count"] in (50, 51)
    assert rx["estimated_layer_count"] in (125, 126)


def test_slicer_hints_zero_build_direction_raises():
    body = _box(10, 10, 10)
    with pytest.raises(ValueError):
        SlicerHints().apply(body, {
            "build_direction": (0.0, 0.0, 0.0),
        })
