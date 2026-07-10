"""Process-name unification — ONE canonicaliser for the two vocabularies.

Historically cost skills said 'cnc_3axis'/'cnc_5axis' while DFM skills said
'cnc_milling', so callers had to know two spellings. skills/_process_names.py
is the single alias table; every accepting entry point (estimate_cost,
dfm_verdict, repair_dfm, recommend_process, quote_package,
cost_min_variant_search) now takes BOTH spellings while its OUTPUT keeps the
existing canonical names (byte-stability for canonical callers).

PINS
1. repair_dfm(processes=['cnc_3axis']) == repair_dfm(processes=['cnc_milling'])
   result-IDENTICAL on a fixture part.
2. estimate_cost(process='cnc_milling') works and returns the same numbers as
   'cnc_3axis' (canonical label in 'process', caller spelling preserved in
   'process_requested').
3. Unknown names -> each skill's existing honest refusal, unchanged.
"""
from __future__ import annotations

import pytest
from build123d import Axis, Box, BuildPart, BuildSketch, Mode, Rectangle, extrude
from pydantic import ValidationError

from phone_designer.skills._process_names import (
    COST_CANONICAL,
    DFM_CANONICAL,
    canon_dfm_processes,
    expand_cost_candidates,
    to_cost_process,
    to_dfm_process,
)
from phone_designer.skills.inspect.dfm_verdict import (
    _EMBEDDED_DEFAULTS,
    _PROCESS_TO_CODE,
    DfmVerdict,
)
from phone_designer.skills.inspect.estimate_cost import EstimateCost
from phone_designer.skills.inspect.quote_package import QuotePackage
from phone_designer.skills.inspect.recommend_process import (
    _CANDIDATES,
    RecommendProcess,
)
from phone_designer.skills.repair.repair_dfm import RepairDfm
from phone_designer.skills.reverse_engineer.cost_min_variant_search import (
    CostMinVariantSearch,
)


# ── fixtures ────────────────────────────────────────────────────────────────

def _sharp_pocket():
    """Block with a square pocket -> sharp internal corners (the repair_dfm
    headline fillet case) — same fixture family as test_repair_dfm."""
    with BuildPart() as bp:
        Box(40, 30, 12)
        with BuildSketch(bp.faces().sort_by(Axis.Z)[-1]):
            Rectangle(16, 10)
        extrude(amount=-8, mode=Mode.SUBTRACT)
    return bp.part


#: fixed synthetic drivers -> estimate_cost becomes PURE MATH (no extraction),
#: so cross-spelling comparisons are exact by construction.
_PRE = {"volume_mm3": 12000.0, "n_holes": 2, "n_pockets": 1, "n_bosses": 0,
        "max_wall_mm": 3.0, "bbox": {"size": [40.0, 30.0, 12.0]}}


def _cost(body, **kw):
    return EstimateCost().apply(body, kw).extras["cost_estimate"]


# ── 1. the canonicaliser itself (pure unit) ─────────────────────────────────

def test_to_dfm_process_folds_cost_spellings():
    assert to_dfm_process("cnc_3axis") == "cnc_milling"
    assert to_dfm_process("cnc_5axis") == "cnc_milling"   # one DFM family
    assert to_dfm_process("injection_mold_pa") == "injection_molding"
    assert to_dfm_process("die_cast_al") == "die_casting"
    assert to_dfm_process("sheet_laser_brake") == "sheet_metal"
    # canonical DFM names round-trip
    for name in DFM_CANONICAL:
        assert to_dfm_process(name) == name
    # unknown names pass through EXACTLY as given (case included)
    assert to_dfm_process("Bogus") == "Bogus"


def test_to_cost_process_folds_dfm_spellings():
    assert to_cost_process("cnc_milling") == "cnc_3axis"   # family default
    assert to_cost_process("injection_molding") == "injection_mold_pa"
    # 5-axis stays DISTINCT — never implied by 'cnc_milling'
    assert to_cost_process("cnc_5axis") == "cnc_5axis"
    # ambiguous / unpriced names are NOT force-mapped
    assert to_cost_process("sheet_metal") == "sheet_metal"
    assert to_cost_process("die_casting") == "die_casting"
    for name in COST_CANONICAL:
        assert to_cost_process(name) == name
    assert to_cost_process("Bogus") == "Bogus"


