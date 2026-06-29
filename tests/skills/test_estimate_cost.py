"""estimate_cost — honest unit-cost + cycle-time estimate (2026-06-20).

Pins the transparent heuristic: material cost from the REAL measured volume,
process-specific machine/cycle time, amortised setup/tooling, and the right
sensitivities (5-axis > 3-axis, denser material > cheaper, bigger lot → lower
moulding unit cost). result_grade='estimate' — a model, not a quote.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.estimate_cost import EstimateCost
from phone_designer.skills.modify_pocket.hole import Hole


def _al_housing():
    b = Box().apply(None, {"length_mm": 60.0, "width_mm": 40.0, "height_mm": 12.0}).body
    for x in (-20.0, -12.0, -4.0, 4.0, 12.0, 20.0):
        b = Hole().apply(b, {"position": (x, 0.0, 12.0), "diameter_mm": 4.0,
                             "depth_mm": 8.0, "direction": "-Z"}).body
    return b


def _est(body, **kw):
    return EstimateCost().apply(body, kw).extras["cost_estimate"]


def test_cnc_estimate_breakdown_and_drivers():
    e = _est(_al_housing(), process="cnc_3axis", material="aluminum", lot_size=1000)
    assert e["grade"] == "estimate"
    assert e["unit_cost_usd"] > 0
    assert e["cycle_time_s"] and e["cycle_time_s"] > 0
    # material is driven by the REAL volume (60x40x12 Al block minus holes ~ 28cm3)
    assert 25.0 < e["drivers"]["volume_cm3"] < 30.0
    assert e["breakdown_usd"]["material"] > 0
    assert "machine" in e["breakdown_usd"]
    assert e["assumptions"]  # documented heuristics surfaced


def test_injection_estimate_has_cycle_and_amortised_tooling():
    e = _est(_al_housing(), process="injection_mold_pa", material="abs", lot_size=10000)
    assert e["cycle_time_s"] > 0
    assert "cycle" in e["breakdown_usd"] and "tooling_amortised" in e["breakdown_usd"]


def test_cost_sensitivities_are_monotone():
    b = _al_housing()
    c3 = _est(b, process="cnc_3axis", material="aluminum")
    c5 = _est(b, process="cnc_5axis", material="aluminum")
    ti = _est(b, process="cnc_3axis", material="titanium")
    im_small = _est(b, process="injection_mold_pa", material="abs", lot_size=1000)
    im_big = _est(b, process="injection_mold_pa", material="abs", lot_size=100000)
    assert c5["breakdown_usd"]["machine"] > c3["breakdown_usd"]["machine"]   # 5-axis ×2.5
    assert ti["breakdown_usd"]["material"] > c3["breakdown_usd"]["material"]  # denser/pricier
    assert im_big["unit_cost_usd"] < im_small["unit_cost_usd"]               # tooling amortises


def test_rate_override_changes_the_estimate():
    b = _al_housing()
    base = _est(b, process="cnc_3axis", material="aluminum")
    fast = _est(b, process="cnc_3axis", material="aluminum",
                rates={"cnc_machine_usd_per_hr": 100.0})
    assert fast["breakdown_usd"]["machine"] > base["breakdown_usd"]["machine"]


def test_normal_part_is_reliable():
    e = _est(_al_housing(), process="cnc_3axis", material="aluminum")
    assert e["reliability"] == "ok"


def test_micro_part_flagged_below_scale():
    # a 2x2x2mm part (8mm³ = 0.008cm³) is below the CNC/IM model's scale
    micro = Box().apply(None, {"length_mm": 2.0, "width_mm": 2.0,
                               "height_mm": 2.0}).body
    e = _est(micro, process="cnc_3axis", material="aluminum")
    assert e["reliability"] == "below_scale"
    assert any("below the scale" in a for a in e["assumptions"])


# ── corpus-backed regression guards (validated across the 161-file sweep): the
#    cost model must FLAG inputs outside its validity, not emit confident garbage.
def _cost_file(rel):
    from phone_designer.skills.create.import_step import ImportStep
    return EstimateCost().apply(
        ImportStep().apply(None, {"path": rel}).body, {}).extras["cost_estimate"]


_DEGENERATE = Path("corpus/oem/pythonocc__splinecage.step")
_BIG_ASSEMBLY = Path("corpus/oem/pythonocc__as1_pe_203.step")


@pytest.mark.skipif(not _DEGENERATE.exists(), reason="corpus splinecage absent")
def test_corpus_degenerate_geometry_flagged():
    # splinecage measures a NEGATIVE volume (open/inverted shell) → unreliable
    assert _cost_file(str(_DEGENERATE))["reliability"] == "degenerate_geometry"


@pytest.mark.skipif(not _BIG_ASSEMBLY.exists(), reason="corpus assembly absent")
def test_corpus_assembly_scale_flagged():
    # a multi-m³ assembly is not a single machined part → flagged, not a $3M quote
    assert _cost_file(str(_BIG_ASSEMBLY))["reliability"] == "above_scale"


# ── sheet-metal processes (laser+brake / turret / progressive-die stamping) ──
def _flat_sheet():
    return Box().apply(None, {"length_mm": 60.0, "width_mm": 40.0,
                              "height_mm": 2.0}).body


def test_sheet_laser_brake_flat_blank_has_no_brake():
    e = _est(_flat_sheet(), process="sheet_laser_brake", material="steel")
    assert "laser_cut" in e["breakdown_usd"] and "handling" in e["breakdown_usd"]
    assert "press_brake" not in e["breakdown_usd"]   # flat blank → no forming
    assert e["reliability"] == "ok"
    assert e["grade"] == "estimate"
    assert "stamping_crossover_lot" in e["drivers"]   # route decision aid


def test_sheet_process_on_non_sheet_is_flagged():
    # a thick housing is not sheet metal — the sheet estimate must say so
    e = _est(_al_housing(), process="sheet_laser_brake", material="aluminum")
    assert e["reliability"] == "not_sheet_metal"
    assert any("not recognised as sheet" in a.lower()
               or "not sheet" in a.lower() for a in e["assumptions"])


_USB = Path("corpus/oem/kicad__USB_A_Molex_67643_Horizontal.step")


@pytest.mark.skipif(not _USB.exists(), reason="corpus USB-A absent")
def test_corpus_formed_shield_laser_brake_vs_stamping():
    # a 12-bend micro shield: laser+brake is flagged infeasible (hand-brake is a
    # fiction at that scale); stamping tooling amortises away with volume
    from phone_designer.skills.create.import_step import ImportStep
    body = ImportStep().apply(None, {"path": str(_USB)}).body

    def _e(proc, lot):
        return EstimateCost().apply(body, {
            "process": proc, "material": "stainless", "lot_size": lot}
        ).extras["cost_estimate"]

    lb = _e("sheet_laser_brake", 1000)
    assert "press_brake" in lb["breakdown_usd"]      # it IS formed
    assert lb["reliability"] == "brake_infeasible_forming"

    s100 = _e("sheet_progressive_die", 100)
    s100k = _e("sheet_progressive_die", 100_000)
    assert "tooling_amortised" in s100["breakdown_usd"]
    assert s100k["unit_cost_usd"] < s100["unit_cost_usd"]   # tooling amortises
    # honest gate: stamping below the economic crossover (laser+brake cheaper) is
    # below_scale, not falsely 'ok'; above it, ok — gated on the real crossover,
    # NOT a magic lot<1000 constant
    assert s100["reliability"] == "below_scale"
    assert s100k["reliability"] == "ok"
    assert s100["drivers"]["stamping_crossover_lot"] > 1000


# ── cost completeness: secondary operations + tolerance → cost (2026-06) ──────
# Defaults reproduce the prior model BYTE-IDENTICALLY; secondary ops/tolerance
# only ever ADD cost when explicitly requested AND grounded.

def _holed_plate():
    """A plate with 3 catalog-detected through-holes (for tapping tests)."""
    b = Box().apply(None, {"length_mm": 60.0, "width_mm": 40.0, "height_mm": 12.0}).body
    for x in (-15.0, 0.0, 15.0):
        b = Hole().apply(b, {"position": (x, 0.0, 12.0), "diameter_mm": 6.0,
                             "depth_mm": 12.0, "direction": "-Z"}).body
    return b


def test_default_args_reproduce_old_unit_cost_byte_identical():
    b = _al_housing()
    e = _est(b, process="cnc_3axis", material="aluminum", lot_size=1000)
    assert e["unit_cost_usd"] == 6.5321
    assert e["breakdown_usd"] == {"material": 0.4568, "machine": 6.0628,
                                  "setup_amortised": 0.0125}
    assert _est(b, process="injection_mold_pa", material="abs",
                lot_size=10000)["unit_cost_usd"] == 1.3444
    assert _est(b, process="sheet_laser_brake", material="aluminum",
                lot_size=1000)["unit_cost_usd"] == 0.6425
    # the secondary machinery is present but inert on the default path.
    assert e["secondary_ops_usd"] == 0.0 and e["primary_usd"] == e["unit_cost_usd"]
    for k in ("finish", "tapping", "deburr", "heat_treat", "plating",
              "inspection", "tolerance_scrap"):
        assert k not in e["breakdown_usd"]
    assert e["drivers"]["tolerance_class"] == "medium"
    assert e["drivers"]["tol_machine_mult"] == 1.0
    assert e["drivers"]["surface_area_dm2"] is None      # area computed lazily
    assert e["drivers"]["n_threaded_holes"] == 0
    assert e["drivers"]["secondary_warnings"] == []


def test_anodize_adds_area_scaled_finish_line():
    big = Box().apply(None, {"length_mm": 120.0, "width_mm": 80.0,
                             "height_mm": 24.0}).body
    small = _al_housing()
    eb = _est(big, finish="anodize", material="aluminum")
    es = _est(small, finish="anodize", material="aluminum")
    assert eb["breakdown_usd"]["finish"] > es["breakdown_usd"]["finish"] > 0
    assert eb["drivers"]["surface_area_dm2"] > es["drivers"]["surface_area_dm2"] > 0
    # anodize requires aluminum — on steel it is flagged, NOT charged.
    mis = _est(small, finish="anodize", material="steel")
    assert "finish" not in mis["breakdown_usd"]
    assert "finish_material_mismatch" in mis["drivers"]["secondary_warnings"]
    # an unrecognised finish (typo / British spelling) is NOT priced, loudly.
    typo = _est(small, finish="anodise", material="aluminum")
    assert "finish" not in typo["breakdown_usd"]
    assert "finish_unrecognised" in typo["drivers"]["secondary_warnings"]


def test_fine_tolerance_raises_machine_cost():
    b = _al_housing()
    med = _est(b, tolerance_class="medium")
    fine = _est(b, tolerance_class="fine")
    prec = _est(b, tolerance_class="precision")
    assert fine["breakdown_usd"]["machine"] > med["breakdown_usd"]["machine"]
    assert prec["breakdown_usd"]["machine"] > fine["breakdown_usd"]["machine"]
    assert fine["drivers"]["tol_machine_mult"] == 1.4
    assert fine["unit_cost_usd"] > med["unit_cost_usd"]
    # fine tolerance also adds a transparent scrap line (a real breakdown key).
    assert "tolerance_scrap" in fine["breakdown_usd"]
    # coarse is cheaper than medium (0.9×).
    assert (_est(b, tolerance_class="coarse")["breakdown_usd"]["machine"]
            < med["breakdown_usd"]["machine"])


def test_threading_adds_per_hole_cost_and_clamps():
    plate = _holed_plate()             # 3 catalog holes
    assert "tapping" not in _est(plate)["breakdown_usd"]
    t1 = _est(plate, n_threaded_holes=1)
    t3 = _est(plate, n_threaded_holes=3)
    assert t3["breakdown_usd"]["tapping"] > t1["breakdown_usd"]["tapping"] > 0
    # cannot tap more holes than exist — clamp + warn, never fabricate threads.
    clamp = _est(plate, n_threaded_holes=999)
    assert clamp["drivers"]["n_threaded_holes"] == 3
    assert "tapping_clamped" in clamp["drivers"]["secondary_warnings"]


def test_secondary_subtotal_sums_into_unit_cost():
    e = _est(_holed_plate(), process="cnc_3axis", material="aluminum",
             finish="anodize", n_threaded_holes=2, tolerance_class="fine",
             plating="nickel", inspection_level="full",
             rates={"deburr_min_per_feature": 0.15, "inspect_min_per_feature": 0.15})
    sec = {"finish", "tapping", "deburr", "heat_treat", "plating",
           "inspection", "tolerance_scrap"}
    sub = round(sum(v for k, v in e["breakdown_usd"].items() if k in sec), 4)
    assert abs(e["secondary_ops_usd"] - sub) < 1e-6
    # invariant via a TOLERANCE, not exact == (per-line rounding ulps).
    assert abs(e["primary_usd"] + e["secondary_ops_usd"] - e["unit_cost_usd"]) < 1e-6
    assert e["secondary_ops_usd"] > 0 and e["grade"] == "estimate"
    assert "tolerance_scrap" in e["breakdown_usd"]   # scrap is a real line


def test_base_plus_T_over_L_preserved_with_secondary_ops():
    # recommend_process fits unit(L)=base+T/L from exactly 2 calls — secondary
    # ops must not introduce a new 1/L power.
    kw = dict(process="cnc_3axis", material="aluminum", finish="anodize",
              n_threaded_holes=0, tolerance_class="fine")
    b = _al_housing()
    u1 = _est(b, lot_size=1, **kw)["unit_cost_usd"]
    u100 = _est(b, lot_size=100, **kw)["unit_cost_usd"]
    T = (u1 - u100) / (1 - 1 / 100)
    base = u1 - T
    assert abs((base + T / 10) - _est(b, lot_size=10, **kw)["unit_cost_usd"]) < 1e-3


def test_injection_skips_tapping_deburr_and_metal_finishes():
    e = _est(_holed_plate(), process="injection_mold_pa", material="abs",
             n_threaded_holes=2, finish="anodize", tolerance_class="fine",
             rates={"deburr_min_per_feature": 0.15})
    for k in ("tapping", "deburr", "finish"):
        assert k not in e["breakdown_usd"]
    assert e["drivers"]["secondary_warnings"]      # the skips are surfaced


def test_sheet_tolerance_has_no_machine_multiplier():
    b = _flat_sheet()
    med = _est(b, process="sheet_laser_brake", material="steel",
               tolerance_class="medium")
    fine = _est(b, process="sheet_laser_brake", material="steel",
                tolerance_class="fine")
    # forming tolerance is fixture/tooling-driven — NO feed-rate multiplier.
    assert med["breakdown_usd"]["laser_cut"] == fine["breakdown_usd"]["laser_cut"]
    # but scrap (and inspection if enabled) still reflect tolerance.
    assert fine["unit_cost_usd"] >= med["unit_cost_usd"]


def test_reliability_unaffected_by_secondary_op_problems():
    # a mis-requested secondary op must NOT downgrade the authoritative primary
    # reliability (recommend_process gates viability on it).
    e = _est(_al_housing(), process="cnc_3axis", material="steel",
             finish="anodize")          # anodize-on-steel mismatch
    assert e["reliability"] == "ok"
    assert "finish_material_mismatch" in e["drivers"]["secondary_warnings"]
