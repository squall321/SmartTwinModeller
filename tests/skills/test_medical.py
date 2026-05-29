"""Tests for medical-device skills:
   membrane_keypad_recess (pocket), cleanability_radius_enforce (finish),
   biocompat_region_tag (compose), captive_screw_tether_pocket (pocket).

Each skill is exercised on a simple box body. We verify:
  - the operation has the expected effect on volume / face-count / tags,
  - manifest-level spec attributes (category, post_conditions).
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.compose.biocompat_region_tag import (
    BiocompatRegionTag,
    get_biocompat_tags,
)
from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_finish.cleanability_radius_enforce import (
    CleanabilityRadiusEnforce,
)
from phone_designer.skills.modify_pocket.captive_screw_tether_pocket import (
    CaptiveScrewTetherPocket,
)
from phone_designer.skills.modify_pocket.membrane_keypad_recess import (
    MembraneKeypadRecess,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _face_count(body) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopTools import TopTools_MapOfShape
    seen = TopTools_MapOfShape()
    count = 0
    it = TopExp_Explorer(body.wrapped, TopAbs_FACE)
    while it.More():
        f = it.Current()
        if not seen.Contains(f):
            seen.Add(f)
            count += 1
        it.Next()
    return count


def _make_box(L: float = 40.0, W: float = 40.0, H: float = 10.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ── membrane_keypad_recess ──────────────────────────────────────────────────


def test_membrane_keypad_recess_removes_shallow_volume():
    box = _make_box(60, 40, 6)
    v0 = _volume(box)
    r = MembraneKeypadRecess().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "length_mm": 40.0,
        "width_mm": 24.0,
        "recess_d_mm": 0.25,
        "corner_r_mm": 2.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0, "membrane keypad recess must remove material"
    actual = v0 - v1
    # rounded-rect area ≈ L*W - (4 - π) * r²
    area = 40.0 * 24.0 - (4.0 - math.pi) * 2.0 * 2.0
    expected = area * 0.25
    assert 0.6 * expected < actual < 1.5 * expected, (
        f"recess removal: expected ≈ {expected:.3f}, got {actual:.3f}"
    )


def test_membrane_keypad_recess_zero_corner_radius_uses_box():
    box = _make_box(40, 30, 6)
    v0 = _volume(box)
    r = MembraneKeypadRecess().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "length_mm": 20.0,
        "width_mm": 15.0,
        "recess_d_mm": 0.3,
        "corner_r_mm": 0.0,
    })
    v1 = _volume(r.body)
    actual = v0 - v1
    expected = 20.0 * 15.0 * 0.3
    assert 0.9 * expected < actual < 1.2 * expected, (
        f"box-recess removal: expected ≈ {expected:.3f}, got {actual:.3f}"
    )


def test_membrane_keypad_recess_rejects_oversize_corner_radius():
    box = _make_box(40, 30, 6)
    with pytest.raises(ValueError, match="corner_r_mm"):
        MembraneKeypadRecess().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "length_mm": 4.0,
            "width_mm": 4.0,
            "recess_d_mm": 0.25,
            "corner_r_mm": 2.0,
        })


def test_membrane_keypad_recess_spec_metadata():
    spec = MembraneKeypadRecess.spec
    assert spec.name == "membrane_keypad_recess"
    assert spec.category == "modify/pocket"
    assert spec.level == "atomic"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── cleanability_radius_enforce ─────────────────────────────────────────────


def test_cleanability_radius_enforce_on_plain_box_is_noop():
    """A plain solid box has only convex edges → nothing to fillet, allow_no_change."""
    box = _make_box(20, 20, 10)
    fc0 = _face_count(box)
    r = CleanabilityRadiusEnforce().apply(box, {"min_internal_r_mm": 1.0})
    fc1 = _face_count(r.body)
    # No concave edges → no change.
    assert fc1 == fc0
    assert r.extras["concave_edges_found"] == 0


def test_cleanability_radius_enforce_fillets_interior_step_corner():
    """Cut a shallow rectangular pocket → the floor↔wall edges are concave.
    cleanability_radius_enforce should fillet them, increasing face count.
    """
    box = _make_box(40, 40, 8)
    r1 = MembraneKeypadRecess().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "length_mm": 28.0,
        "width_mm": 28.0,
        "recess_d_mm": 1.5,
        "corner_r_mm": 0.0,   # sharp interior — needs cleanability fillet
    })
    fc_before = _face_count(r1.body)
    r2 = CleanabilityRadiusEnforce().apply(r1.body, {"min_internal_r_mm": 0.5})
    fc_after = _face_count(r2.body)
    # At least one concave edge must have been found.
    assert r2.extras["concave_edges_found"] >= 1
    # And face count must change (fillet adds new faces).
    assert fc_after > fc_before


def test_cleanability_radius_enforce_rejects_out_of_range_radius():
    inst = CleanabilityRadiusEnforce()
    with pytest.raises(Exception):  # pydantic ValidationError
        inst.apply(None, {"min_internal_r_mm": 100.0})


def test_cleanability_radius_enforce_spec_metadata():
    spec = CleanabilityRadiusEnforce.spec
    assert spec.name == "cleanability_radius_enforce"
    assert spec.category == "modify/finish"
    assert spec.level == "atomic"
    assert any(pc.kind == "face_count_changed" for pc in spec.post_conditions)


# ── biocompat_region_tag ────────────────────────────────────────────────────


def test_biocompat_region_tag_attaches_attribute_default_usp_vi():
    box = _make_box(20, 20, 10)
    v0 = _volume(box)
    r = BiocompatRegionTag().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
    })
    # Geometry unchanged.
    assert _volume(r.body) == pytest.approx(v0, abs=1e-6)
    tags = get_biocompat_tags(r.body)
    assert "USP_VI" in tags
    assert len(tags["USP_VI"]) >= 1
    # The recorded face center should be on the top of the box (z ≈ 10).
    center = tags["USP_VI"][0].bbox_center
    assert abs(center[2] - 10.0) < 0.5


def test_biocompat_region_tag_multiple_classes_coexist():
    box = _make_box(20, 20, 10)
    r1 = BiocompatRegionTag().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "biocompat_class": "USP_VI",
    })
    r2 = BiocompatRegionTag().apply(r1.body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "biocompat_class": "ISO10993_5",
    })
    tags = get_biocompat_tags(r2.body)
    assert "USP_VI" in tags
    assert "ISO10993_5" in tags


def test_biocompat_region_tag_rejects_no_match():
    box = _make_box(20, 20, 10)
    with pytest.raises(RuntimeError, match="matched 0"):
        BiocompatRegionTag().apply(box, {
            "face_selector": {
                "kind": "faces_by_area",
                "min": 1e9, "max": 1e10,
            },
        })


def test_biocompat_region_tag_spec_metadata():
    spec = BiocompatRegionTag.spec
    assert spec.name == "biocompat_region_tag"
    assert spec.category == "compose"
    assert spec.level == "atomic"
    assert any(pc.kind == "body_present" for pc in spec.post_conditions)


# ── captive_screw_tether_pocket ─────────────────────────────────────────────


def test_captive_screw_tether_pocket_removes_volume_with_tether_hole():
    box = _make_box(40, 40, 10)
    v0 = _volume(box)
    r = CaptiveScrewTetherPocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "thread_spec": "M3",
        "tether_clearance_d_mm": 5.5,
        "tether_d_mm": 1.5,
        "clearance_depth_mm": 5.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0, "captive screw tether pocket must remove material"


def test_captive_screw_tether_pocket_rejects_too_small_tether_clearance():
    box = _make_box(40, 40, 10)
    # M3 medium clearance ≈ 3.4 mm; with tether_d=2.0, tether_clearance must
    # exceed 3.4 + 2.0 = 5.4. Use 4.5 → should reject.
    with pytest.raises(ValueError, match="tether_clearance_d_mm"):
        CaptiveScrewTetherPocket().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "thread_spec": "M3",
            "tether_clearance_d_mm": 4.5,
            "tether_d_mm": 2.0,
        })


def test_captive_screw_tether_pocket_unknown_thread_spec_raises():
    box = _make_box(40, 40, 10)
    with pytest.raises(ValueError, match="unknown thread_spec"):
        CaptiveScrewTetherPocket().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": (0.0, 0.0),
            "thread_spec": "M999",
        })


def test_captive_screw_tether_pocket_spec_metadata():
    spec = CaptiveScrewTetherPocket.spec
    assert spec.name == "captive_screw_tether_pocket"
    assert spec.category == "modify/pocket"
    assert spec.level == "atomic"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)
