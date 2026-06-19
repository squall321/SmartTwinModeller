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
            description="cnc_3axis | cnc_5axis | injection_mold_pa | injection_molding | cnc_milling",
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

        if _is_injection(proc):
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
            },
            "assumptions": assumptions,
            "grade": "estimate",
        }
        return SkillResult(body=body, history=EntityHistoryMap(),
                           extras={"cost_estimate": out})
