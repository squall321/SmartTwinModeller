"""estimate_cost — inspect macro, read-only (2026-06-20).

Pillar REPORT → turn the rich measured analysis (volume, feature counts, wall
thickness, bbox) into a QUANTIFIED unit-cost + cycle-time ESTIMATE for a chosen
process. This closes the "we measure everything but never put a number on it"
gap — a watch/phone-housing designer cares about $ / part and cycle time.

HONEST by construction (result_grade='estimate'): every rate is a DOCUMENTED
default heuristic (machine $/hr, material $/kg, material-removal rate, per-feature
machining minutes, mould tooling + cooling factors), surfaced in
``assumptions``; the model is a transparent breakdown, not a quote. Override any
rate via ``rates``. Two process families for the domain:

  * CNC milling (cnc_3axis / cnc_5axis) — unit cost = material + machine-time
    (setup + roughing[stock removed / MRR] + Σ per-feature minutes) × rate;
    5-axis applies the catalog ``cost_factor_vs_3axis``.
  * Injection moulding (injection_mold_*) — unit cost = material + (cycle-time ×
    rate) + amortised tooling / lot; cycle time grows with the max wall
    thickness (cooling-dominated).

extras["cost_estimate"] = {
    "process", "material", "lot_size",
    "unit_cost_usd", "cycle_time_s",
    "breakdown_usd": {material, machine|cycle, setup|tooling_amortised, ...},
    "drivers": {volume_cm3, mass_g, n_holes, n_pockets, max_wall_mm, bbox_mm},
    "assumptions": [str, ...],
    "grade": "estimate",
}
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# ── documented default rates (honest heuristics — override via Args.rates) ─────
#: material: (density g/cm3, $/kg)
_MATERIALS: dict[str, tuple[float, float]] = {
    "aluminum": (2.70, 6.0),
    "steel": (7.85, 2.5),
    "stainless": (8.00, 5.0),
    "titanium": (4.43, 35.0),
    "brass": (8.50, 8.0),
    "abs": (1.05, 3.0),
    "pa": (1.14, 4.5),      # polyamide / nylon
    "pc": (1.20, 4.0),
    "pom": (1.41, 3.5),
}

_DEFAULT_RATES: dict[str, float] = {
    "cnc_machine_usd_per_hr": 50.0,
    "cnc_setup_min": 15.0,
    "cnc_mrr_cm3_per_min": 8.0,      # roughing material-removal rate (metal, conservative)
    "cnc_min_per_hole": 0.5,
    "cnc_min_per_pocket": 1.2,
    "cnc_min_per_boss": 0.8,
    "im_machine_usd_per_hr": 40.0,
    "im_tooling_usd": 5000.0,        # amortised over lot_size
    "im_cycle_base_s": 8.0,
    "im_cycle_s_per_mm_wall": 5.0,   # cooling-dominated: thicker wall → longer cycle
    # ── sheet metal (laser+press-brake / turret / progressive-die stamping) ──
    # Fully-burdened US-job-shop 2026 defaults; design-panel + adversarial-rate
    # validated (laser speed is a power law of thickness, NOT linear 1/t).
    "sm_scrap_factor": 1.3,                 # nesting/skeleton waste on material $
    "sm_laser_machine_usd_per_hr": 120.0,
    "sm_laser_ref_speed_mm_per_min": 10000.0,   # contour speed @ 1mm mild steel
    "sm_laser_ref_thickness_mm": 1.0,
    "sm_laser_speed_exp": 0.8,              # speed(t)=ref×(ref_t/t)^exp (falls < 1/t)
    "sm_laser_max_speed_mm_per_min": 18000.0,   # thin-gauge accel ceiling
    "sm_laser_min_speed_mm_per_min": 500.0,     # heavy-plate floor
    "sm_pierce_s_per_hole": 0.5,
    "sm_laser_setup_min": 5.0,              # /lot
    "sm_laser_handling_s_per_part": 8.0,    # load/unload, its OWN breakdown line
    "sm_brake_machine_usd_per_hr": 80.0,
    "sm_brake_setup_min": 18.0,             # /lot, only when formed
    "sm_brake_s_per_bend": 8.0,             # per bend AND per hem (a hit)
    "sm_brake_handling_s_per_part": 12.0,   # once, when n_forms>=1
    "sm_turret_machine_usd_per_hr": 95.0,
    "sm_turret_s_per_hit": 0.3,
    "sm_nibble_pitch_mm": 3.0,              # turret outline step
    "sm_stamp_machine_usd_per_hr": 110.0,
    "sm_stamp_s_per_part": 0.5,             # one finished part per stroke
    "sm_stamp_tooling_base_usd": 8000.0,    # progressive die: base + per-station
    "sm_stamp_tooling_usd_per_station": 4000.0,
    "sm_min_cut_s": 2.0,                    # per-part cut floor
    "sm_perimeter_complexity_factor": 1.2,  # outline est is a lower bound
}


def _density_and_price(material: str) -> tuple[float, float, str]:
    key = (material or "aluminum").strip().lower()
    if key in _MATERIALS:
        d, p = _MATERIALS[key]
        return d, p, key
    return _MATERIALS["aluminum"][0], _MATERIALS["aluminum"][1], f"{key}→aluminum(default)"


def _is_cnc(process: str) -> bool:
    return "cnc" in process or "mill" in process


def _is_injection(process: str) -> bool:
    return "inject" in process or "mold" in process or "mould" in process


def _is_sheet(process: str) -> bool:
    return any(k in process for k in (
        "sheet", "laser", "brake", "stamp", "turret", "punch", "progressive"))


def _laser_speed_mm_min(thickness_mm: float, R: dict) -> float:
    """Fiber-laser contour speed as a power law of thickness (real speed falls
    SLOWER than 1/t), clamped to a thin-gauge ceiling + heavy-plate floor."""
    t = max(thickness_mm, 0.05)
    spd = (R["sm_laser_ref_speed_mm_per_min"]
           * (R["sm_laser_ref_thickness_mm"] / t) ** R["sm_laser_speed_exp"])
    return max(R["sm_laser_min_speed_mm_per_min"],
               min(spd, R["sm_laser_max_speed_mm_per_min"]))


def _cut_perimeter_mm(sm: dict, R: dict, assumptions: list) -> float | None:
    """OUTLINE cut-perimeter estimate (a LOWER BOUND), 4-tier fallback keyed on
    which detect_sheet_metal fields are populated. Holes are charged as pierces,
    not summed into the contour."""
    fp = sm.get("flat_pattern") or {}
    area = fp.get("flat_blank_area_mm2")
    dev = fp.get("developed_length_mm")
    width = fp.get("blank_width_mm")
    bbox = sm.get("bbox_mm")
    base = None
    if dev and width:
        base, tier = 2.0 * (dev + width), "T1 developed_length×width rectangle"
    elif area and width:
        base, tier = 2.0 * (area / width + width), "T2 area/width rectangle"
    elif area:
        base, tier = 4.0 * math.sqrt(area), "T3 √area square (under-reads long-thin)"
    elif bbox and len(bbox) >= 2:
        s = sorted([float(v) for v in bbox], reverse=True)
        base, tier = 2.0 * (s[0] + s[1]), "T4 bbox face (bend development ignored)"
    else:
        return None
    assumptions.append(
        f"cut-perimeter {tier} ×{R['sm_perimeter_complexity_factor']} complexity "
        "(outline only; holes pierced; a LOWER BOUND — notched outlines are longer).")
    return base * R["sm_perimeter_complexity_factor"]


def _sheet_cost(sm: dict, proc: str, density: float, price_per_kg: float,
                R: dict, lot: int, n_holes: int, volume_cm3: float,
                reliability: str, assumptions: list):
    """Sheet-metal cost: laser+brake (default) / turret+brake / progressive-die
    stamping. Returns (breakdown, cycle_time_s, reliability, drivers_extra)."""
    is_sm = sm.get("is_sheet_metal")
    t = sm.get("thickness_mm")
    n_bends = int(sm.get("n_bends") or 0)
    n_hems = int(sm.get("n_hems") or 0)
    n_forms = n_bends + n_hems
    fp = sm.get("flat_pattern") or {}
    blank_area = fp.get("flat_blank_area_mm2")
    bbox = sm.get("bbox_mm")
    extra: dict = {}

    if (not is_sm) or sm.get("skipped") or t is None:
        reliability = "not_sheet_metal"
        assumptions.insert(0, "SHEET estimate INVALID — part NOT recognised as sheet "
                           "metal; cost it with the CNC/injection model instead "
                           "(this breakdown is emitted only so it is inspectable).")

    # material from the developed blank (× scrap), with honest fallbacks
    if blank_area and t:
        sheet_vol_cm3 = blank_area * t / 1000.0
    elif bbox and t and len(bbox) >= 2:
        s = sorted([float(v) for v in bbox], reverse=True)
        sheet_vol_cm3 = s[0] * s[1] * t / 1000.0
        assumptions.append("blank area unknown → bbox face × thickness.")
        if reliability == "ok":
            reliability = "degenerate_geometry"
    else:
        sheet_vol_cm3 = max(volume_cm3, 0.0)
        if reliability == "ok":
            reliability = "degenerate_geometry"
    material_usd = round(sheet_vol_cm3 * density / 1000.0 * price_per_kg
                         * R["sm_scrap_factor"], 4)
    breakdown: dict = {"material": material_usd}

    perim = _cut_perimeter_mm(sm, R, assumptions)
    if perim is None:
        perim = 4.0 * math.sqrt(max(sheet_vol_cm3 * 1000.0 / max(t or 1.0, 0.05), 1.0))

    def _brake(bd):
        if n_forms > 0:
            run = n_forms * R["sm_brake_s_per_bend"] + R["sm_brake_handling_s_per_part"]
            setup = R["sm_brake_setup_min"] * 60.0 / max(lot, 1)
            bd["press_brake"] = round((run + setup) / 3600.0
                                      * R["sm_brake_machine_usd_per_hr"], 4)
            return run
        return 0.0

    if "stamp" in proc or "progressive" in proc:
        n_stations = max(2, n_bends + n_hems + math.ceil(n_holes / 2) + 2)
        tooling = (R["sm_stamp_tooling_base_usd"]
                   + n_stations * R["sm_stamp_tooling_usd_per_station"])
        breakdown["stamp_machine"] = round(
            R["sm_stamp_s_per_part"] / 3600.0 * R["sm_stamp_machine_usd_per_hr"], 4)
        breakdown["tooling_amortised"] = round(tooling / max(lot, 1), 4)
        cycle_time_s = round(R["sm_stamp_s_per_part"], 1)
        if lot < 1000 and reliability == "ok":
            reliability = "below_scale"
        assumptions.append(
            f"stamping: {n_stations}-station die ${tooling:.0f}/{lot} "
            f"= ${breakdown['tooling_amortised']}; cycle {R['sm_stamp_s_per_part']}s "
            "(forms all features in one stroke, independent of n_bends).")
    elif "turret" in proc or "punch" in proc:
        hits = n_holes + math.ceil(perim / R["sm_nibble_pitch_mm"])
        run = max(hits * R["sm_turret_s_per_hit"], R["sm_min_cut_s"])
        setup = R["sm_laser_setup_min"] * 60.0 / max(lot, 1)
        breakdown["turret_punch"] = round((run + setup) / 3600.0
                                          * R["sm_turret_machine_usd_per_hr"], 4)
        breakdown["handling"] = round(R["sm_laser_handling_s_per_part"] / 3600.0
                                      * R["sm_turret_machine_usd_per_hr"], 4)
        brun = _brake(breakdown)
        cycle_time_s = round(run + R["sm_laser_handling_s_per_part"] + brun, 1)
        assumptions.append(
            f"turret: {hits} hits ({n_holes} holes + nibbled outline @"
            f"{R['sm_nibble_pitch_mm']}mm) — nibbled edge is stair-stepped, prefer "
            "laser for cosmetic/irregular outlines.")
    else:  # laser + press-brake (default sheet route)
        spd = _laser_speed_mm_min(t or 1.0, R)
        cut_s = perim / spd * 60.0
        pierce_s = (n_holes + 1) * R["sm_pierce_s_per_hole"]
        run = max(cut_s + pierce_s, R["sm_min_cut_s"])
        setup = R["sm_laser_setup_min"] * 60.0 / max(lot, 1)
        breakdown["laser_cut"] = round((run + setup) / 3600.0
                                       * R["sm_laser_machine_usd_per_hr"], 4)
        breakdown["handling"] = round(R["sm_laser_handling_s_per_part"] / 3600.0
                                      * R["sm_laser_machine_usd_per_hr"], 4)
        brun = _brake(breakdown)
        cycle_time_s = round(run + R["sm_laser_handling_s_per_part"] + brun, 1)
        # brake-infeasible guard: many folds on a tiny body → a hand-brake route
        # is a costing fiction (the 12-bend USB-A shield); point to stamping.
        if (n_forms >= 6 and bbox and min(float(v) for v in bbox) < 15.0
                and reliability == "ok"):
            reliability = "brake_infeasible_forming"
            assumptions.append(
                f"brake-infeasible: {n_forms} forms on a <15mm body — a hand-brake "
                "route is a costing fiction; use progressive-die/multi-slide "
                "(see sheet_progressive_die).")
        # stamping crossover (decision aid)
        n_stations = max(2, n_bends + n_hems + math.ceil(n_holes / 2) + 2)
        tooling = (R["sm_stamp_tooling_base_usd"]
                   + n_stations * R["sm_stamp_tooling_usd_per_station"])
        stamp_unit = (material_usd
                      + R["sm_stamp_s_per_part"] / 3600.0 * R["sm_stamp_machine_usd_per_hr"])
        lb_unit = sum(breakdown.values())
        denom = lb_unit - stamp_unit
        crossover = int(tooling / denom) if denom > 1e-6 else None
        extra["stamping_crossover_lot"] = crossover
        assumptions.append(
            f"laser+brake: speed {round(spd)}mm/min@{t}mm, cut {round(cut_s, 1)}s + "
            f"{round(pierce_s, 1)}s pierce; {n_forms} forms×{R['sm_brake_s_per_bend']}s. "
            + (f"Stamping wins above lot ~{crossover}." if crossover else
               "Stamping crossover N/A."))

    if sm.get("flat_unformed"):
        assumptions.append("flat unformed blank — could be a laser-cut plate, a "
                           "machined plate or a thin moulding (geometry ambiguous).")
    if (sm.get("confidence") or 1.0) < 0.5:
        assumptions.append(
            f"low sheet-metal confidence ({sm.get('confidence')}) — treat as indicative.")
    return breakdown, cycle_time_s, reliability, extra


@skill(
    name="estimate_cost",
    category="inspect",
    level="macro",
    summary="Estimate unit cost (USD) + cycle time for a part in a chosen process "
            "(CNC milling / injection moulding) from its measured volume, feature "
            "counts and wall thickness. Transparent heuristic breakdown with "
            "documented default rates (override via `rates`); result_grade="
            "'estimate' — a model, not a quote.",
    selector_kinds=[],
    history_rules={},
    produces_features=["cost_estimate"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.4,
    result_grade="estimate",
    post_conditions=[PostCondition(kind="body_present")],
)
class EstimateCost(SkillBase):
    class Args(BaseModel):
        process: str = Field(
            default="cnc_3axis",
            description="cnc_3axis | cnc_5axis | injection_mold_pa | cnc_milling | "
                        "sheet_laser_brake (default sheet) | sheet_turret_brake | "
                        "sheet_progressive_die (stamping)",
        )
        material: str = Field(
            default="aluminum",
            description="aluminum | steel | stainless | titanium | brass | abs | pa | pc | pom",
        )
        lot_size: int = Field(
            default=1000, ge=1,
            description="Production lot — amortises CNC setup / injection tooling.",
        )
        rates: dict[str, float] = Field(
            default_factory=dict,
            description="Override any default rate (see _DEFAULT_RATES keys).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.inspect.mass_properties import MassProperties
        from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
            ExtractFeatureCatalog,
        )

        R = dict(_DEFAULT_RATES)
        R.update({k: float(v) for k, v in (args.rates or {}).items() if k in R})
        density, price_per_kg, mat_label = _density_and_price(args.material)
        proc = (args.process or "cnc_3axis").strip().lower()
        assumptions: list[str] = []

        # ── measured drivers ──────────────────────────────────────────────────
        volume_cm3 = 0.0
        bbox = None
        try:
            # mass_properties extras is FLAT (volume_mm3 at top level), no bbox.
            mp = MassProperties().apply(body, {"density_g_per_cm3": density}).extras
            volume_cm3 = float(mp.get("volume_mm3") or 0.0) / 1000.0
        except Exception as exc:  # noqa: BLE001
            assumptions.append(f"mass_properties failed ({type(exc).__name__}); volume=0")
        # ── input-scale validation: the cost model only applies to a single
        # machinable/mouldable part of plausible size. Flag (don't silently emit
        # a confident number for) a degenerate, micro, or assembly-scale input.
        reliability = "ok"
        if volume_cm3 <= 0.0:
            reliability = "degenerate_geometry"
            assumptions.append(
                f"non-positive volume ({round(volume_cm3,4)}cm³) — open/inverted "
                "shell or non-solid; cost is UNRELIABLE.")
            volume_cm3 = abs(volume_cm3)   # keep the math finite for the breakdown
        elif volume_cm3 < 0.01:
            reliability = "below_scale"
            assumptions.append(
                f"tiny part ({round(volume_cm3,5)}cm³) — below the scale of the "
                "modelled processes (CNC/IM); cost is indicative only.")
        elif volume_cm3 > 1.0e6:
            reliability = "above_scale"
            assumptions.append(
                f"very large volume ({round(volume_cm3,0)}cm³, >1m³) — likely an "
                "ASSEMBLY, not a single machined part; cost is UNRELIABLE.")
        mass_g = volume_cm3 * density

        n_holes = n_pockets = n_bosses = 0
        max_wall_mm = None
        try:
            cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
            n_holes = len(cat.get("holes") or [])
            n_pockets = len(cat.get("pockets") or [])
            n_bosses = len(cat.get("bosses") or [])
            max_wall_mm = cat.get("base_thickness_mm")
            bbox = cat.get("initial_bbox_mm")  # 6-tuple world bbox → CNC stock block
        except Exception as exc:  # noqa: BLE001
            assumptions.append(f"feature_catalog failed ({type(exc).__name__}); counts=0")

        # bbox stock volume (CNC starts from a block) — fall back to 1.3× part vol
        stock_cm3 = volume_cm3 * 1.3
        if isinstance(bbox, dict) and bbox.get("size"):
            sz = bbox["size"]
            stock_cm3 = (float(sz[0]) * float(sz[1]) * float(sz[2])) / 1000.0
        elif isinstance(bbox, (list, tuple)) and len(bbox) >= 6:
            stock_cm3 = (
                (float(bbox[3]) - float(bbox[0]))
                * (float(bbox[4]) - float(bbox[1]))
                * (float(bbox[5]) - float(bbox[2]))) / 1000.0

        material_usd = round(mass_g / 1000.0 * price_per_kg, 4)
        breakdown: dict[str, float] = {"material": material_usd}
        cycle_time_s = None
        sheet_extra: dict = {}

        if _is_sheet(proc):
            from phone_designer.skills.inspect.detect_sheet_metal import (
                DetectSheetMetal,
            )
            sm = {}
            try:
                sm = DetectSheetMetal().apply(body, {}).extras["sheet_metal"]
            except Exception as exc:  # noqa: BLE001
                assumptions.append(
                    f"detect_sheet_metal failed ({type(exc).__name__})")
            breakdown, cycle_time_s, reliability, sheet_extra = _sheet_cost(
                sm, proc, density, price_per_kg, R, args.lot_size, n_holes,
                volume_cm3, reliability, assumptions)
        elif _is_injection(proc):
            wall = float(max_wall_mm) if max_wall_mm else 2.0
            if not max_wall_mm:
                assumptions.append("wall thickness unknown → assumed 2.0mm")
            cycle_time_s = R["im_cycle_base_s"] + wall * R["im_cycle_s_per_mm_wall"]
            cycle_usd = round(cycle_time_s / 3600.0 * R["im_machine_usd_per_hr"], 4)
            tooling_amortised = round(R["im_tooling_usd"] / max(args.lot_size, 1), 4)
            breakdown["cycle"] = cycle_usd
            breakdown["tooling_amortised"] = tooling_amortised
            assumptions.append(
                f"injection: cycle={round(cycle_time_s,1)}s "
                f"(base {R['im_cycle_base_s']}s + {wall}mm wall × "
                f"{R['im_cycle_s_per_mm_wall']}s); tooling ${R['im_tooling_usd']:.0f}"
                f"/{args.lot_size} = ${tooling_amortised}")
        else:  # CNC (default)
            if not _is_cnc(proc):
                assumptions.append(f"unknown process '{proc}' → CNC model")
            removed_cm3 = max(stock_cm3 - volume_cm3, 0.0)
            rough_min = removed_cm3 / max(R["cnc_mrr_cm3_per_min"], 0.1)
            feat_min = (n_holes * R["cnc_min_per_hole"]
                        + n_pockets * R["cnc_min_per_pocket"]
                        + n_bosses * R["cnc_min_per_boss"])
            setup_min_per_part = R["cnc_setup_min"] / max(args.lot_size, 1)
            machine_min = rough_min + feat_min
            cycle_time_s = round(machine_min * 60.0, 1)
            factor = 1.0
            if "5" in proc:
                factor = 2.5  # catalog cnc_5axis cost_factor_vs_3axis
                assumptions.append("5-axis: ×2.5 machine-cost factor (catalog)")
            machine_usd = round(machine_min / 60.0 * R["cnc_machine_usd_per_hr"] * factor, 4)
            setup_usd = round(setup_min_per_part / 60.0 * R["cnc_machine_usd_per_hr"], 4)
            breakdown["machine"] = machine_usd
            breakdown["setup_amortised"] = setup_usd
            assumptions.append(
                f"CNC: rough {round(rough_min,1)}min ({round(removed_cm3,1)}cm³ "
                f"removed / {R['cnc_mrr_cm3_per_min']} MRR) + feat {round(feat_min,1)}min "
                f"({n_holes}h+{n_pockets}p+{n_bosses}b); ${R['cnc_machine_usd_per_hr']}/hr; "
                f"setup {R['cnc_setup_min']}min/{args.lot_size}")

        unit_cost = round(sum(breakdown.values()), 4)
        assumptions.append(f"material {mat_label}: {density}g/cm³ × ${price_per_kg}/kg")

        out = {
            "process": proc,
            "material": mat_label,
            "lot_size": args.lot_size,
            "reliability": reliability,
            "unit_cost_usd": unit_cost,
            "cycle_time_s": cycle_time_s,
            "breakdown_usd": breakdown,
            "drivers": {
                "volume_cm3": round(volume_cm3, 3),
                "mass_g": round(mass_g, 3),
                "stock_cm3": round(stock_cm3, 3),
                "n_holes": n_holes,
                "n_pockets": n_pockets,
                "n_bosses": n_bosses,
                "max_wall_mm": max_wall_mm,
                **sheet_extra,
            },
            "assumptions": assumptions,
            "grade": "estimate",
        }
        return SkillResult(body=body, history=EntityHistoryMap(),
                           extras={"cost_estimate": out})
