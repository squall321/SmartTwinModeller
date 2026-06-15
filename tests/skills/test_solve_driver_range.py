"""Contract tests for solve_driver_range (Pillar VARIANTS, phase-4 2026-06-15).

solve_driver_range estimates the FEASIBLE RANGE [min,max] of a named driver
WITHOUT brute-force rebuilding every value: an analytic envelope from the
catalog-space containment / min-wall pre-check (reused from
plan_from_scaled_catalog) bisects the valid<->invalid bound, then a few
generate_variant_family rebuild probes at the bounds confirm it. The
load-bearing guarantees pinned here:

  1. An envelope-governed driver (housing_length / wall / bore) gets a plausible
     analytic [min,max] bracketing the seed.
  2. The bound is a REAL constraint where a feature drops just past it
     (bounds_constrained) vs the search-span edge where growth is unbounded.
  3. Verify-by-rebuild probes ratify the analytic bound -> method 'probed'
     (valid inside, degraded/dropped just outside a constrained bound).
  4. HONEST FALLBACK -> feasible_range=None with a reason for:
       * a loft / sweep / revolve (freeform) base catalog,
       * a coupled role (pitch / pocket),
       * a non-key / freeform driver name.
     NEVER a faked range.

The analytic layer (probe=False) is OCCT-free and fast; the probed tests run a
real rebuild and are marked slow.
"""
from __future__ import annotations

import copy
import pathlib

import pytest

