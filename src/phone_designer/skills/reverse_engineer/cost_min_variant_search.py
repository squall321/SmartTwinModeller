"""cost_min_variant_search — macro, read-only (Pillar VARIANTS, A2).

Sweep a named parametric driver, REBUILD the part at each value, PRICE each
rebuild, and return the CHEAPEST variant that is still VALID (rebuilds cleanly)
AND GENUINELY manufacturable. Answers: "what is the cheapest version of this
part that still works?"

Composes (reinvents nothing):
  - generate_variant_family._build_one  — the variant-body rebuild
    (apply_variant_drivers, relation-propagated -> vary -> normalize -> plan ->
    PlanExecutor.run()). Returns (plan_dict, rebuilt_body, drivers_unresolved).
  - generate_variant_family._validate / _fidelity_vs_step / _volume_mm3 /
    _write_step / resolve_sweep_values — validity + fidelity + bookkeeping.
  - recommend_process — per-variant cost + viability (already gates DFM + sheet,
    never recommends a hard-infeasible process).
  - repair_dfm — OPT-IN (repair_dfm=True): on a valid-but-NOT-genuinely-viable
    variant, auto-fix DFM, re-price the repaired body.

AIRTIGHT VIABILITY GATE (the central correctness point — validators caught all
three base designs leaking here): recommend_process returns a NON-None
recommendation for the cheapest in the best AVAILABLE tier, including
``viable_marginal`` (DFM threshold-miss / below-scale) and ``unproven`` (DFM
skipped). The cheapest variant is TYPICALLY the thinnest/smallest — exactly the
geometry most likely to be DFM-marginal — so admitting marginal/unproven would
crown a barely-makeable winner. COMPETITION therefore requires the strict
``recommendation['viability'] == 'viable'`` tier. Marginal/unproven variants are
still RECORDED (viability_tier surfaced) but cannot WIN.

HONESTY: fidelity is hausdorff vs the value[0] REBUILD (relative, not vs the
original STEP — the base recon is imperfect), labeled accordingly. The cheapest
variant typically deviates MOST from intent, so cost AND fidelity are reported
together; an absolute low-fidelity warning fires even with max_deviation_mm
unset; closest_viable (most faithful viable competitor) is returned beside the
winner. winner=None with a distinct overall_flag when nothing genuinely competes
— never invent a winner. grade='estimate' everywhere (cost a model, not a quote).

Read-only in BOTH modes: each variant rebuilds its OWN body and discards it
(the repaired body too); the anchor body is returned untouched.

extras['cost_variant_search'] — see _apply / the test module.
"""
from __future__ import annotations

import os
import tempfile
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult

SCHEMA_VERSION = 1

# Cost-candidate KEY -> {dfm_name, tooling_class}, single-sourced from
# recommend_process._CANDIDATES so the mapping never drifts.
from phone_designer.skills.inspect.recommend_process import _CANDIDATES  # noqa: E402

_KEY2DFM = {c[0]: c[2] for c in _CANDIDATES}
_KEY2TOOL = {c[0]: c[3] for c in _CANDIDATES}
_ALL_KEYS = [c[0] for c in _CANDIDATES if c[4]]  # costable candidate keys
# tooling simplicity ordinal (fewer/cheaper tooling first) for the tie-break.
_TOOL_ORDINAL = {"none": 0, "machined": 1, "soft_tool": 2, "hard_tool": 3}


