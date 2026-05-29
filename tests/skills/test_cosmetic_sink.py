"""DFM pack — cosmetic side classify, sink-mark risk, class-A surface tag."""
from __future__ import annotations

import pytest

from phone_designer.skills.compose.class_a_surface_tag import (
    ClassASurfaceTag,
    get_class_a_tags,
    set_class_a_tags,
)
from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.cosmetic_side_classify import CosmeticSideClassify
from phone_designer.skills.inspect.inspect_sink_mark_risk import InspectSinkMarkRisk


# ──────────────────────────────────────────────────────────────────────────────
# cosmetic_side_classify


def test_cosmetic_side_classify_default_viewer_top_visible():
    """Default viewer (100,0,50) sees +X / +Z faces of a 20×20×10 box."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = CosmeticSideClassify().apply(box, {})
    assert "a_faces" in r.extras
    assert "b_faces" in r.extras
    # legacy alias retained
    assert r.extras["cosmetic_faces"] == r.extras["a_faces"]
    # 6 box faces — at least one is cosmetic (+X face visible from x=100)
    assert r.extras["cosmetic_report"]["face_count"] == 6
    assert len(r.extras["a_faces"]) >= 1
    assert len(r.extras["b_faces"]) >= 1
    # cosmetic + b should cover every face
    assert (
        len(r.extras["a_faces"]) + len(r.extras["b_faces"])
        == r.extras["cosmetic_report"]["face_count"]
    )
    # body unchanged
    assert r.body is box


def test_cosmetic_side_classify_explicit_viewer_top():
    """Viewer directly above the box → top face is cosmetic, bottom is not."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = CosmeticSideClassify().apply(box, {"viewer_position": (0.0, 0.0, 200.0)})
    details = {d["face_idx"]: d for d in r.extras["cosmetic_report"]["details"]}
    # at least one face is the top — center z ≈ 10 — and it should be cosmetic
    top_idx = None
    bottom_idx = None
    for d in details.values():
        if abs(d["centroid"][2] - 10.0) < 0.1:
            top_idx = d["face_idx"]
        elif abs(d["centroid"][2] - 0.0) < 0.1:
            bottom_idx = d["face_idx"]
    assert top_idx is not None
    assert details[top_idx]["is_cosmetic"] is True
    if bottom_idx is not None:
        assert details[bottom_idx]["is_cosmetic"] is False


def test_cosmetic_side_classify_spec_metadata():
    spec = CosmeticSideClassify.spec
    assert spec.name == "cosmetic_side_classify"
    assert spec.level == "atomic"
    assert "cosmetic_classification" in spec.produces_features


# ──────────────────────────────────────────────────────────────────────────────
# inspect_sink_mark_risk


def test_sink_mark_risk_thick_box_flags_high_thickness():
    """A 20×20×10 box with nominal_wall=1 mm → 10 mm walls register as
    >> 60% of nominal → at least one sink-mark candidate."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = InspectSinkMarkRisk().apply(box, {
        "nominal_wall_mm": 1.0,
        "material_shrinkage_pct": 0.6,
        "samples_per_face": 4,
    })
    assert "sink_mark_risks" in r.extras
    assert "sink_mark_summary" in r.extras
    s = r.extras["sink_mark_summary"]
    assert s["nominal_wall_mm"] == 1.0
    assert s["rib_to_wall_ratio_threshold"] == pytest.approx(0.6)
    assert s["risk_count"] >= 1
    # severities sum to total
    counts = s["severity_counts"]
    assert counts["low"] + counts["medium"] + counts["high"] == s["risk_count"]
    # Each risk has the required fields
    first = r.extras["sink_mark_risks"][0]
    assert {"location", "severity", "recommendation"} <= set(first.keys())
    assert first["severity"] in ("low", "medium", "high")
    # body unchanged
    assert r.body is box


def test_sink_mark_risk_no_risk_when_wall_already_thick():
    """If nominal_wall_mm is larger than the actual wall, ratio stays low and
    no risks are emitted (threshold = 0.6 * 100 mm = 60 mm, > any real wall)."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = InspectSinkMarkRisk().apply(box, {
        "nominal_wall_mm": 100.0,
        "material_shrinkage_pct": 0.6,
        "samples_per_face": 4,
    })
    assert r.extras["sink_mark_summary"]["risk_count"] == 0
    assert r.extras["sink_mark_risks"] == []


def test_sink_mark_risk_shrinkage_bumps_severity():
    """Higher shrinkage bumps severity bands up one level."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    low_shrink = InspectSinkMarkRisk().apply(box, {
        "nominal_wall_mm": 1.0, "material_shrinkage_pct": 0.6,
        "samples_per_face": 4,
    })
    high_shrink = InspectSinkMarkRisk().apply(box, {
        "nominal_wall_mm": 1.0, "material_shrinkage_pct": 2.0,
        "samples_per_face": 4,
    })
    # When risks exist on both, high-shrink must have >= as many high entries
    low_h = low_shrink.extras["sink_mark_summary"]["severity_counts"]["high"]
    high_h = high_shrink.extras["sink_mark_summary"]["severity_counts"]["high"]
    assert high_h >= low_h


def test_sink_mark_risk_spec_metadata():
    spec = InspectSinkMarkRisk.spec
    assert spec.name == "inspect_sink_mark_risk"
    assert spec.level == "atomic"
    assert "sink_mark_report" in spec.produces_features


# ──────────────────────────────────────────────────────────────────────────────
# class_a_surface_tag


def test_class_a_surface_tag_attaches_pd_class_a():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = ClassASurfaceTag().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
    })
    tags = get_class_a_tags(r.body)
    assert "class_a" in tags
    assert len(tags["class_a"]) == 1
    ref = tags["class_a"][0]
    assert ref.kind == "face"
    # top face z=10 center
    assert abs(ref.bbox_center[2] - 10.0) < 0.5
    # body unchanged in volume/topology
    assert r.body is box


def test_class_a_surface_tag_custom_label():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = ClassASurfaceTag().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "label": "front_cosmetic",
    })
    tags = get_class_a_tags(r.body)
    assert "front_cosmetic" in tags
    assert "class_a" not in tags


def test_class_a_surface_tag_no_match_raises():
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    with pytest.raises(RuntimeError, match="0 faces"):
        ClassASurfaceTag().apply(box, {
            "face_selector": {"kind": "face_named", "name": "nonexistent"},
        })


def test_class_a_helpers_get_set():
    """get_class_a_tags / set_class_a_tags helpers behave like the tag_face
    counterparts."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    # empty by default
    assert get_class_a_tags(box) == {}
    # set then get
    set_class_a_tags(box, {"foo": []})
    assert "foo" in get_class_a_tags(box)


def test_class_a_multiple_labels_coexist():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    a = ClassASurfaceTag().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "label": "A",
    }).body
    b = ClassASurfaceTag().apply(a, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "label": "B",
    }).body
    tags = get_class_a_tags(b)
    assert "A" in tags and "B" in tags


def test_class_a_surface_tag_spec_metadata():
    spec = ClassASurfaceTag.spec
    assert spec.name == "class_a_surface_tag"
    assert spec.level == "atomic"
    assert "class_a_attribute" in spec.produces_features