def test_canon_dfm_processes_dedup_and_alias_record():
    canon, aliases = canon_dfm_processes(["cnc_3axis", "cnc_5axis", "bogus"])
    assert canon == ["cnc_milling", "bogus"]
    assert aliases == {"cnc_3axis": "cnc_milling", "cnc_5axis": "cnc_milling"}
    # canonical input round-trips with NO alias record (byte-stability)
    assert canon_dfm_processes(["cnc_milling", "injection_molding"]) == (
        ["cnc_milling", "injection_molding"], {})


def test_expand_cost_candidates_family_expansion():
    exp, aliases = expand_cost_candidates(["cnc_milling"])
    assert exp == ["cnc_3axis", "cnc_5axis"]
    assert aliases == {"cnc_milling": ["cnc_3axis", "cnc_5axis"]}
    exp, aliases = expand_cost_candidates(["sheet_metal"])
    assert exp == ["sheet_laser_brake", "sheet_turret_brake",
                   "sheet_progressive_die"]
    # canonical keys pass through untouched; unknowns pass through as given
    assert expand_cost_candidates(["cnc_3axis", "bogus"]) == (
        ["cnc_3axis", "bogus"], {})


def test_alias_tables_do_not_drift_from_the_registries():
    # every DFM name dfm_verdict knows is canonical here, and vice versa
    assert DFM_CANONICAL == set(_PROCESS_TO_CODE) | set(_EMBEDDED_DEFAULTS)
    # every recommend_process candidate key maps into a known DFM family
    for key, _cost_proc, dfm_proc, _tool, _costable in _CANDIDATES:
        assert to_dfm_process(key) == (dfm_proc or to_dfm_process(key)), key
        if dfm_proc is not None:
            assert to_dfm_process(key) == dfm_proc, key
    # cost canonical covers all costable candidate keys
    assert {c[0] for c in _CANDIDATES} <= COST_CANONICAL


# ── 2. PIN 1: repair_dfm result-identical across spellings ──────────────────

def test_pin1_repair_dfm_spelling_identical():
    body = _sharp_pocket()
    a = RepairDfm().apply(body, {"processes": ["cnc_3axis"]}).extras["dfm_repair"]
    b = RepairDfm().apply(body, {"processes": ["cnc_milling"]}).extras["dfm_repair"]
    assert a == b                       # the WHOLE result, bit-for-bit
    assert a["processes"] == ["cnc_milling"]  # canonical DFM name in output
    # and the repair genuinely did something on this fixture (not a vacuous ==)
    assert a["fixes_applied"] or a["suggestions"]


# ── 3. PIN 2: estimate_cost same numbers across spellings ───────────────────

def test_pin2_estimate_cost_cnc_milling_equals_cnc_3axis():
    body = _sharp_pocket()
    c3 = _cost(body, process="cnc_3axis", precomputed=dict(_PRE))
    cm = _cost(body, process="cnc_milling", precomputed=dict(_PRE))
    assert cm["process"] == "cnc_3axis"           # canonical label kept
    assert cm["process_requested"] == "cnc_milling"  # caller spelling preserved
    assert cm["unit_cost_usd"] == c3["unit_cost_usd"]
    assert cm["breakdown_usd"] == c3["breakdown_usd"]
    assert cm["cycle_time_s"] == c3["cycle_time_s"]
    assert cm["drivers"] == c3["drivers"]
    # the ONLY differences are the honest alias trail
    trimmed = {k: v for k, v in cm.items()
               if k not in ("process_requested", "assumptions")}
    assert trimmed == {k: v for k, v in c3.items() if k != "assumptions"}
    assert any("process alias" in a for a in cm["assumptions"])


def test_pin2_injection_alias_and_5axis_stays_distinct():
    body = _sharp_pocket()
    ia = _cost(body, process="injection_molding", precomputed=dict(_PRE))
    ib = _cost(body, process="injection_mold_pa", precomputed=dict(_PRE))
    assert ia["process"] == "injection_mold_pa"
    assert ia["unit_cost_usd"] == ib["unit_cost_usd"]
    # cnc_milling never implies the 5-axis factor
    c5 = _cost(body, process="cnc_5axis", precomputed=dict(_PRE))
    cm = _cost(body, process="cnc_milling", precomputed=dict(_PRE))
    assert c5["process"] == "cnc_5axis"
    assert c5["breakdown_usd"]["machine"] > cm["breakdown_usd"]["machine"]


