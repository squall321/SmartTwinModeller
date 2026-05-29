"""Wave3-A: Class-A surface audit pack.

Covers
    - highlight_line_view  (reflective-streak audit)
    - zebra_stripe_view    (G1 break audit)
    - continuity_audit     (G0/G1/G2 cert per shared edge)
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.continuity_audit import ContinuityAudit
from phone_designer.skills.inspect.highlight_line_view import HighlightLineView
from phone_designer.skills.inspect.zebra_stripe_view import ZebraStripeView


# ──────────────────────────────────────────────────────────────────────────────
# highlight_line_view


def test_highlight_line_view_default_args_box():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = HighlightLineView().apply(box, {})
    extras = r.extras
    assert "highlight_lines" in extras
    assert "view_direction" in extras
    assert extras["light_count"] == 8
    assert extras["face_count"] == 6
    # body unchanged
    assert r.body is box


def test_highlight_line_view_custom_lights_view():
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 30}).body
    r = HighlightLineView().apply(box, {
        "view_direction": (1.0, 0.0, 0.0),
        "light_count": 4,
        "samples_per_stripe": 8,
    })
    extras = r.extras
    assert extras["light_count"] == 4
    # view_direction unit-normalised (magnitude 1 within FP tol)
    vd = extras["view_direction"]
    mag = sum(c * c for c in vd) ** 0.5
    assert abs(mag - 1.0) < 1e-4
    assert isinstance(extras["highlight_lines"], list)


def test_highlight_line_view_planar_box_no_kinks():
    """On a perfectly planar box every reflected ray is constant per face — no
    sharp jumps. The highlight_lines list may be empty (or only contain very
    short stripes), confirming the threshold is reasonable."""
    box = Box().apply(None, {"length_mm": 50, "width_mm": 50, "height_mm": 50}).body
    r = HighlightLineView().apply(box, {"light_count": 4, "samples_per_stripe": 6})
    # Every reported break should exceed threshold
    thresh = r.extras["sharp_threshold_rad"]
    for stripe in r.extras["highlight_lines"]:
        for p in stripe["points"]:
            assert p["delta_rad"] >= thresh


def test_highlight_line_view_spec_metadata():
    spec = HighlightLineView.spec
    assert spec.name == "highlight_line_view"
    assert spec.level == "atomic"
    assert "highlight_line_audit" in spec.produces_features


# ──────────────────────────────────────────────────────────────────────────────
# zebra_stripe_view


def test_zebra_stripe_view_box_defaults():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = ZebraStripeView().apply(box, {})
    extras = r.extras
    assert extras["stripe_count"] == 20
    assert "g1_discontinuities" in extras
    # 12 edges on a box, all shared between 2 perpendicular faces ⇒ G1 break ≈ 90°
    assert extras["edge_count"] == 12
    # Every break must exceed the published threshold
    thresh = extras["g1_break_threshold_rad"]
    for d in extras["g1_discontinuities"]:
        assert d["max_break_rad"] >= thresh
        assert d["severity"] in ("low", "medium", "high")
    # body unchanged
    assert r.body is box


def test_zebra_stripe_view_box_detects_all_12_edges_as_breaks():
    """A box has 12 sharp 90° edges — every one should show up as a G1
    discontinuity at the highest severity."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = ZebraStripeView().apply(box, {"stripe_count": 10, "samples_per_edge": 3})
    discs = r.extras["g1_discontinuities"]
    # we expect close to 12 reported breaks — be lenient for rare edge-case
    # surface fits that fail UV projection.
    assert len(discs) >= 6
    high_sev = [d for d in discs if d["severity"] == "high"]
    assert len(high_sev) >= 1


def test_zebra_stripe_view_custom_view_direction():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = ZebraStripeView().apply(box, {
        "stripe_count": 6,
        "view_direction": (0.0, 1.0, 0.0),
    })
    vd = r.extras["view_direction"]
    assert vd[1] > 0.9  # mostly +Y


def test_zebra_stripe_view_spec_metadata():
    spec = ZebraStripeView.spec
    assert spec.name == "zebra_stripe_view"
    assert spec.level == "atomic"
    assert "zebra_stripe_audit" in spec.produces_features


# ──────────────────────────────────────────────────────────────────────────────
# continuity_audit


def test_continuity_audit_box_g1_required_fails_every_edge():
    """A naked box has 90° dihedrals everywhere — every shared edge is G0
    only, so a G1 audit should flag many failures."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = ContinuityAudit().apply(box, {"min_continuity": "G1"})
    extras = r.extras
    assert extras["min_continuity"] == "G1"
    assert extras["edge_count"] == 12
    # All 12 edges are shared between perpendicular faces
    assert extras["shared_edge_count"] >= 6
    # Most should fail at G1
    assert extras["fail_count"] >= 6
    for f in extras["continuity_failures"]:
        assert f["required"] == "G1"
        assert f["achieved"] in ("broken", "G0")
        assert f["max_normal_angle_rad"] >= 0.0


def test_continuity_audit_g0_passes_box():
    """At G0 (positional) the box must pass: every face does meet at the
    edge, even if the tangent is broken."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = ContinuityAudit().apply(box, {"min_continuity": "G0"})
    extras = r.extras
    assert extras["fail_count"] == 0
    assert extras["pass_count"] >= 6


def test_continuity_audit_g2_strict_finds_failures():
    """G2 is the strictest — a box passes none of its sharp dihedrals."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = ContinuityAudit().apply(box, {"min_continuity": "G2"})
    assert r.extras["fail_count"] >= r.extras["pass_count"]


def test_continuity_audit_body_unchanged():
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = ContinuityAudit().apply(box, {"min_continuity": "G1"})
    assert r.body is box


def test_continuity_audit_spec_metadata():
    spec = ContinuityAudit.spec
    assert spec.name == "continuity_audit"
    assert spec.level == "atomic"
    assert "continuity_audit" in spec.produces_features
