"""_pillar_tools — thin JSON-safe wrappers over the COMPARE / VARIANTS /
CHEAPEST-VARIANT pillars (Phase-1 track 1-4).

Corpus-independent: every integration test builds its OWN small synthetic
body with build123d (a plate-with-holes for compare, a 40x30x20 box for the
variant sweeps — the same shapes the pillars' own tests validated) and writes
it to a STEP in tmp_path. Verified properties:

  * compare_parts_tool(A, A) -> classification 'identical', report artifact
    written, strict-JSON-safe output; a missing path raises (unmasked).
  * variants_tool chains identify_key_dimensions -> generate_variant_family:
    the rank-0 driver (housing_length) is swept 1.0x..2.0x, every variant
    record carries value/valid/plan_path, output strict-JSON-safe; bad
    n_variants refused with fm.invalid_args.
  * cheapest_variant_tool preserves the STRICT viability gate: gate fields
    (overall_flag / winner / per-variant viability_tier) pass through, the
    box winner is the smallest genuinely-viable value, and the gate-violation
    guard raises on a crafted marginal winner (the declared failure path is
    reachable).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from phone_designer.mcp_support._pillar_tools import (
    _assert_strict_viability_gate,
    _json_safe,
    cheapest_variant_tool,
    compare_parts_tool,
    variants_tool,
)


# ──────────────────────────────────────────────────────────────────────────────
# synthetic STEP fixtures (corpus-independent)


def _register_all():
    from phone_designer.corpus.regress import _force_register_all
    _force_register_all()


def _write_step(part, path: Path) -> str:
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer

    shape = part.wrapped if hasattr(part, "wrapped") else part
    w = STEPControl_Writer()
    assert w.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs) == (
        IFSelect_ReturnStatus.IFSelect_RetDone)
    assert w.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone
    return str(path)


@pytest.fixture(scope="module")
def plate_step(tmp_path_factory) -> str:
    """A prismatic plate with three distinct through holes — same synthetic
    shape test_compare_parts pinned for the compare macro."""
    _register_all()
    from build123d import Align, Box, Cylinder, Pos

    height = 6.0
    plate = Box(40.0, 30.0, height,
                align=(Align.CENTER, Align.CENTER, Align.MIN))
    for (x, y, d) in [(-10.0, -6.0, 6.0), (12.0, 8.0, 4.0), (4.0, -9.0, 5.0)]:
        plate = plate - (Pos(x, y, height / 2.0) * Cylinder(d / 2.0, height + 2.0))
    p = tmp_path_factory.mktemp("pillar_compare") / "plate_a.step"
    return _write_step(plate, p)


@pytest.fixture(scope="module")
def box_step(tmp_path_factory) -> str:
    """The 40x30x20 solid box the variant-sweep pillars' own tests use."""
    _register_all()
    from phone_designer.skills.create.box import Box

    body = Box().apply(None, {"length_mm": 40.0, "width_mm": 30.0,
                              "height_mm": 20.0}).body
    p = tmp_path_factory.mktemp("pillar_box") / "box.step"
    return _write_step(body, p)


# ──────────────────────────────────────────────────────────────────────────────
# fast unit tests — no OCCT geometry


def test_json_safe_strips_nonfinite_and_tuples():
    out = _json_safe({
        "nan": float("nan"),
        "inf": float("inf"),
        "ninf": float("-inf"),
        "t": (1, 2.5),
        "p": Path("x/y"),
        "nested": [{"v": float("inf")}],
        "keep": 3.5,
    })
    assert out["nan"] is None and out["inf"] is None and out["ninf"] is None
    assert out["t"] == [1, 2.5]
    assert isinstance(out["p"], str)
    assert out["nested"][0]["v"] is None
    assert out["keep"] == 3.5
    json.dumps(out, allow_nan=False)  # strict-JSON contract


def test_gate_guard_lets_viable_winner_and_none_through():
    _assert_strict_viability_gate({"winner": None, "variants": []})
    _assert_strict_viability_gate({
        "winner": {"value": 20.0, "viability_tier": "viable"},
        "variants": [{"value": 20.0, "viable": True,
                      "viability_tier": "viable"}],
    })


def test_gate_guard_raises_on_marginal_winner():
    # The declared fm.viability_gate_violation path — a marginal candidate can
    # NEVER be crowned, not even by an upstream regression.
    with pytest.raises(RuntimeError, match="fm.viability_gate_violation"):
        _assert_strict_viability_gate({
            "winner": {"value": 20.0, "viability_tier": "viable_marginal"},
            "variants": [],
        })
    # winner tier lies 'viable' but its variant record disagrees -> also raise.
    with pytest.raises(RuntimeError, match="fm.viability_gate_violation"):
        _assert_strict_viability_gate({
            "winner": {"value": 20.0, "viability_tier": "viable"},
            "variants": [{"value": 20.0, "viable": False,
                          "viability_tier": "unproven"}],
        })


