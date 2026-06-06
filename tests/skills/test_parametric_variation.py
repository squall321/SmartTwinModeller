"""Parametric variation skills — uniform scale, per-feature scale, absolute
overrides. The variation pipeline is pure (no OCCT calls), so the tests
operate on small synthetic catalogs and inspect the result dict directly.
"""
from __future__ import annotations

from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
    VaryFeatureCatalog,
    vary_catalog,
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