def test_estimate_cost_canonical_caller_byte_stable():
    # canonical spelling -> NO alias keys, NO alias assumption (byte-stability)
    c3 = _cost(_sharp_pocket(), process="cnc_3axis", precomputed=dict(_PRE))
    assert "process_requested" not in c3
    assert not any("process alias" in a for a in c3["assumptions"])


# ── 4. dfm_verdict + recommend_process accept both spellings ────────────────

def test_dfm_verdict_accepts_cost_spellings_identically():
    body = _sharp_pocket()
    d3 = DfmVerdict().apply(body, {"processes": ["cnc_3axis"]}).extras["dfm_verdict"]
    dm = DfmVerdict().apply(body, {"processes": ["cnc_milling"]}).extras["dfm_verdict"]
    assert set(d3["processes"]) == {"cnc_milling"}   # canonical key, verdict runs
    assert d3["processes"] == dm["processes"]        # identical verdict payload
    # the fold is recorded ONLY when it happened
    assert d3["_meta"]["process_aliases"] == {"cnc_3axis": "cnc_milling"}
    assert "process_aliases" not in dm["_meta"]
    # caller spelling preserved in the request echo
    assert d3["_meta"]["processes_requested"] == ["cnc_3axis"]


def test_recommend_process_candidates_family_expansion():
    body = _sharp_pocket()
    rp = RecommendProcess().apply(body, {
        "candidates": ["cnc_milling"], "lot_size": 100,
    }).extras["process_recommendation"]
    assert rp["request"]["candidates"] == ["cnc_3axis", "cnc_5axis"]
    assert any("candidate aliases accepted" in a for a in rp["assumptions"])


# ── 5. validator entry points (quote_package / cost_min_variant_search) ─────

def test_quote_package_processes_accept_both_vocabularies():
    args = QuotePackage.Args(out_dir="unused", processes=["cnc_milling"])
    assert args.processes == ["cnc_3axis", "cnc_5axis"]
    # canonical input round-trips untouched
    args = QuotePackage.Args(out_dir="unused", processes=["cnc_3axis"])
    assert args.processes == ["cnc_3axis"]


def test_cost_min_variant_search_processes_accept_both_vocabularies():
    args = CostMinVariantSearch.Args(driver="d", processes=["cnc_milling"])
    assert args.processes == ["cnc_3axis", "cnc_5axis"]
    args = CostMinVariantSearch.Args(driver="d", processes=["injection_molding"])
    assert args.processes == ["injection_mold_pa"]


# ── 6. PIN 3: unknown names -> the existing honest refusals, unchanged ──────

def test_pin3_dfm_verdict_unknown_still_reported():
    dv = DfmVerdict().apply(_sharp_pocket(), {
        "processes": ["cnc_milling", "bogus"],
    }).extras["dfm_verdict"]
    assert dv["_meta"]["unknown_processes"] == ["bogus"]
    assert "bogus" not in dv["processes"]


def test_pin3_estimate_cost_unknown_still_flagged():
    un = _cost(_sharp_pocket(), process="frobnicate", precomputed=dict(_PRE))
    assert un["process"] == "frobnicate"          # NOT silently renamed
    assert "process_requested" not in un
    assert any("unknown process 'frobnicate'" in a for a in un["assumptions"])


def test_pin3_quote_package_unknown_still_raises():
    with pytest.raises(ValidationError, match="unknown process key"):
        QuotePackage.Args(out_dir="unused", processes=["bogus"])
    # a DFM name with NO costable model is refused under its ORIGINAL spelling
    with pytest.raises(ValidationError, match="die_casting"):
        QuotePackage.Args(out_dir="unused", processes=["die_casting"])


def test_pin3_cost_min_variant_search_unknown_still_raises():
    with pytest.raises(ValidationError, match="unknown process key"):
        CostMinVariantSearch.Args(driver="d", processes=["bogus"])
