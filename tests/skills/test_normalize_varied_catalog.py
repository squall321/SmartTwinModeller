"""normalize_varied_catalog (plan item P6) — derived-field recomputation on
varied catalogs. Pure dict work (no OCCT), so the tests use small synthetic
catalogs; the standard re-match pass reads the committed
catalogs/standards/threads_metric.yaml.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.reverse_engineer.normalize_varied_catalog import (
    NormalizeVariedCatalog,
    normalize_catalog,
)
from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
    vary_catalog_ex,
)


def _m3_hole_catalog() -> dict:
    """One simple hole that classify_holes would have matched as M3
    (3.4 mm close clearance, confidence ~0.95)."""
    return {
        "holes": [
            {
                "id": 0,
                "type": "simple",
                "axis_origin": [5.0, 5.0, 5.0],
                "axis_dir": [0.0, 0.0, -1.0],
                "entry_origin": [5.0, 5.0, 5.0],
                "entry_depth_mm": 4.0,
                "diameters_mm": [3.4],
                "depth_mm": 4.0,
                "standard_match": {
                    "thread_spec": "M3",
                    "fit": "close",
                    "confidence": 0.95,
                },
            },
        ],
        "pockets": [],
        "patterns": [],
        "symmetries": [],
        "initial_bbox_mm": [0.0, 0.0, 0.0, 10.0, 10.0, 5.0],
        "base_thickness_mm": 2.0,
    }


def _ring_pattern_catalog() -> dict:
    """Circular pattern: 4 members on a Ø20 ring about the origin."""
    return {
        "holes": [],
        "pockets": [],
        "patterns": [
            {
                "pattern_kind": "circular",
                "feature_kind": "hole",
                "center": [0.0, 0.0, 0.0],
                "axis": [0.0, 0.0, 1.0],
                "pitch_radius_mm": 10.0,
                "count": 4,
                "positions": [
                    [10.0, 0.0, 0.0],
                    [0.0, 10.0, 0.0],
                    [-10.0, 0.0, 0.0],
                    [0.0, -10.0, 0.0],
                ],
            },
        ],
        "symmetries": [],
        "initial_bbox_mm": [-15.0, -15.0, -2.0, 15.0, 15.0, 2.0],
    }


# ──────────────────────────────────────────────────────────────────────────────
# pass 1 — standard re-match


def test_m3_match_does_not_survive_a_2x_scale():
    """An M3-matched hole scaled 2× (Ø3.4 → Ø6.8) must NOT still say M3 —
    either it re-matches another spec (M6-family) or goes None."""
    varied, _ = vary_catalog_ex(_m3_hole_catalog(), scale_factor=2.0)
    assert varied["holes"][0]["diameters_mm"] == [6.8]
    # The stale match is still there before normalization …
    assert varied["holes"][0]["standard_match"]["thread_spec"] == "M3"

    normalized, warnings = normalize_catalog(varied, scale_hint=2.0)
    sm = normalized["holes"][0]["standard_match"]
    if sm is not None:
        assert sm["thread_spec"] != "M3", sm
        assert float(sm["confidence"]) >= 0.6, sm
    # Re-matching is normalization, not a violation — no warnings for it.
    assert warnings == []
    # Input not mutated (deep copy contract).
    assert varied["holes"][0]["standard_match"]["thread_spec"] == "M3"


def test_low_confidence_rematch_is_nulled():
    """A diameter far from every standard (after an odd absolute override)
    must null the stale match, never keep it. Ø20 is 8 mm from the largest
    metric fit value (M10 coarse 12.0) → confidence ≈ 0.11 < 0.6 → None."""
    cat = _m3_hole_catalog()
    varied, _ = vary_catalog_ex(
        cat, absolute_overrides={"holes.0.diameters_mm.0": 20.0},
    )
    normalized, _ = normalize_catalog(varied, scale_hint=None)
    assert normalized["holes"][0]["standard_match"] is None


def test_unmatched_hole_stays_none_without_scale_hint():
    cat = _m3_hole_catalog()
    cat["holes"][0]["standard_match"] = None
    normalized, warnings = normalize_catalog(cat, scale_hint=None)
    assert normalized["holes"][0]["standard_match"] is None
    assert warnings == []


# ──────────────────────────────────────────────────────────────────────────────
# pass 2 — pattern coherence


def test_pitch_radius_edit_recomputes_positions_exactly():
    """pitch_radius_mm ×1.5 (positions untouched by the dotted key) →
    normalize recomputes every member position analytically at the new
    radius, preserving angles."""
    varied, _ = vary_catalog_ex(
        _ring_pattern_catalog(),
        per_feature_scale={"patterns.0.pitch_radius_mm": 1.5},
    )
    assert varied["patterns"][0]["pitch_radius_mm"] == 15.0
    # positions still on the old Ø20 ring → incoherent by 50 %.
    assert varied["patterns"][0]["positions"][0] == [10.0, 0.0, 0.0]

    normalized, warnings = normalize_catalog(varied)
    new_pos = normalized["patterns"][0]["positions"]
    assert new_pos[0] == pytest.approx([15.0, 0.0, 0.0])
    assert new_pos[1] == pytest.approx([0.0, 15.0, 0.0])
    assert new_pos[2] == pytest.approx([-15.0, 0.0, 0.0])
    assert new_pos[3] == pytest.approx([0.0, -15.0, 0.0])
    # every member back on the declared pitch circle
    for p in new_pos:
        assert math.hypot(p[0], p[1]) == pytest.approx(15.0)
    assert len(warnings) == 1
    assert "patterns.0" in warnings[0]


def test_linear_spacing_edit_recomputes_positions():
    cat = {
        "patterns": [{
            "pattern_kind": "linear",
            "feature_kind": "hole",
            "direction": [1.0, 0.0, 0.0],
            "spacing_mm": 5.0,
            "positions": [[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [8.0, 0.0, 0.0]],
        }],
    }
    normalized, warnings = normalize_catalog(cat)
    pos = normalized["patterns"][0]["positions"]
    assert pos == [
        pytest.approx([0.0, 0.0, 0.0]),
        pytest.approx([5.0, 0.0, 0.0]),
        pytest.approx([10.0, 0.0, 0.0]),
    ]
    assert len(warnings) == 1


def test_coherent_pattern_is_left_alone():
    cat = _ring_pattern_catalog()
    normalized, warnings = normalize_catalog(cat)
    assert normalized["patterns"][0]["positions"] == cat["patterns"][0]["positions"]
    assert warnings == []


# ──────────────────────────────────────────────────────────────────────────────
# pass 3 — counterbore invariants (warn, never mutate)


def test_counterbore_invariant_violations_warn_without_mutation():
    cat = {
        "holes": [
            {   # multi-diameter hole collapsed to equal diameters
                "id": 0, "type": "counterbore",
                "diameters_mm": [6.0, 6.0],
                "standard_match": None,
            },
            {   # counterbore type with a single diameter
                "id": 1, "type": "counterbore",
                "diameters_mm": [8.0],
                "standard_match": None,
            },
        ],
    }
    normalized, warnings = normalize_catalog(cat)
    # never mutated
    assert normalized["holes"][0]["diameters_mm"] == [6.0, 6.0]
    assert normalized["holes"][1]["diameters_mm"] == [8.0]
    joined = "\n".join(warnings)
    assert "holes.0" in joined and "max(diameters)" in joined
    assert "holes.1" in joined and "distinct" in joined
    # hole 0 also fails the cb-implies-2-distinct rule → 3 warnings total
    assert len(warnings) == 3


# ──────────────────────────────────────────────────────────────────────────────
# pass 4 — mirror coherence


def test_mirror_plane_outside_scaled_bbox_warns():
    cat = {
        "symmetries": [
            {"plane_origin": [50.0, 0.0, 0.0], "plane_normal": [1.0, 0.0, 0.0],
             "symmetry_score": 0.9},
        ],
        "initial_bbox_mm": [-10.0, -10.0, 0.0, 10.0, 10.0, 5.0],
    }
    _, warnings = normalize_catalog(cat)
    assert len(warnings) == 1
    assert "symmetries.0" in warnings[0]


def test_mirror_plane_inside_bbox_is_silent():
    cat = {
        "symmetries": [
            {"plane_origin": [0.0, 0.0, 2.5], "plane_normal": [1.0, 0.0, 0.0],
             "symmetry_score": 0.9},
        ],
        "initial_bbox_mm": [-10.0, -10.0, 0.0, 10.0, 10.0, 5.0],
    }
    _, warnings = normalize_catalog(cat)
    assert warnings == []


# ──────────────────────────────────────────────────────────────────────────────
# P6 acceptance: pure uniform scale ⇒ zero warnings


@pytest.mark.parametrize("scale", [0.5, 2.0])
def test_pure_uniform_scale_produces_zero_warnings(scale):
    cat = _m3_hole_catalog()
    cat["patterns"] = _ring_pattern_catalog()["patterns"]
    cat["symmetries"] = [
        {"plane_origin": [5.0, 5.0, 2.5], "plane_normal": [1.0, 0.0, 0.0],
         "symmetry_score": 0.8},
    ]
    varied, _ = vary_catalog_ex(cat, scale_factor=scale)
    _, warnings = normalize_catalog(varied, scale_hint=scale)
    assert warnings == [], warnings


# ──────────────────────────────────────────────────────────────────────────────
# skill wrapper


def test_skill_wrapper_extras_shape():
    varied, _ = vary_catalog_ex(_m3_hole_catalog(), scale_factor=2.0)
    res = NormalizeVariedCatalog().apply(None, {
        "catalog": varied,
        "scale_hint": 2.0,
    })
    assert "normalized_catalog" in res.extras
    assert isinstance(res.extras["variation_warnings"], list)
    sm = res.extras["normalized_catalog"]["holes"][0]["standard_match"]
    assert sm is None or sm["thread_spec"] != "M3"
    # read-only: input catalog untouched
    assert varied["holes"][0]["standard_match"]["thread_spec"] == "M3"
