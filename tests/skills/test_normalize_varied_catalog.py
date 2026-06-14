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
from phone_designer.skills.reverse_engineer.recover_design_relations import (
    recover_design_relations,
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


# ──────────────────────────────────────────────────────────────────────────────
# pass 5 — relation coherence (Pillar VARIANTS, 2026-06-14; opt-in, warn only)


def _counterbore_catalog(through_d: float = 4.0, cb_d: float = 8.0) -> dict:
    """One counterbore hole — through bore + larger seat (default ratio 2.0)."""
    return {
        "holes": [
            {
                "id": 0,
                "type": "counterbore",
                "axis_origin": [5.0, 5.0, 5.0],
                "axis_dir": [0.0, 0.0, -1.0],
                "entry_origin": [5.0, 5.0, 5.0],
                "entry_depth_mm": 4.0,
                "diameters_mm": [through_d, cb_d],
                "depth_mm": 4.0,
                "standard_match": None,
            },
        ],
        "pockets": [],
        "patterns": [],
        "symmetries": [],
        "initial_bbox_mm": [0.0, 0.0, 0.0, 10.0, 10.0, 5.0],
        "base_thickness_mm": 2.0,
    }


def test_relations_none_is_byte_identical_to_today():
    """The whole point of the opt-in: relations=None (and the no-relations-arg
    legacy call) produce a DEEP-EQUAL normalized catalog + identical warnings."""
    cat = _m3_hole_catalog()
    cat["patterns"] = _ring_pattern_catalog()["patterns"]
    varied, _ = vary_catalog_ex(cat, scale_factor=2.0)

    legacy_norm, legacy_warn = normalize_catalog(varied, scale_hint=2.0)
    none_norm, none_warn = normalize_catalog(
        varied, scale_hint=2.0, relations=None,
    )
    assert none_norm == legacy_norm
    assert none_warn == legacy_warn

    # also via an empty relations list (falsy ⇒ pass 5 skipped) — same result.
    empty_norm, empty_warn = normalize_catalog(
        varied, scale_hint=2.0, relations=[],
    )
    assert empty_norm == legacy_norm
    assert empty_warn == legacy_warn


def test_broken_counterbore_ratio_is_named_warn_only():
    """A counterbore relation recovered at ratio 2.0, then the through bore is
    edited so the ratio falls to 1.33 — pass 5 NAMES the violation by its
    member keys and never mutates the catalog."""
    cat = _counterbore_catalog(through_d=4.0, cb_d=8.0)
    relations = recover_design_relations(cat)
    assert len(relations) == 1 and relations[0]["kind"] == "counterbore"
    assert relations[0]["value"] == pytest.approx(2.0)

    # break the ratio: through 4 → 6 (cb still 8 ⇒ ratio 1.33).
    varied, _ = vary_catalog_ex(
        cat, absolute_overrides={"holes.0.diameters_mm.0": 6.0},
    )
    normalized, warnings = normalize_catalog(varied, relations=relations)

    cb_warns = [w for w in warnings if "counterbore" in w]
    assert len(cb_warns) == 1, warnings
    assert "holes.0.diameters_mm.0" in cb_warns[0]
    assert "holes.0.diameters_mm.1" in cb_warns[0]
    # WARN-ONLY: the edited diameters are returned verbatim.
    assert normalized["holes"][0]["diameters_mm"] == [6.0, 8.0]

    # WITHOUT relations the same break is silent (pass 5 never runs).
    _, warn_none = normalize_catalog(varied)
    assert [w for w in warn_none if "counterbore" in w] == []


def test_coherent_counterbore_edit_does_not_warn():
    """Driving BOTH members so the ratio is held (the apply_variant_drivers
    propagation result) leaves pass 5 silent."""
    cat = _counterbore_catalog(through_d=4.0, cb_d=8.0)
    relations = recover_design_relations(cat)
    # through 4→5 and cb 8→10 keeps ratio 2.0.
    varied, _ = vary_catalog_ex(cat, absolute_overrides={
        "holes.0.diameters_mm.0": 5.0,
        "holes.0.diameters_mm.1": 10.0,
    })
    _, warnings = normalize_catalog(varied, relations=relations)
    assert [w for w in warnings if "counterbore" in w] == []


def test_uniform_scale_holds_every_relation_no_pass5_warning():
    """A pure uniform scale holds ratio / equality / pitch exactly — pass 5
    adds ZERO warnings (the P6 acceptance criterion survives the opt-in)."""
    cat = _counterbore_catalog(through_d=4.0, cb_d=8.0)
    cat["patterns"] = _ring_pattern_catalog()["patterns"]
    relations = recover_design_relations(cat)
    assert relations  # at least the counterbore relation
    for scale in (0.5, 2.0):
        varied, _ = vary_catalog_ex(cat, scale_factor=scale)
        _, warnings = normalize_catalog(
            varied, scale_hint=scale, relations=relations,
        )
        assert warnings == [], (scale, warnings)


def test_broken_pattern_pitch_relation_warns():
    """A pattern_pitch relation whose declared pitch is edited (positions left
    on the old ring) is flagged by pass 5 — independent of pass 2's own
    position recompute (pass 2 also fires here; pass 5 names the RELATION)."""
    cat = _ring_pattern_catalog()
    relations = recover_design_relations(cat)
    pp = [r for r in relations if r["kind"] == "pattern_pitch"]
    assert pp, relations
    # edit the pitch radius only; positions stay on the Ø20 ring → incoherent.
    varied, _ = vary_catalog_ex(
        cat, per_feature_scale={"patterns.0.pitch_radius_mm": 1.5},
    )
    # measure pass 5 in ISOLATION on the still-stale positions (use the varied
    # catalog directly so pass 2's recompute does not pre-fix the positions).
    warnings: list[str] = []
    from phone_designer.skills.reverse_engineer.normalize_varied_catalog import (
        _check_relation_coherence,
    )
    _check_relation_coherence(varied, relations, warnings)
    rel_warns = [w for w in warnings if "pattern_pitch" in w]
    assert len(rel_warns) == 1, warnings
    assert "patterns.0" in rel_warns[0]


def test_relations_skill_arg_threads_through_wrapper():
    """The skill wrapper exposes the relations arg and surfaces pass-5
    warnings; without it (None) the broken ratio is silent."""
    cat = _counterbore_catalog(through_d=4.0, cb_d=8.0)
    relations = recover_design_relations(cat)
    varied, _ = vary_catalog_ex(
        cat, absolute_overrides={"holes.0.diameters_mm.0": 6.0},
    )

    res_with = NormalizeVariedCatalog().apply(None, {
        "catalog": varied, "relations": relations,
    })
    assert any(
        "counterbore" in w
        for w in res_with.extras["variation_warnings"]
    )

    res_without = NormalizeVariedCatalog().apply(None, {"catalog": varied})
    assert not any(
        "counterbore" in w
        for w in res_without.extras["variation_warnings"]
    )