def test_variants_tool_refuses_bad_n():
    with pytest.raises(ValueError, match="fm.invalid_args"):
        variants_tool("does_not_matter.step", n_variants=1)
    with pytest.raises(ValueError, match="fm.invalid_args"):
        variants_tool("does_not_matter.step", n_variants=99)


def test_cheapest_variant_refuses_unknown_process_fast():
    # validated BEFORE any body load — a typo fails in milliseconds.
    with pytest.raises(ValueError, match="fm.invalid_args"):
        cheapest_variant_tool("does_not_matter.step", "cnc4axis")


def test_cheapest_variant_refuses_bad_iterations():
    with pytest.raises(ValueError, match="fm.invalid_args"):
        cheapest_variant_tool("x.step", "cnc_3axis", max_iterations=1)


def test_compare_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        compare_parts_tool(str(tmp_path / "missing_a.step"),
                           str(tmp_path / "missing_b.step"),
                           str(tmp_path))


# ──────────────────────────────────────────────────────────────────────────────
# integration — compare


@pytest.mark.slow
def test_compare_identical_plates(plate_step, tmp_path):
    out = compare_parts_tool(plate_step, plate_step, str(tmp_path))

    assert out["ok"] is True
    assert out["summary"]["classification"] == "identical"
    assert out["summary"]["similarity_score"] >= 0.98
    haus = out["summary"]["hausdorff_mm"]
    assert haus is not None and haus < 0.5
    assert out["grade"] == "estimate"

    # full comparison passes through (stage table included — honest metadata).
    comp = out["part_comparison"]
    assert "_stages" in comp and comp["classification"] == "identical"

    # artifact written and parseable.
    report = Path(out["artifacts"]["report_json"])
    assert report.is_file()
    on_disk = json.loads(report.read_text(encoding="utf-8"))
    assert on_disk["classification"] == "identical"

    # strict-JSON-safe end to end.
    json.dumps(out, allow_nan=False)


# ──────────────────────────────────────────────────────────────────────────────
# integration — variants


@pytest.mark.slow
def test_variants_tool_box_family(box_step, tmp_path):
    out = variants_tool(box_step, n_variants=3, out_dir=str(tmp_path))

    assert out["ok"] is True
    # rank-0 key dimension of a 40x30x20 box is its length.
    assert out["key_dimensions"][0]["name"] == "housing_length"
    assert out["driver"] == "housing_length"
    assert out["driver_role"] == "envelope"

    # 1.0x .. 2.0x sweep of the 40mm length.
    assert out["values"] == pytest.approx([40.0, 60.0, 80.0])
    assert out["baseline_value"] == pytest.approx(40.0)

    variants = out["variants"]
    assert len(variants) == 3
    assert out["summary"]["n"] == 3
    assert out["summary"]["n_valid"] == 3
    for v in variants:
        assert v["valid"] is True
        assert v["plan_path"] is not None  # per-variant plan artifact
        assert v["volume_mm3"] is not None and v["volume_mm3"] > 0
    # fidelity anchored at the baseline rebuild, growing with the edit.
    fids = [v["fidelity_vs_baseline"] for v in variants]
    assert fids[0] == pytest.approx(0.0, abs=1e-6)
    assert fids[1] is not None and fids[2] is not None
    assert fids[2] > fids[1] > 0.0

    assert Path(out["artifacts"]["report_json"]).is_file()
    json.dumps(out, allow_nan=False)


# ──────────────────────────────────────────────────────────────────────────────
# integration — cheapest variant (strict gate pass-through)


@pytest.mark.slow
def test_cheapest_variant_box_strict_gate(box_step, tmp_path):
    out = cheapest_variant_tool(
        box_step, "cnc_3axis", material="aluminum", lot_size=1000,
        out_dir=str(tmp_path), max_iterations=3)

    assert out["ok"] is True
    assert out["grade"] == "estimate"

    search = out["cost_variant_search"]
    # gate fields pass through on EVERY variant record.
    for v in search["variants"]:
        assert "viable" in v and "viability_tier" in v
    # the box is genuinely viable on cnc_3axis -> a strict-tier winner exists
    # and it is the smallest (cheapest) value: 0.5 x 40 = 20. The sweep goes
    # DOWN from the 1.0x baseline, so the cheapest is also the FURTHEST from
    # the baseline rebuild — the macro honestly flags that (its absolute
    # low-fidelity warning fires even with no max_deviation_mm set).
    assert out["overall_flag"] == "cheapest_low_fidelity"
    assert out["winner"] is not None
    assert out["winner"]["viability_tier"] == "viable"
    assert out["winner"]["value"] == pytest.approx(20.0)
    assert out["winner"]["process"] == "cnc_3axis"
    # every competitor sits in the STRICT tier (marginal can never compete).
    for v in search["variants"]:
        if v["competitor"]:
            assert v["viable"] is True and v["viability_tier"] == "viable"
    # savings vs the 1.0x baseline are real and positive.
    savings = out["savings_vs_baseline"]
    assert savings is not None and savings["abs_usd"] > 0

    assert Path(out["artifacts"]["report_json"]).is_file()
    json.dumps(out, allow_nan=False)
