"""cost_min_variant_search — sweep a driver, price each rebuild, pick the
cheapest GENUINELY-VIABLE variant (Pillar VARIANTS, A2).

Two layers:
  * fast unit tests (arg validation, helper mapping, no OCCT) — run everywhere;
  * integration tests on a generated solid box (genuine-winner path) + the
    occt__linkrods corpus part (the airtight all-marginal gate), the latter
    skipped when the corpus STEP is absent.

The CENTRAL property pinned here: COMPETITION requires the strict
recommendation['viability'] == 'viable' tier. A cheaper variant whose best
process is only 'viable_marginal'/'unproven' (the thinnest/smallest geometry is
EXACTLY the DFM-marginal one) is recorded but CANNOT win. The skill never
invents a winner, never silently truncates the sweep, and is read-only.
"""
from __future__ import annotations

import pathlib

import pytest

from phone_designer.skills.reverse_engineer.cost_min_variant_search import (
    CostMinVariantSearch,
    _ALL_KEYS,
    _KEY2DFM,
    _KEY2TOOL,
)

_REPO = pathlib.Path(__file__).resolve().parents[2]
_LINKRODS = _REPO / "corpus" / "oem" / "complex" / "occt__linkrods.step"


# ──────────────────────────────────────────────────────────────────────────────
# fast unit tests — no OCCT

def test_key_to_dfm_mapping_is_single_sourced():
    # six cost-keys collapse to three DFM process names.
    assert _KEY2DFM["cnc_3axis"] == "cnc_milling"
    assert _KEY2DFM["cnc_5axis"] == "cnc_milling"
    assert _KEY2DFM["injection_mold_pa"] == "injection_molding"
    assert _KEY2DFM["sheet_laser_brake"] == "sheet_metal"
    assert _KEY2DFM["sheet_progressive_die"] == "sheet_metal"
    # tooling class carried for the tie-break.
    assert _KEY2TOOL["cnc_3axis"] == "machined"
    assert _KEY2TOOL["injection_mold_pa"] == "hard_tool"


def test_costable_keys_exclude_advisory_processes():
    assert "die_cast_al" not in _ALL_KEYS and "3d_printing" not in _ALL_KEYS
    assert "cnc_3axis" in _ALL_KEYS and "injection_mold_pa" in _ALL_KEYS


def test_unknown_process_key_rejected():
    with pytest.raises(Exception, match="unknown process"):
        CostMinVariantSearch.Args(driver="x", values=[1.0], processes=["cnc4axis"])


def test_empty_process_list_rejected():
    # [] is a usage error (None means "all"), caught up front rather than raising
    # deep in the sweep (e.g. an empty DFM-process mapping under repair_dfm=True).
    with pytest.raises(Exception, match="non-empty"):
        CostMinVariantSearch.Args(driver="x", values=[1.0], processes=[])


def test_must_supply_values_or_sweep_is_enforced_downstream():
    # Args construct fine; the one-of rule is enforced at apply via
    # resolve_sweep_values — pin that the field defaults are both-None.
    a = CostMinVariantSearch.Args(driver="housing_length", values=[1.0])
    assert a.sweep is None and a.repair_dfm is False and a.max_variants == 16


# ──────────────────────────────────────────────────────────────────────────────
# integration: genuine-winner path on a generated solid box

def _box_catalog():
    from phone_designer.corpus.regress import _force_register_all
    _force_register_all()
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    body = Box().apply(None, {"length_mm": 40.0, "width_mm": 30.0,
                              "height_mm": 20.0}).body
    cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    return body, cat


