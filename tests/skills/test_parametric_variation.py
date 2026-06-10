"""Parametric variation skills — uniform scale, per-feature scale, absolute
overrides. The variation pipeline is pure (no OCCT calls), so the tests
operate on small synthetic catalogs and inspect the result dict directly.

P7/P8 additions (2026-06-10): strict override resolution, extra='forbid'
Args, preserve_wall_thickness, and the catalog-space containment/min-wall
pre-check on plan_from_scaled_catalog.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from phone_designer.skills.reverse_engineer.plan_from_scaled_catalog import (
    PlanFromScaledCatalog,
)
from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
    VaryFeatureCatalog,
    vary_catalog,
    vary_catalog_ex,
)


def _one_pocket_catalog() -> dict:
    """Minimal feature_catalog with a single pocket — depth 5 mm, top_d 10 mm."""
    return {
        "pockets": [
            {
                "id": 0,
                "depth_mm": 5.0,
                "top_d_mm": 10.0,
                "axis_origin": [0.0, 0.0, 0.0],
                "axis_dir": [0.0, 0.0, -1.0],
            },
        ],
        "holes": [],
        "bosses": [],
    }


def test_uniform_scale_doubles_pocket_dims():
    """scale=2.0 ⇒ pocket depth 5→10, top_d 10→20."""
    cat = _one_pocket_catalog()
    res = VaryFeatureCatalog().apply(None, {
        "catalog": cat,
        "scale_factor": 2.0,
    })
    varied = res.extras["varied_catalog"]
    pocket = varied["pockets"][0]
    assert pocket["depth_mm"] == 10.0
    assert pocket["top_d_mm"] == 20.0
    # Input catalog must NOT have been mutated (deep copy).
    assert cat["pockets"][0]["depth_mm"] == 5.0
    assert cat["pockets"][0]["top_d_mm"] == 10.0


def test_per_feature_scale_overrides_targeted_field():
    """scale=1.0 + per_feature_scale={'pockets.0.depth_mm': 3.0} ⇒ depth 5→15."""
    cat = _one_pocket_catalog()
    res = VaryFeatureCatalog().apply(None, {
        "catalog": cat,
        "scale_factor": 1.0,
        "per_feature_scale": {"pockets.0.depth_mm": 3.0},
    })
    varied = res.extras["varied_catalog"]
    pocket = varied["pockets"][0]
    assert pocket["depth_mm"] == 15.0
    # top_d untouched by the per-feature override.
    assert pocket["top_d_mm"] == 10.0


def test_absolute_override_beats_scale():
    """absolute_overrides win over scale_factor and per_feature_scale."""
    cat = _one_pocket_catalog()
    # Apply a 2× uniform scale (would push top_d to 20) AND a per-feature 4×
    # multiplier (would push it to 40), then an absolute override to 7.5.
    res = vary_catalog(
        cat,
        scale_factor=2.0,
        per_feature_scale={"pockets.0.top_d_mm": 4.0},
        absolute_overrides={"pockets.0.top_d_mm": 7.5},
    )
    assert res["pockets"][0]["top_d_mm"] == 7.5
    # depth is only affected by the uniform 2× scale.
    assert res["pockets"][0]["depth_mm"] == 10.0


# ──────────────────────────────────────────────────────────────────────────────
# P8 — no more silent override swallowing


def test_typoed_dotted_key_is_reported_not_swallowed():
    """A typo'd dotted key ('diamter') must surface in unresolved_overrides
    — the pre-P8 behaviour returned a bit-identical catalog with no signal."""
    cat = _one_pocket_catalog()
    varied, warnings = vary_catalog_ex(
        cat,
        per_feature_scale={"pockets.0.diamter_mm": 2.0},   # typo: diamter
        absolute_overrides={"holes.5.depth_mm": 9.0},       # stale index
    )
    assert sorted(warnings["unresolved_overrides"]) == [
        "holes.5.depth_mm", "pockets.0.diamter_mm",
    ]
    # the variation itself is a no-op (nothing resolved)
    assert varied["pockets"][0] == cat["pockets"][0]

    res = VaryFeatureCatalog().apply(None, {
        "catalog": cat,
        "per_feature_scale": {"pockets.0.diamter_mm": 2.0},
    })
    assert res.extras["unresolved_overrides"] == ["pockets.0.diamter_mm"]


def test_strict_mode_raises_on_unresolved_override():
    with pytest.raises(ValueError, match="diamter_mm"):
        VaryFeatureCatalog().apply(None, {
            "catalog": _one_pocket_catalog(),
            "per_feature_scale": {"pockets.0.diamter_mm": 2.0},
            "strict": True,
        })


def test_unknown_args_key_raises_validation_error():
    """extra='forbid' on both Args models — unknown keys are typos, not
    silently-dropped extras."""
    with pytest.raises(ValidationError):
        VaryFeatureCatalog().apply(None, {
            "catalog": _one_pocket_catalog(),
            "scale_factorr": 2.0,   # typo
        })
    with pytest.raises(ValidationError):
        PlanFromScaledCatalog().apply(None, {
            "catalog": _one_pocket_catalog(),
            "per_feature_scales": {},   # typo
        })


# ──────────────────────────────────────────────────────────────────────────────
# P7 — constraints (preserve_wall_thickness / containment / min-wall)


def _edge_hole_catalog() -> dict:
    """Synthetic 20×20×10 plate with one Ø0.6 hole whose axis sits 1 mm
    from the x-min face — the canonical P7 shrink-violation case."""
    return {
        "holes": [{
            "id": 0,
            "type": "simple",
            "axis_origin": [1.0, 10.0, 10.0],
            "axis_dir": [0.0, 0.0, -1.0],
            "entry_origin": [1.0, 10.0, 10.0],
            "entry_depth_mm": 5.0,
            "diameters_mm": [0.6],
            "depth_mm": 5.0,
            "standard_match": None,
        }],
        "pockets": [],
        "bosses": [],
        "patterns": [],
        "symmetries": [],
        "initial_bbox_mm": [0.0, 0.0, 0.0, 20.0, 20.0, 10.0],
        "base_thickness_mm": 2.0,
    }


def test_preserve_wall_thickness_excludes_base_thickness_from_scale():
    """2× scale with preserve_wall_thickness: initial_bbox_mm doubles while
    base_thickness_mm stays put."""
    res = PlanFromScaledCatalog().apply(None, {
        "catalog": _edge_hole_catalog(),
        "scale_factor": 2.0,
        "constraints": {"preserve_wall_thickness": True},
    })
    varied = res.extras["varied_catalog"]
    assert varied["base_thickness_mm"] == 2.0           # NOT scaled
    assert varied["initial_bbox_mm"] == [0.0, 0.0, 0.0, 40.0, 40.0, 20.0]
    assert varied["holes"][0]["diameters_mm"] == [1.2]  # everything else scales
    # without the constraint the same scale doubles the thickness
    res2 = PlanFromScaledCatalog().apply(None, {
        "catalog": _edge_hole_catalog(),
        "scale_factor": 2.0,
    })
    assert res2.extras["varied_catalog"]["base_thickness_mm"] == 4.0


def test_containment_fail_raises_for_min_wall_violation_at_half_scale():
    """0.5×: the hole axis lands 0.5 mm from the x-min face; with r=0.15 the
    remaining wall is 0.35 mm < 0.8 mm floor → policy 'fail' raises."""
    with pytest.raises(ValueError, match="min_wall|containment"):
        PlanFromScaledCatalog().apply(None, {
            "catalog": _edge_hole_catalog(),
            "scale_factor": 0.5,
            "constraints": {"min_wall_mm": 0.8, "containment": "fail"},
        })


def test_containment_clamp_pulls_hole_inward_and_records():
    res = PlanFromScaledCatalog().apply(None, {
        "catalog": _edge_hole_catalog(),
        "scale_factor": 0.5,
        "constraints": {"min_wall_mm": 0.8, "containment": "clamp"},
    })
    violations = res.extras["constraint_violations"]
    assert len(violations) == 1
    v = violations[0]
    assert v["feature"] == "holes.0"
    assert v["action"] == "clamped"
    # wall needed: (xmin + 0.8) - (0.5 - 0.15) = 0.45 mm inward shift on x
    assert v["clamp"]["shift_mm"][0] == pytest.approx(0.45)

    hole = res.extras["varied_catalog"]["holes"][0]
    assert hole["entry_origin"][0] == pytest.approx(0.95)
    assert hole["axis_origin"][0] == pytest.approx(0.95)
    # post-clamp wall: (0.95 - 0.15) - 0.0 == exactly the 0.8 floor
    assert hole["entry_origin"][0] - 0.15 == pytest.approx(0.8)
    # y / z untouched
    assert hole["entry_origin"][1:] == pytest.approx([5.0, 5.0])


def test_containment_warn_records_without_mutation():
    res = PlanFromScaledCatalog().apply(None, {
        "catalog": _edge_hole_catalog(),
        "scale_factor": 0.5,
        "constraints": {"min_wall_mm": 0.8, "containment": "warn"},
    })
    violations = res.extras["constraint_violations"]
    assert len(violations) == 1
    assert violations[0]["action"] == "recorded"
    hole = res.extras["varied_catalog"]["holes"][0]
    assert hole["entry_origin"][0] == pytest.approx(0.5)  # untouched


def test_unknown_constraint_key_raises():
    with pytest.raises(ValueError, match="preserve_wall"):
        PlanFromScaledCatalog().apply(None, {
            "catalog": _edge_hole_catalog(),
            "scale_factor": 2.0,
            "constraints": {"preserve_wall_thicknes": True},   # typo
        })
    with pytest.raises(ValueError, match="containment"):
        PlanFromScaledCatalog().apply(None, {
            "catalog": _edge_hole_catalog(),
            "constraints": {"containment": "explode"},
        })


# ──────────────────────────────────────────────────────────────────────────────
# P6 wiring — plan_from_scaled_catalog surfaces normalization results


def test_plan_from_scaled_catalog_surfaces_p6_p8_extras():
    res = PlanFromScaledCatalog().apply(None, {
        "catalog": _edge_hole_catalog(),
        "scale_factor": 2.0,
        "per_feature_scale": {"holes.0.diamter_mm": 1.5},   # typo on purpose
    })
    assert res.extras["unresolved_overrides"] == ["holes.0.diamter_mm"]
    assert isinstance(res.extras["variation_warnings"], list)
    assert res.extras["constraint_violations"] == []
    assert "normalized_catalog" in res.extras
    assert res.extras["generated_plan"] is not None


def test_normalize_opt_out_passes_raw_varied_catalog():
    res = PlanFromScaledCatalog().apply(None, {
        "catalog": _edge_hole_catalog(),
        "scale_factor": 2.0,
        "normalize": False,
    })
    assert res.extras["normalized_catalog"] == res.extras["varied_catalog"]
    assert res.extras["variation_warnings"] == []