from phone_designer.skills.reverse_engineer.solve_driver_range import (
    SolveDriverRange,
    solve_driver_range,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _edge_hole_catalog() -> dict:
    """40x20x10 plate, one Ø6 hole whose axis sits at x=8 (envelope reaches
    x=11). Shrinking housing_length below ~12 violates containment / min-wall;
    growing is unbounded."""
    return {
        "initial_bbox_mm": [0.0, 0.0, 0.0, 40.0, 20.0, 10.0],
        "base_thickness_mm": 3.0,
        "holes": [{
            "id": 0, "type": "simple",
            "axis_origin": [8.0, 10.0, 10.0],
            "entry_origin": [8.0, 10.0, 10.0],
            "axis_dir": [0.0, 0.0, -1.0],
            "diameters_mm": [6.0], "depth_mm": 5.0, "entry_depth_mm": 5.0,
        }],
        "pockets": [], "bosses": [], "patterns": [], "symmetries": [],
    }


def _point_hole_near_max_catalog() -> dict:
    """40x20x10 plate, a small Ø0.6 hole near xmax (x=38.5). The envelope
    floor (~38.8) coincides with the box-mode emit floor, so a SHRINK probe
    just past the analytic min bound actually drops the feature — the case
    where rebuild probes RATIFY the analytic bound (method 'probed')."""
    return {
        "initial_bbox_mm": [0.0, 0.0, 0.0, 40.0, 20.0, 10.0],
        "base_thickness_mm": 3.0,
        "holes": [{
            "id": 0, "type": "simple",
            "axis_origin": [38.5, 10.0, 10.0],
            "entry_origin": [38.5, 10.0, 10.0],
            "axis_dir": [0.0, 0.0, -1.0],
            "diameters_mm": [0.6], "depth_mm": 5.0, "entry_depth_mm": 5.0,
        }],
        "pockets": [], "bosses": [], "patterns": [], "symmetries": [],
    }


# ──────────────────────────────────────────────────────────────────────────
# analytic layer (OCCT-free)
# ──────────────────────────────────────────────────────────────────────────
def test_envelope_driver_gets_plausible_analytic_range():
    cat = _edge_hole_catalog()
    r = solve_driver_range(cat, "housing_length", min_wall_mm=0.8)
    fr = r["feasible_range"]
    assert fr is not None
    assert r["method"] == "analytic"
    lo, hi = fr
    # the range brackets the seed (40) and the lower bound is a real
    # containment limit somewhere above the hole envelope (x=11).
    assert lo < r["seed_value"] <= hi
    assert lo > 11.0
    # lower bound is a genuine constraint; growing is the span edge.
    assert r["bounds_constrained"]["min"] is True
    assert r["bounds_constrained"]["max"] is False


def test_bore_and_wall_drivers_also_bound():
    cat = _edge_hole_catalog()
    rb = solve_driver_range(cat, "primary_bore_diameter", min_wall_mm=0.8)
    assert rb["feasible_range"] is not None
    assert rb["driver_role"] == "primary_bore"
    rw = solve_driver_range(cat, "wall_thickness", min_wall_mm=0.8)
    assert rw["feasible_range"] is not None
    assert rw["driver_role"] == "wall"


def test_tighter_min_wall_narrows_the_lower_bound():
    cat = _edge_hole_catalog()
    loose = solve_driver_range(cat, "housing_length", min_wall_mm=0.1)
    tight = solve_driver_range(cat, "housing_length", min_wall_mm=3.0)
    # a bigger wall floor pushes the lower feasible bound UP.
    assert tight["feasible_range"][0] >= loose["feasible_range"][0]


def test_input_catalog_not_mutated():
    cat = _edge_hole_catalog()
    snap = copy.deepcopy(cat)
    solve_driver_range(cat, "housing_length", min_wall_mm=0.8)
    assert cat == snap


# ──────────────────────────────────────────────────────────────────────────
# HONEST FALLBACK — feasible_range=None, never faked
# ──────────────────────────────────────────────────────────────────────────
def test_loft_base_driver_returns_none_honestly():
    cat = _edge_hole_catalog()
    cat["loft_features"] = [{"kind": "boss", "center_xy": [20.0, 10.0]}]
    r = solve_driver_range(cat, "housing_length", min_wall_mm=0.8)
    assert r["feasible_range"] is None
    assert r["method"] == "none"
    assert "loft_features" in r["reason"]


def test_sweep_and_revolve_bases_also_fall_back():
    for fam in ("sweep_features", "revolve_features"):
        cat = _edge_hole_catalog()
        cat[fam] = [{"kind": "x"}]
        r = solve_driver_range(cat, "housing_length")
        assert r["feasible_range"] is None
        assert r["method"] == "none"
        assert fam in r["reason"]


def test_pitch_role_is_coupled_and_returns_none():
    cat = _edge_hole_catalog()
    cat["patterns"] = [{
        "pattern_kind": "linear", "feature_kind": "hole",
        "direction": [1.0, 0.0, 0.0], "spacing_mm": 5.0, "count": 3,
        "positions": [[0, 0, 0], [5, 0, 0], [10, 0, 0]],
    }]
    r = solve_driver_range(cat, "hole_pattern_pitch", min_wall_mm=0.8)
    assert r["feasible_range"] is None
    assert r["method"] == "none"
    assert "coupled" in r["reason"]


def test_non_key_driver_returns_none():
    cat = _edge_hole_catalog()
    r = solve_driver_range(cat, "totally_made_up_driver")
    assert r["feasible_range"] is None
    assert r["method"] == "none"
    assert "not a key_dimensions name" in r["reason"]


def test_empty_catalog_returns_none():
    r = solve_driver_range({}, "housing_length")
    assert r["feasible_range"] is None
    assert r["method"] == "none"


# ──────────────────────────────────────────────────────────────────────────
# skill wrapper — analytic only (no probes, fast)
# ──────────────────────────────────────────────────────────────────────────
def test_skill_wrapper_analytic_only():
    cat = _edge_hole_catalog()
    res = SolveDriverRange().apply(None, {
        "catalog": cat, "driver": "housing_length",
        "min_wall_mm": 0.8, "probe": False,
    })
    dr = res.extras["driver_range"]
    assert dr["feasible_range"] is not None
    assert dr["method"] == "analytic"
    assert dr["probes"] == []


def test_skill_wrapper_unknown_arg_key_raises():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SolveDriverRange().apply(None, {
            "catalog": _edge_hole_catalog(),
            "driver": "housing_length",
            "min_wallmm": 0.8,  # typo
        })


def test_skill_wrapper_none_fallback_skips_probes():
    cat = _edge_hole_catalog()
    cat["loft_features"] = [{"kind": "boss"}]
    res = SolveDriverRange().apply(None, {
        "catalog": cat, "driver": "housing_length", "probe": True,
    })
    dr = res.extras["driver_range"]
    assert dr["feasible_range"] is None
    assert dr["probes"] == []  # no rebuilds attempted for a None range


# ──────────────────────────────────────────────────────────────────────────
# verify-by-rebuild probes (OCCT, slow) — the 'probed' confirmation
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.slow
def test_probes_ratify_constrained_bound_to_probed():
    """The point-hole-near-xmax case: valid just inside both bounds; the
    constrained MIN bound drops the feature just outside (ratified); the MAX
    bound is honestly the search-span edge. => method 'probed'."""
    from phone_designer.skills import export_manifest  # noqa: F401
    cat = _point_hole_near_max_catalog()
    res = SolveDriverRange().apply(None, {
        "catalog": cat, "driver": "housing_length",
        "min_wall_mm": 0.0, "probe": True, "probe_margin_frac": 0.1,
    })
    dr = res.extras["driver_range"]
    assert dr["feasible_range"] is not None
    assert dr["method"] == "probed"
    probes = {p["position"]: p for p in dr["probes"]}
    # valid just inside both bounds
    assert probes["inside_min"]["valid"] is True
    assert probes["inside_max"]["valid"] is True
    # the constrained min bound degrades just outside (feature dropped)
    assert probes["outside_min"]["confirmed"] is True
    assert (probes["outside_min"]["features_expected"]
            < probes["inside_min"]["features_expected"])
    # the max bound is the unbounded search-span edge (honest label)
    assert probes["outside_max"]["expected"] == "span_edge"


# ──────────────────────────────────────────────────────────────────────────
# real linkrods — analytic envelope + a loft-base honest None (corpus-gated)
# ──────────────────────────────────────────────────────────────────────────
_LINKRODS = _REPO_ROOT / "corpus" / "oem" / "complex" / "occt__linkrods.step"


def _linkrods_catalog():
    from phone_designer.skills import export_manifest  # noqa: F401
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    body = ImportStep().apply(None, {"path": str(_LINKRODS)}).body
    catalog = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    return body, catalog


@pytest.mark.requires_oem
@pytest.mark.slow
@pytest.mark.skipif(not _LINKRODS.exists(),
                    reason=f"corpus STEP not found: {_LINKRODS}")
def test_real_linkrods_housing_length_range_and_loft_fallback():
    """On occt__linkrods: housing_length gets a plausible range bracketing the
    seed, CONFIRMED by rebuild probes (method 'probed'); a synthetic
    loft_features stamp flips it to an honest feasible_range=None (the freeform
    fallback).

    min_wall_mm=0 (pure containment) because the linkrods pocket genuinely
    sits within ~0.05 mm of the part edge — a positive wall floor correctly
    reports the seed itself as min-wall-infeasible (an honest None, not a bug),
    so we bound the geometric containment range here. The sub-0.07 mm seed
    slack the solver measures absorbs the part's envelope-vs-bbox extraction
    noise without papering over a real violation.
    """
    body, catalog = _linkrods_catalog()
    if catalog.get("skipped"):
        pytest.skip(f"linkrods catalog skipped: {catalog.get('reason')}")

    res = SolveDriverRange().apply(body, {
        "catalog": catalog, "driver": "housing_length",
        "min_wall_mm": 0.0, "probe": True, "probe_margin_frac": 0.08,
    })
    dr = res.extras["driver_range"]
    assert dr["feasible_range"] is not None, dr.get("reason")
    lo, hi = dr["feasible_range"]
    assert lo < dr["seed_value"] <= hi
    # probes ratify the analytic bound on a REAL part.
    assert dr["method"] == "probed"
    probes = {p["position"]: p for p in dr["probes"]}
    assert probes["inside_min"]["valid"] is True
    assert probes["inside_max"]["valid"] is True
    # the constrained shrink bound drops a feature just outside.
    assert probes["outside_min"]["confirmed"] is True
    assert dr["bounds_constrained"]["min"] is True

    # Honest loft fallback: stamp a loft feature and the range must go None.
    loft_cat = copy.deepcopy(catalog)
    loft_cat["loft_features"] = [{"kind": "boss", "center_xy": [4.0, 3.0]}]
    res2 = SolveDriverRange().apply(body, {
        "catalog": loft_cat, "driver": "housing_length", "probe": False,
    })
    dr2 = res2.extras["driver_range"]
    assert dr2["feasible_range"] is None
    assert dr2["method"] == "none"
    assert "loft" in dr2["reason"]
