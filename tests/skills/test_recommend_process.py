"""recommend_process — cost-ranked, viability-gated process advisor.

Synthesises estimate_cost + dfm_verdict + detect_sheet_metal into a ranked
recommendation with cost-vs-volume crossovers. Pins the honest behaviour: grade
'estimate' everywhere; a formed sheet part recommends a SHEET process (not CNC);
sheet routes are not_applicable on a solid block; die_cast/3d_print are advisory,
never recommended.
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.recommend_process import RecommendProcess


def _box():
    return Box().apply(None, {"length_mm": 40.0, "width_mm": 30.0,
                              "height_mm": 20.0}).body


def _rec(body, **kw):
    kw.setdefault("volume_ladder", [1, 1000, 1000000])   # short ladder = fast
    return RecommendProcess().apply(body, kw).extras["process_recommendation"]


def test_grade_estimate_and_advisory_never_recommended():
    r = _rec(_box(), lot_size=1000)
    assert r["grade"] == "estimate"
    assert all(row["grade"] == "estimate" for row in r["ranking"])
    # die_cast / 3d_print are advisories, never cost-ranked or recommended
    assert {a["process"] for a in r["advisories_unpriced"]} <= {"die_cast_al", "3d_printing"}
    ranked = {row["process"] for row in r["ranking"]}
    assert "die_cast_al" not in ranked and "3d_printing" not in ranked
    if r["recommendation"]:
        assert r["recommendation"]["process"] not in ("die_cast_al", "3d_printing")
    assert r["overall_flag"] in {
        "ok", "marginal", "input_unreliable", "no_viable_process",
        "advisory_alternative_exists"}


def test_solid_block_sheet_routes_not_applicable():
    r = _rec(_box(), lot_size=1000)
    # a solid block is NOT sheet metal → all 3 sheet routes excluded not_applicable
    sheet_excl = [x for x in r["excluded"] if x["process"].startswith("sheet")]
    assert len(sheet_excl) == 3
    assert all(x["viability"] == "not_applicable" for x in sheet_excl)
    # a machined route is rankable for a solid block
    assert any(row["process"].startswith("cnc") for row in r["ranking"])


def _l_bracket(t=2.0, r=2.0, La=30.0, Lb=20.0, W=25.0):
    # a formed sheet-metal bracket (1 bend) — fast synthetic stand-in for a real
    # formed shield, so the advisor test does not need the (slow) corpus part
    from build123d import Axis, Box, Pos, fillet
    L = (Pos(La / 2, 0, t / 2) * Box(La, W, t)) + (Pos(t / 2, 0, Lb / 2) * Box(t, W, Lb))
    e = min(L.edges().filter_by(Axis.Y),
            key=lambda e: (e.center().X ** 2 + e.center().Z ** 2))
    return fillet(e, r)


def test_formed_sheet_recommends_sheet_not_cnc():
    # a FORMED sheet part recommends a SHEET process; CNC/injection are
    # not_applicable (cannot machine-from-solid or mould a bent part)
    r = _rec(_l_bracket(), lot_size=1000, material="steel")
    assert r["recommendation"] is not None
    assert r["recommendation"]["process"].startswith("sheet")
    na = {x["process"] for x in r["excluded"] if x["viability"] == "not_applicable"}
    assert "cnc_3axis" in na and "injection_mold_pa" in na


def test_formed_sheet_high_volume_favours_stamping():
    # stamping wins at the top of the volume ladder as tooling amortises
    r = _rec(_l_bracket(), lot_size=1000, material="steel",
             volume_ladder=[1, 1000, 1000000])
    assert r["winner_by_lot"].get("1000000") == "sheet_progressive_die"


def test_precomputes_geometry_drivers_once_for_speed():
    # recommend_process extracts the feature catalog ONCE and injects it into
    # every estimate_cost call (which would otherwise re-run ExtractFeatureCatalog
    # ~0.16s per call). The precompute stage must run and succeed; the cost calls
    # then take ~no extraction time. The recommendation must still be correct.
    r = _rec(_box(), lot_size=1000)
    assert "precompute_drivers" in r["_stages"]
    assert r["_stages"]["precompute_drivers"]["ok"] is True
    # the per-(proc,lot) cost calls no longer re-extract — each is near-instant.
    cost_stages = [v["duration_s"] for k, v in r["_stages"].items()
                   if k.startswith("cost:") and v.get("ok")]
    assert cost_stages, "expected cost stages"
    assert max(cost_stages) < 0.1   # extraction skipped → cost math only
    # recommendation still produced.
    assert r["overall_flag"] in {
        "ok", "marginal", "advisory_alternative_exists", "input_unreliable",
        "no_viable_process"}
