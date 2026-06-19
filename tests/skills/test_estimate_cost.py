"""estimate_cost — honest unit-cost + cycle-time estimate (2026-06-20).

Pins the transparent heuristic: material cost from the REAL measured volume,
process-specific machine/cycle time, amortised setup/tooling, and the right
sensitivities (5-axis > 3-axis, denser material > cheaper, bigger lot → lower
moulding unit cost). result_grade='estimate' — a model, not a quote.
"""
from __future__ import annotations

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
