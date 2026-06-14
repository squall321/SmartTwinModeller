"""generate_variant_family — sweep a driver over N values into N validated
variant rebuilds (Pillar VARIANTS, 2026-06-14).

Two layers:
  * fast unit tests for the pure value-resolution / arg-validation logic
    (no OCCT) — these run everywhere;
  * the pillar VERIFICATION test on occt__linkrods (slow / fidelity, skipped
    when the corpus STEP is absent): sweep housing_length over
    [baseline, 1.25x, 1.5x, 2.0x] → 4 variants, all valid, bbox length scales
    monotonically, fidelity_vs_baseline reported per variant (≈0 at the
    baseline, growing with the edit).
"""
from __future__ import annotations

import pathlib

import pytest

from phone_designer.skills.reverse_engineer.generate_variant_family import (
    GenerateVariantFamily,
    resolve_sweep_values,
)

_REPO = pathlib.Path(__file__).resolve().parents[2]
_LINKRODS = _REPO / "corpus" / "oem" / "complex" / "occt__linkrods.step"


# ──────────────────────────────────────────────────────────────────────────────
# pure value resolution (no OCCT)


def test_explicit_values_pass_through():
    assert resolve_sweep_values([5.0, 6.25, 7.5, 10.0], None) == [
        5.0, 6.25, 7.5, 10.0,
    ]


def test_sweep_is_inclusive_and_evenly_spaced():
    vals = resolve_sweep_values(None, {"start": 5.0, "stop": 10.0, "steps": 5})
    assert vals == pytest.approx([5.0, 6.25, 7.5, 8.75, 10.0])


def test_sweep_two_steps_is_endpoints():
    assert resolve_sweep_values(
        None, {"start": 2.0, "stop": 4.0, "steps": 2},
    ) == pytest.approx([2.0, 4.0])


def test_must_supply_exactly_one_of_values_or_sweep():
    with pytest.raises(ValueError, match="exactly one"):
        resolve_sweep_values(None, None)
    with pytest.raises(ValueError, match="exactly one"):
        resolve_sweep_values([1.0], {"start": 1.0, "stop": 2.0, "steps": 2})


def test_sweep_steps_must_be_at_least_two():
    with pytest.raises(ValueError, match="steps"):
        resolve_sweep_values(None, {"start": 1.0, "stop": 2.0, "steps": 1})


def test_empty_values_rejected():
    with pytest.raises(ValueError, match="empty"):
        resolve_sweep_values([], None)


def test_non_numeric_value_rejected():
    with pytest.raises(ValueError, match="non-numeric"):
        resolve_sweep_values([1.0, "big"], None)


def test_unknown_arg_key_raises():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GenerateVariantFamily().apply(None, {
            "driver": "housing_length",
            "values": [1.0, 2.0],
            "stepss": "box",   # typo
        })


# ──────────────────────────────────────────────────────────────────────────────
# Pillar verification — occt__linkrods housing_length sweep


@pytest.mark.slow
@pytest.mark.fidelity
@pytest.mark.skipif(
    not _LINKRODS.is_file(),
    reason="occt__linkrods.step corpus file not present (partial checkout)",
)
def test_linkrods_housing_length_sweep_is_a_valid_monotone_family():
    from phone_designer.corpus.regress import _force_register_all

    _force_register_all()
    from phone_designer.corpus.parametric_demo import load_file

    body, catalog, _ext = load_file(_LINKRODS)

    # housing_length baseline extent ≈ 5.02 mm — sweep it 1.0/1.25/1.5/2.0×.
    baseline = next(
        e["value_mm"]
        for e in __import__(
            "phone_designer.skills.reverse_engineer.identify_key_dimensions",
            fromlist=["identify_key_dimensions"],
        ).identify_key_dimensions(catalog)
        if e["name"] == "housing_length"
    )
    values = [baseline, baseline * 1.25, baseline * 1.5, baseline * 2.0]

    res = GenerateVariantFamily().apply(body, {
        "catalog": catalog,
        "driver": "housing_length",
        "values": values,
    })
    fam = res.extras["variant_family"]

    assert fam["driver"] == "housing_length"
    assert fam["driver_role"] == "envelope"
    assert fam["fidelity_metric"] == "hausdorff_mm"
    assert fam["summary"]["n"] == 4

    variants = fam["variants"]
    assert len(variants) == 4

    # (1) all four variants are valid: features_lost==0 AND brep_valid.
    for v in variants:
        assert v["error"] is None, v
        assert v["valid"] is True, v
        assert v["features_lost"] == 0, v
        assert v["brep_valid"] is True, v
        assert v["drivers_unresolved"] == [], v
    assert fam["summary"]["n_valid"] == 4

    # (2) the swept axis (x / length) scales monotonically with the driver.
    lengths = [v["bbox"][0] for v in variants]
    assert all(
        lengths[i + 1] > lengths[i] for i in range(len(lengths) - 1)
    ), lengths
    # and each length tracks its target value (envelope extent == driver).
    for v in variants:
        assert v["bbox"][0] == pytest.approx(v["value"], rel=0.01), v

    # (3) fidelity_vs_baseline is reported per variant, RELATIVE to value[0]:
    #     ~0 at the baseline, strictly growing with the edit magnitude.
    fids = [v["fidelity_vs_baseline"] for v in variants]
    assert all(f is not None for f in fids), fids
    assert fids[0] == pytest.approx(0.0, abs=1e-6), fids
    assert all(fids[i + 1] > fids[i] for i in range(len(fids) - 1)), fids

    # (4) per-variant plan files were written (write_plans default True).
    for v in variants:
        assert v["plan_path"] is not None
        assert pathlib.Path(v["plan_path"]).is_file(), v

    # read-only: the supplied body is returned unchanged.
    assert res.body is body