@pytest.mark.slow
def test_solid_box_cheapest_viable_wins():
    body, cat = _box_catalog()
    res = CostMinVariantSearch().apply(body, {
        "catalog": cat, "driver": "housing_length",
        "values": [20.0, 30.0, 40.0, 50.0], "lot_size": 1000,
        "processes": ["cnc_3axis"]})
    out = res.extras["cost_variant_search"]

    assert out["grade"] == "estimate"
    assert out["overall_flag"] == "ok"
    assert out["summary"]["n_valid"] == 4
    assert out["summary"]["n_competitors"] == 4
    # every competitor is the STRICT viable tier.
    for v in out["variants"]:
        assert v["viable"] is True and v["viability_tier"] == "viable"
    # cost rises with size -> the SMALLEST is the cheapest winner.
    assert out["winner"] is not None
    assert out["winner"]["value"] == 20.0
    assert out["winner"]["process"] == "cnc_3axis"
    costs = [v["unit_cost_usd"] for v in
             sorted(out["variants"], key=lambda v: v["value"])]
    assert all(costs[i + 1] > costs[i] for i in range(len(costs) - 1)), costs
    # fidelity scales with the edit; the winner (==baseline) is most faithful.
    assert out["winner"]["fidelity_vs_baseline_mm"] == 0.0
    assert out["closest_viable"]["value"] == 20.0
    # read-only — body returned IS the input.
    assert res.body is body


@pytest.mark.slow
def test_box_max_deviation_gate_excludes_far_variants():
    body, cat = _box_catalog()
    # tight fidelity gate: only the baseline + the nearest stay competitors.
    res = CostMinVariantSearch().apply(body, {
        "catalog": cat, "driver": "housing_length",
        "values": [20.0, 30.0, 60.0], "lot_size": 1000,
        "processes": ["cnc_3axis"], "max_deviation_mm": 12.0})
    out = res.extras["cost_variant_search"]
    far = next(v for v in out["variants"] if v["value"] == 60.0)
    assert far["excluded_by_fidelity"] is True
    assert far["competitor"] is False
    # the far variant still appears in the table (never silently dropped).
    assert any(v["value"] == 60.0 for v in out["cost_curve"])


@pytest.mark.slow
def test_over_cap_sweep_raises_never_truncates():
    body, cat = _box_catalog()
    with pytest.raises(ValueError, match="max_variants"):
        CostMinVariantSearch().apply(body, {
            "catalog": cat, "driver": "housing_length",
            "sweep": {"start": 20.0, "stop": 60.0, "steps": 30},
            "max_variants": 16})


# ──────────────────────────────────────────────────────────────────────────────
# integration: airtight gate on the linkrods corpus part (all-marginal honesty)

@pytest.mark.slow
@pytest.mark.fidelity
@pytest.mark.skipif(not _LINKRODS.is_file(),
                    reason="occt__linkrods.step corpus file not present")
def test_linkrods_all_marginal_never_crowns_a_winner():
    from phone_designer.corpus.regress import _force_register_all
    _force_register_all()
    from phone_designer.corpus.parametric_demo import load_file
    from phone_designer.skills.reverse_engineer.identify_key_dimensions import (
        identify_key_dimensions,
    )
    body, catalog, _ = load_file(_LINKRODS)
    base = next(e["value_mm"] for e in identify_key_dimensions(catalog)
                if e["name"] == "housing_length")

    res = CostMinVariantSearch().apply(body, {
        "catalog": catalog, "driver": "housing_length",
        "values": [base, base * 1.25, base * 1.5, base * 2.0], "lot_size": 1000})
    out = res.extras["cost_variant_search"]

    # the thin-walled linkrods is DFM-marginal across the board -> every variant
    # is valid + priced but only 'viable_marginal' -> NONE competes -> no winner.
    assert out["summary"]["n_valid"] == 4
    assert out["summary"]["n_competitors"] == 0
    assert out["winner"] is None
    assert out["overall_flag"] == "only_marginal_variants"
    for v in out["variants"]:
        assert v["valid"] is True
        assert v["unit_cost_usd"] is not None      # priced
        assert v["viable"] is False                 # but cannot win
        assert v["viability_tier"] in ("viable_marginal", "unproven")
    # fidelity scales monotonically with the edit, anchored at the baseline.
    fids = [v["fidelity_vs_baseline_mm"]
            for v in sorted(out["variants"], key=lambda v: v["value"])]
    assert fids[0] == 0.0
    assert all(fids[i + 1] > fids[i] for i in range(len(fids) - 1)), fids
    assert res.body is body