def _occt(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


@skill(
    name="cost_min_variant_search",
    category="reverse_engineer",
    level="macro",
    summary="Sweep a named driver, REBUILD + PRICE each variant, return the "
            "CHEAPEST GENUINELY-VIABLE one (strict 'viable' tier — marginal/"
            "unproven recorded but cannot win). Each value: _build_one rebuild "
            "-> validate + fidelity (hausdorff vs the value[0] rebuild) -> "
            "recommend_process (cost + viability; DFM/sheet gated internally). "
            "Cheapest typically deviates MOST from intent, so cost AND fidelity "
            "are reported together (closest_viable beside the winner); an "
            "absolute low-fidelity warning fires even with max_deviation_mm "
            "unset; winner=None with a distinct overall_flag when nothing "
            "competes (never invent a winner). repair_dfm=True OPT-IN chains the "
            "auto-fix on valid-but-not-viable variants. Read-only (rebuilds + "
            "discards each variant); grade='estimate' (a model, not a quote). "
            "Aggregate cost ~ n_valid x (rebuild + recommend_process).",
    selector_kinds=[],
    history_rules={},
    produces_features=["cost_variant_search"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.driver_unresolved", "fm.all_invalid"],
    cost_hint=12.0,  # up-to-16 valid variants x (rebuild + recommend_process@3.0)
    result_grade="estimate",
    post_conditions=[PostCondition(kind="body_present")],
)
class CostMinVariantSearch(SkillBase):
    class Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

        catalog: dict | None = Field(
            default=None,
            description="feature_catalog to vary. None -> the last catalog "
                        "shared by PlanFromFeatureCatalog (same fallback as "
                        "generate_variant_family).")
        driver: str = Field(
            ...,
            description="The single driver to sweep — a key_dimensions name "
                        "(identify_key_dimensions) or a raw vary_feature_catalog "
                        "dotted_key.")
        values: list | None = Field(
            default=None,
            description="Explicit ABSOLUTE target values. Supply exactly one of "
                        "values / sweep.")
        sweep: dict | None = Field(
            default=None,
            description="{'start','stop','steps'} -> steps (>=2) evenly spaced "
                        "absolute values. Supply exactly one of values / sweep.")
        lot_size: int = Field(default=1000, ge=1,
                              description="Production lot size -> recommend_process.")
        material: str = Field(default="aluminum",
                             description="Material -> recommend_process / estimate_cost.")
        processes: list[str] | None = Field(
            default=None,
            description="Subset of recommend_process candidate KEYS "
                        f"{_ALL_KEYS}. None -> all. Unknown names RAISE "
                        "(a typo is a bug, not a silently-dropped extra).")
        max_deviation_mm: float | None = Field(
            default=None, gt=0.0,
            description="HARD fidelity gate: a variant whose fidelity vs the "
                        "value[0] rebuild exceeds this is recorded but cannot win.")
        repair_dfm: bool = Field(
            default=False,
            description="OPT-IN: chain repair_dfm on a valid-but-NOT-genuinely-"
                        "viable variant, then re-price the repaired body. Off by "
                        "default (it mutates geometry + muddies fidelity).")
        repair_max_hausdorff_mm: float | None = Field(
            default=None, gt=0.0,
            description="-> repair_dfm.max_hausdorff_mm (the repair's geometry "
                        "ceiling). None -> repair_dfm's own default.")
        rates: dict = Field(default_factory=dict,
                           description="Rate overrides -> recommend_process.")
        pull_direction: list[float] = Field(
            default=[0.0, 0.0, 1.0], min_length=3, max_length=3,
            description="Mold-open / tool axis -> recommend_process (+ repair_dfm).")
        base_step_kind: str = Field(default="box",
                                   description="-> plan_from_feature_catalog.")
        relations: list | None = Field(
            default=None,
            description="design_relations. None -> recovered from the catalog once.")
        constraints: dict | None = Field(default=None,
                                        description="pass-through to _build_one.")
        max_variants: int = Field(
            default=16, ge=2, le=24,
            description="HARD sweep cap. Over-cap RAISES (never silently truncate).")
        fidelity_linear_deflection_mm: float = Field(
            default=0.1, gt=0.0,
            description="Tessellation chord tolerance for the fidelity hausdorff.")
        write_plans: bool = Field(
            default=True,
            description="Write each variant plan to its own temp file so "
                        "plans/reconstructed_plan.yaml is never clobbered.")

        @field_validator("processes")
        @classmethod
        def _known_processes(cls, v):
            if v is None:
                return v
            bad = [p for p in v if p not in _ALL_KEYS]
            if bad:
                raise ValueError(
                    f"unknown process key(s) {bad}; known costable keys: {_ALL_KEYS}")
            return v

    # ──────────────────────────────────────────────────────────────────────────
    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.reverse_engineer.generate_variant_family import (
            _build_one,
            _fidelity_vs_step,
            _validate,
            _volume_mm3,
            _write_step,
            resolve_sweep_values,
        )
        from phone_designer.skills.reverse_engineer.identify_key_dimensions import (
            identify_key_dimensions,
        )
        from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
            PlanFromFeatureCatalog,
        )
        from phone_designer.skills.reverse_engineer.recover_design_relations import (
            recover_design_relations,
        )

        stages: dict[str, dict] = {}

        def _stage(name, fn):
            t0 = time.perf_counter()
            try:
                r = fn()
                stages[name] = {"ok": True,
                                "duration_s": round(time.perf_counter() - t0, 3)}
                return r
            except Exception as exc:
                stages[name] = {"ok": False,
                                "duration_s": round(time.perf_counter() - t0, 3),
                                "error": f"{type(exc).__name__}: {exc}"}
                raise

        catalog = args.catalog
        if catalog is None:
            catalog = getattr(PlanFromFeatureCatalog, "_LAST_CATALOG", None)
        catalog = catalog or {}

        values = resolve_sweep_values(args.values, args.sweep)
        if len(values) > args.max_variants:
            raise ValueError(
                f"sweep has {len(values)} values > max_variants={args.max_variants}; "
                "raise max_variants or shorten the sweep (never silently truncated)")

        key_dims = identify_key_dimensions(catalog)
        by_name = {e["name"]: e for e in key_dims}
        is_name = args.driver in by_name
        driver_role = by_name[args.driver].get("role") if is_name else None

        relations = args.relations
        if relations is None:
            relations = recover_design_relations(catalog)

        candidate_keys = args.processes if args.processes is not None else _ALL_KEYS

        baseline_value = values[0]
        tmpdir = tempfile.mkdtemp(prefix="cost_variant_")
        baseline_step: str | None = None

        variants: list[dict] = []
        defl = args.fidelity_linear_deflection_mm

        for idx, value in enumerate(values):
            rec_var = self._eval_variant(
                idx, value, body, catalog, relations, args, is_name,
                candidate_keys, baseline_step, defl, tmpdir,
                _build_one, _validate, _fidelity_vs_step, _volume_mm3, _write_step,
            )
            # capture the baseline STEP from idx 0 for downstream fidelity.
            if idx == 0 and rec_var.get("_baseline_step"):
                baseline_step = rec_var.pop("_baseline_step")
            else:
                rec_var.pop("_baseline_step", None)
            variants.append(rec_var)

        out = self._assemble(variants, values, baseline_value, args, driver_role,
                             is_name, baseline_step, body)
        out["_stages"] = stages
        return SkillResult(body=body, history=EntityHistoryMap(),
                           extras={"cost_variant_search": out})

    # ──────────────────────────────────────────────────────────────────────────
    def _eval_variant(self, idx, value, body, catalog, relations, args, is_name,
                      candidate_keys, baseline_step, defl, tmpdir,
                      _build_one, _validate, _fidelity_vs_step, _volume_mm3,
                      _write_step) -> dict:
        rec: dict[str, Any] = {
            "value": round(float(value), 6), "valid": False, "viable": False,
            "competitor": False, "unit_cost_usd": None, "recommended_process": None,
            "viability_tier": None, "tooling_class": None,
            "recommend_overall_flag": None, "fidelity_vs_baseline_mm": None,
            "volume_mm3": None, "features_expected": None, "features_lost": None,
            "brep_valid": None, "drivers_unresolved": [], "excluded_by_fidelity": False,
            "excluded_reason": None, "dfm_repaired": False, "repair_grade": None,
            "error": None, "_baseline_step": None,
        }
        plan_out = (os.path.join(tmpdir, f"variant_{idx}.yaml")
                    if args.write_plans else None)
        try:
            plan_dict, rebuilt, unresolved = _build_one(
                body, catalog, relations, args.driver, is_name, value,
                args.constraints, args.base_step_kind, plan_out)
        except Exception as exc:
            rec["error"] = f"rebuild error: {type(exc).__name__}: {exc}"
            return rec
        rec["drivers_unresolved"] = list(unresolved or [])
        if rebuilt is None:
            rec["error"] = "rebuild produced no body"
            return rec

        try:
            feat_exp, feat_lost, brep_valid = _validate(rebuilt, plan_dict)
        except Exception:
            feat_exp, feat_lost, brep_valid = None, None, None
        rec["features_expected"] = feat_exp
        rec["features_lost"] = feat_lost
        rec["brep_valid"] = brep_valid
        rec["valid"] = (feat_lost == 0 and brep_valid is True)
        rec["volume_mm3"] = _volume_mm3(rebuilt)

        # fidelity: idx0 is the anchor (write it, fidelity 0); others vs anchor.
        if idx == 0:
            step = os.path.join(tmpdir, "baseline.step")
            if rec["valid"] and _write_step(rebuilt, step):
                rec["_baseline_step"] = step
                rec["fidelity_vs_baseline_mm"] = 0.0
        elif baseline_step is not None:
            rec["fidelity_vs_baseline_mm"] = _fidelity_vs_step(
                rebuilt, baseline_step, defl)

        if not rec["valid"]:
            if rec["error"] is None:
                rec["error"] = (
                    f"invalid rebuild (features_lost={feat_lost}, brep_valid={brep_valid})")
            return rec

        # HARD fidelity gate — before any cost call. None-guarded.
        fid = rec["fidelity_vs_baseline_mm"]
        if args.max_deviation_mm is not None:
            if fid is None:
                rec["excluded_by_fidelity"] = True
                rec["excluded_reason"] = "fidelity unmeasurable (baseline anchor failed)"
                return rec
            if fid > args.max_deviation_mm:
                rec["excluded_by_fidelity"] = True
                rec["excluded_reason"] = (
                    f"fidelity {fid:.3f} mm > max_deviation_mm {args.max_deviation_mm} mm")
                return rec

        # price the variant.
        self._price(rec, rebuilt, args, candidate_keys, baseline_step, defl,
                    _fidelity_vs_step)
        return rec

    def _price(self, rec, rebuilt, args, candidate_keys, baseline_step, defl,
               _fidelity_vs_step):
        from phone_designer.skills.inspect.recommend_process import RecommendProcess

        def _recommend(b):
            return RecommendProcess().apply(b, {
                "material": args.material, "lot_size": args.lot_size,
                "pull_direction": [float(c) for c in args.pull_direction],
                "candidates": list(candidate_keys), "rates": dict(args.rates),
            }).extras["process_recommendation"]

        try:
            block = _recommend(rebuilt)
        except Exception as exc:
            rec["viable"] = False
            rec["error"] = f"viability input_unreliable: {type(exc).__name__}: {exc}"
            rec["recommend_overall_flag"] = "input_unreliable"
            return
        self._absorb(rec, block)

        genuine = self._genuine(block)
        # OPT-IN repair on a valid-but-not-genuinely-viable variant.
        if (args.repair_dfm and not genuine
                and block.get("overall_flag") != "input_unreliable"):
            repaired = self._try_repair(rebuilt, args, candidate_keys)
            if repaired is not None:
                rec["dfm_repaired"] = True
                if baseline_step is not None:
                    rec["fidelity_vs_baseline_mm"] = _fidelity_vs_step(
                        _occt(repaired), baseline_step, defl)
                try:
                    block = _recommend(repaired)
                    self._absorb(rec, block)
                    genuine = self._genuine(block)
                except Exception as exc:
                    rec["error"] = f"post-repair pricing failed: {exc}"

        rec["viable"] = genuine

    def _absorb(self, rec, block):
        rectn = block.get("recommendation")
        rec["recommend_overall_flag"] = block.get("overall_flag")
        if rectn:
            rec["recommended_process"] = rectn.get("process")
            rec["viability_tier"] = rectn.get("viability")
            rec["unit_cost_usd"] = rectn.get("unit_cost_usd")
            rec["tooling_class"] = _KEY2TOOL.get(rectn.get("process"))

    @staticmethod
    def _genuine(block) -> bool:
        r = block.get("recommendation")
        return bool(r and r.get("viability") == "viable"
                    and block.get("overall_flag") != "input_unreliable")

    def _try_repair(self, rebuilt, args, candidate_keys):
        from phone_designer.skills.repair.repair_dfm import RepairDfm
        dfm_procs = sorted({_KEY2DFM[k] for k in candidate_keys if _KEY2DFM.get(k)})
        if not dfm_procs:
            raise ValueError(
                f"repair_dfm: no DFM process maps from candidate keys {candidate_keys}")
        rargs = {"processes": dfm_procs,
                 "pull_direction": [float(c) for c in args.pull_direction],
                 "apply": True}
        if args.repair_max_hausdorff_mm is not None:
            rargs["max_hausdorff_mm"] = args.repair_max_hausdorff_mm
        try:
            res = RepairDfm().apply(rebuilt, rargs)
        except Exception:
            return None
        dfm = res.extras.get("dfm_repair", {})
        if dfm.get("grade") in ("repaired", "partial") and dfm.get("body_changed"):
            return res.body
        return None

    # ──────────────────────────────────────────────────────────────────────────
    def _assemble(self, variants, values, baseline_value, args, driver_role,
                  is_name, baseline_step, body) -> dict:
        competitors = [v for v in variants if v["valid"] and v["viable"]
                       and v["unit_cost_usd"] is not None
                       and not v["excluded_by_fidelity"]]
        # mark the competitor flag.
        for v in competitors:
            v["competitor"] = True

        n_valid = sum(1 for v in variants if v["valid"])
        n_viable = sum(1 for v in variants if v["viable"])
        n_marginal = sum(1 for v in variants if v["valid"] and not v["viable"]
                         and v["recommended_process"] is not None
                         and v["viability_tier"] in ("viable_marginal", "unproven"))
        n_excl_fid = sum(1 for v in variants if v["excluded_by_fidelity"])

        def _fid(v):
            f = v["fidelity_vs_baseline_mm"]
            return f if f is not None else float("inf")

        def _tool_ord(v):
            return _TOOL_ORDINAL.get(v["tooling_class"], 99)

        winner = None
        closest_viable = None
        if competitors:
            ranked = sorted(competitors, key=lambda v: (
                v["unit_cost_usd"], _fid(v), _tool_ord(v), abs(v["value"] - baseline_value)))
            best = ranked[0]
            runner = ranked[1] if len(ranked) > 1 else None
            fids = sorted(_fid(v) for v in competitors)
            fid_rank = fids.index(_fid(best)) + 1
            winner = {
                "value": best["value"], "unit_cost_usd": best["unit_cost_usd"],
                "process": best["recommended_process"],
                "viability_tier": best["viability_tier"],
                "fidelity_vs_baseline_mm": best["fidelity_vs_baseline_mm"],
                "fidelity_rank_among_competitors": f"{fid_rank}/{len(competitors)}",
                "dfm_repaired": best["dfm_repaired"],
                "runner_up": ({
                    "value": runner["value"], "unit_cost_usd": runner["unit_cost_usd"],
                    "delta_usd": round(runner["unit_cost_usd"] - best["unit_cost_usd"], 4),
                    "delta_pct": round(
                        100.0 * (runner["unit_cost_usd"] - best["unit_cost_usd"])
                        / max(best["unit_cost_usd"], 1e-9), 1),
                } if runner else None),
                "why": (f"cheapest genuinely-viable variant at lot {args.lot_size} "
                        f"(${best['unit_cost_usd']}/unit via {best['recommended_process']}); "
                        f"fidelity {best['fidelity_vs_baseline_mm']} mm vs baseline "
                        f"(rank {fid_rank}/{len(competitors)})"),
            }
            cv = min(competitors, key=_fid)
            closest_viable = {
                "value": cv["value"], "unit_cost_usd": cv["unit_cost_usd"],
                "fidelity_vs_baseline_mm": cv["fidelity_vs_baseline_mm"],
            }

        # baseline competitor (value[0]) for savings.
        base_comp = next((v for v in variants if v["value"] == round(float(baseline_value), 6)
                          and v["unit_cost_usd"] is not None), None)
        baseline_out = None
        savings = None
        if base_comp:
            baseline_out = {
                "value": base_comp["value"], "unit_cost_usd": base_comp["unit_cost_usd"],
                "process": base_comp["recommended_process"],
                "fidelity_vs_baseline_mm": base_comp["fidelity_vs_baseline_mm"]}
            if winner and base_comp["unit_cost_usd"]:
                abs_usd = round(base_comp["unit_cost_usd"] - winner["unit_cost_usd"], 4)
                savings = {"abs_usd": abs_usd, "pct": round(
                    100.0 * abs_usd / max(base_comp["unit_cost_usd"], 1e-9), 1)}

        overall_flag = self._overall_flag(
            variants, competitors, winner, n_valid, n_excl_fid, is_name, baseline_step, args)

        cost_curve = [{
            "value": v["value"], "unit_cost_usd": v["unit_cost_usd"],
            "fidelity_vs_baseline_mm": v["fidelity_vs_baseline_mm"],
            "valid": v["valid"], "viable": v["viable"], "competitor": v["competitor"],
        } for v in sorted(variants, key=lambda v: v["value"])]

        # strip the private key from each variant record.
        for v in variants:
            v.pop("_baseline_step", None)

        return {
            "schema_version": SCHEMA_VERSION, "grade": "estimate",
            "overall_flag": overall_flag,
            "driver": args.driver, "driver_role": driver_role, "is_name": is_name,
            "fidelity_metric": "hausdorff_mm_vs_baseline_rebuild",
            "values_swept": [round(float(x), 6) for x in values],
            "baseline_value": round(float(baseline_value), 6),
            "lot_size": args.lot_size, "material": args.material,
            "processes": list(args.processes) if args.processes else _ALL_KEYS,
            "max_deviation_mm": args.max_deviation_mm, "repair_dfm": bool(args.repair_dfm),
            "variants": variants, "winner": winner, "closest_viable": closest_viable,
            "cost_curve": cost_curve, "baseline": baseline_out,
            "savings_vs_baseline": savings,
            "summary": {
                "n_swept": len(values), "n_valid": n_valid, "n_viable": n_viable,
                "n_competitors": len(competitors), "n_excluded_by_fidelity": n_excl_fid,
                "n_viable_marginal": n_marginal},
            "confidence_note": (
                "Estimate-grade: unit costs are heuristic models not quotes; "
                "fidelity is hausdorff vs the value[0] REBUILD (relative, not vs "
                "the original STEP; bbox_center-aligned, so a centroid shift can "
                "read low); only GENUINELY-viable (strict tier, not marginal/"
                "unproven) variants compete; the cheapest typically deviates most "
                "from intent — read cost AND fidelity together; treat the winner "
                "as a value to rebuild deliberately, not a quote."),
        }

    def _overall_flag(self, variants, competitors, winner, n_valid, n_excl_fid,
                      is_name, baseline_step, args) -> str:
        if n_valid == 0:
            return "all_invalid"
        # driver never landed on any variant.
        if all(v["drivers_unresolved"] for v in variants) and any(
                v["drivers_unresolved"] for v in variants):
            return "driver_unresolved"
        # baseline anchor failed -> fidelity unmeasurable under a set gate.
        if (args.max_deviation_mm is not None and baseline_step is None):
            return "baseline_anchor_failed"
        if not competitors:
            # valids exist but none genuinely viable.
            any_marginal = any(v["valid"] and not v["viable"]
                               and v["viability_tier"] in ("viable_marginal", "unproven")
                               for v in variants)
            if n_excl_fid and not any(v["valid"] and v["viable"] for v in variants):
                return "all_excluded_by_fidelity"
            return "only_marginal_variants" if any_marginal else "no_viable_variant"
        # a winner exists — check fidelity warnings + advisory alternative.
        if winner:
            fids = [c["fidelity_vs_baseline_mm"] for c in competitors
                    if c["fidelity_vs_baseline_mm"] is not None]
            wf = winner["fidelity_vs_baseline_mm"]
            soft_ceiling = self._soft_ceiling(args)
            worst_among = bool(fids) and wf is not None and wf >= max(fids) - 1e-9
            absolutely_bad = wf is not None and soft_ceiling is not None and wf > soft_ceiling
            if (worst_among and len(competitors) > 1) or absolutely_bad:
                return "cheapest_low_fidelity"
            # the winner's recommend run flagged an unpriced advisory alternative.
            wv = next((v for v in variants if v["value"] == winner["value"]), None)
            if wv and wv["recommend_overall_flag"] == "advisory_alternative_exists":
                return "advisory_alternative_exists"
        return "ok"

    def _soft_ceiling(self, args) -> float | None:
        """Absolute low-fidelity warning threshold even when max_deviation_mm is
        unset: max(0.5 mm, 10% of the baseline rebuild bbox diagonal)."""
        if args.max_deviation_mm is not None:
            return args.max_deviation_mm
        return 0.5  # conservative absolute floor; bbox-relative term needs the
        # baseline body which is discarded — 0.5 mm is the honest minimum warning.
