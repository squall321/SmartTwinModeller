"""plan_reexecute — macro (Pillar VARIANTS, plan schema v2, 2026-07-02).

Take a SAVED plan (YAML path or inline dict) + a parameter override set,
resolve every ``{"$expr": ...}`` arg node against the (overridden) parameter
table, re-run the plan through the PlanExecutor, and report the measured
volume / bbox changes vs the original-parameter run.

Selector policy — LOOSE mode (the executor's built-in relaxed mode): a frozen
selector that no longer matches after the parameter change is NOT a hard
failure; the executor records the mismatch, re-resolves the selector, and
RE-CAPTURES the new freeze ("freeze invalidate + re-capture"). Every such
drift is surfaced in ``selector_drift`` with an HONEST warning — this is the
SolidWorks rebuild-error analogue: the plan still rebuilt, but a drifted
feature may have landed on a different face than the author intended and must
be verified.

Baseline semantics: by default (``run_baseline=True``) the ORIGINAL-parameter
plan is re-executed on this machine so both sides of the delta are measured
the same way (deterministic, no stale numbers). ``run_baseline=False`` falls
back to the plan's recorded per-step metrics (last PASS step post_volume) —
honestly labelled as possibly stale, bbox then unavailable.

extras["reexecute"] schema (strict-JSON-safe — no inf/nan, None for missing)::

    {"ok": bool, "grade": "reexecuted", "mode": "loose"|"strict",
     "plan_name": str, "plan_source": "path"|"inline",
     "parameters_base": {name: float} | None,
     "parameters_resolved": {name: float} | None,   # after overrides
     "overrides_applied": {name: float},
     "baseline": {"ran": bool, "outcome": str|None, "error_count": int|None,
                  "volume_mm3": float|None, "bbox_mm": [x,y,z]|None,
                  "is_solid": bool|None, "freeze_mismatch_count": int|None,
                  "source": "reexecuted"|"recorded_metrics"|None},
     "variant":  {... same keys ...},
     "deltas": {"volume_mm3": float|None, "volume_pct": float|None,
                "bbox_mm": [dx,dy,dz]|None},
     "selector_drift": [{step_id, expected, actual}, ...],
     "step_report": [{id, skill, status, error}, ...],
     "warnings": [...], "caveats": [...]}

Structured refusals (all reachable in tests):
    fm.bad_args         — not exactly one of plan_path / plan given
    fm.plan_load_failed — file missing / unparseable / schema-invalid
    fm.expr_error       — undefined name, cycle, override of unknown param,
                          bad $expr node (raised by plan/params.py)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _round_or_none(v: Any, nd: int = 3) -> float | None:
    """strict-JSON-safe rounding — non-finite / non-numeric → None."""
    import math
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, nd)


def _measure_body(body: Any) -> dict[str, Any]:
    """volume / bbox / is_solid for a final body. Never raises.

    is_solid follows the house rule: at least one TopAbs_SOLID sub-shape AND
    volume > 1e-6 (topology never lies; divergence-theorem pseudo-mass on an
    open shell does).
    """
    out: dict[str, Any] = {"volume_mm3": None, "bbox_mm": None,
                           "is_solid": None}
    if body is None:
        return out
    shape = body.wrapped if hasattr(body, "wrapped") else body
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.TopAbs import TopAbs_SOLID
        from OCP.TopExp import TopExp_Explorer

        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        volume = abs(float(props.Mass()))
        has_solid = TopExp_Explorer(shape, TopAbs_SOLID).More()
        out["volume_mm3"] = _round_or_none(volume)
        out["is_solid"] = bool(has_solid and volume > 1e-6)
    except Exception:  # noqa: BLE001 — measurement failure stays None (honest)
        pass
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        bb = Bnd_Box()
        BRepBndLib.Add_s(shape, bb)
        if not bb.IsVoid():
            xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
            out["bbox_mm"] = [_round_or_none(xmax - xmin),
                              _round_or_none(ymax - ymin),
                              _round_or_none(zmax - zmin)]
    except Exception:  # noqa: BLE001
        pass
    return out


def _run_side(plan, mode, initial_body) -> tuple[dict[str, Any], Any, list, list]:
    """Execute one side (baseline or variant). Returns
    (summary, final_body, freeze_mismatches, step_report)."""
    from phone_designer.plan.executor import ExecutionMode, PlanExecutor

    exec_mode = (ExecutionMode.LOOSE if mode == "loose"
                 else ExecutionMode.STRICT)
    result = PlanExecutor(plan, mode=exec_mode).run(initial_body=initial_body)
    measured = _measure_body(result.final_body)
    summary = {
        "ran": True,
        "source": "reexecuted",
        "outcome": result.outcome,
        "error_count": result.error_count,
        "volume_mm3": measured["volume_mm3"],
        "bbox_mm": measured["bbox_mm"],
        "is_solid": measured["is_solid"],
        "freeze_mismatch_count": len(result.freeze_mismatches),
    }
    step_report = [
        {"id": s.id, "skill": s.skill,
         "status": getattr(s.status, "value", str(s.status)),
         "error": (s.failure.message if s.failure else None)}
        for s in plan.steps
    ]
    return summary, result.final_body, list(result.freeze_mismatches), step_report


_EMPTY_SIDE: dict[str, Any] = {
    "ran": False, "source": None, "outcome": None, "error_count": None,
    "volume_mm3": None, "bbox_mm": None, "is_solid": None,
    "freeze_mismatch_count": None,
}


@skill(
    name="plan_reexecute",
    category="reverse_engineer",
    level="macro",
    summary="Re-execute a SAVED plan (schema v2) with a parameter override "
            "set: resolve {'$expr': ...} args against the overridden "
            "parameter table, re-run via the PlanExecutor in LOOSE selector "
            "mode (freeze invalidate + re-capture), and report measured "
            "volume/bbox deltas vs the original-parameter run. Frozen-"
            "selector drift is surfaced as an HONEST warning (SolidWorks "
            "rebuild-error analogue), never silently absorbed.",
    selector_kinds=[],
    history_rules={},
    produces_features=["reexecuted"],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.bad_args", "fm.plan_load_failed", "fm.expr_error"],
    cost_hint=1.0,   # two full plan executions (baseline + variant)
    # NO body_present gate: a plan whose every step failed still yields the
    # full honest report (ok=False, outcome=FAIL) instead of a masked crash.
    post_conditions=[],
)
class PlanReexecute(SkillBase):
    class Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

        plan_path: str | None = Field(
            default=None,
            description="Path to a saved plan YAML (schema v1 or v2). "
                        "Exactly one of plan_path / plan must be given.")
        plan: dict[str, Any] | None = Field(
            default=None,
            description="Inline plan dict (same schema as the YAML). "
                        "Exactly one of plan_path / plan must be given.")
        parameter_overrides: dict[str, float] = Field(
            default_factory=dict,
            description="name → new value. Overriding a name not in the "
                        "plan's parameter table refuses (fm.expr_error). An "
                        "override PINS a derived parameter to a literal.")
        mode: Literal["loose", "strict"] = Field(
            default="loose",
            description="Executor selector mode for the VARIANT run. 'loose' "
                        "(default, recommended): freeze mismatch → warn + "
                        "re-resolve + re-capture, drift reported. 'strict': "
                        "freeze mismatch fails the step (cross-check mode).")
        run_baseline: bool = Field(
            default=True,
            description="Re-execute the original-parameter plan for a "
                        "measured like-for-like baseline (default). False = "
                        "use the plan's recorded step metrics if present "
                        "(honestly labelled possibly stale; bbox unavailable).")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.plan.model import Plan
        from phone_designer.plan.params import resolve_plan
        from phone_designer.plan.yaml_io import _migrate, load_plan

        # ------------------------------------------------ load (refusals)
        if (args.plan_path is None) == (args.plan is None):
            raise ValueError(
                "fm.bad_args: exactly ONE of plan_path / plan must be given")
        if args.plan_path is not None:
            p = Path(args.plan_path)
            try:
                plan = load_plan(p)
            except FileNotFoundError as exc:
                raise ValueError(
                    f"fm.plan_load_failed: plan file not found: {p}") from exc
            except Exception as exc:  # raw cause appended — never masked
                raise ValueError(
                    f"fm.plan_load_failed: {p}: "
                    f"{type(exc).__name__}: {exc}") from exc
            plan_source = "path"
        else:
            try:
                plan = Plan.model_validate(_migrate(dict(args.plan)))
            except Exception as exc:
                raise ValueError(
                    f"fm.plan_load_failed: inline plan invalid: "
                    f"{type(exc).__name__}: {exc}") from exc
            plan_source = "inline"

        # -------------------------------- resolve exprs (fm.expr_error path)
        # base table first (no overrides), then the overridden variant table.
        # resolve_plan deep-copies, so the loaded plan is never mutated and
        # baseline/variant runs cannot contaminate each other's freezes.
        base_plan, base_table = resolve_plan(plan, None)
        variant_plan, variant_table = resolve_plan(
            plan, args.parameter_overrides or None)

        warnings: list[str] = []
        caveats: list[str] = [
            "re-executed in LOOSE selector mode — frozen selectors that no "
            "longer match are re-resolved and re-captured; any entry in "
            "selector_drift means a feature may have landed on a DIFFERENT "
            "face/edge than the plan author froze (SolidWorks rebuild-error "
            "analogue) and must be verified"
            if args.mode == "loose" else
            "re-executed in STRICT selector mode — a frozen selector that no "
            "longer matches FAILS that step",
        ]

        # ------------------------------------------------ baseline side
        if args.run_baseline:
            baseline, _base_body, _base_drift, _ = _run_side(
                base_plan, args.mode, body)
        else:
            baseline = dict(_EMPTY_SIDE)
            recorded = None
            for s in reversed(plan.steps):
                m = s.metrics or {}
                if m.get("post_volume_mm3") is not None:
                    recorded = m["post_volume_mm3"]
                    break
            if recorded is not None:
                baseline.update({"source": "recorded_metrics",
                                 "volume_mm3": _round_or_none(recorded)})
                caveats.append(
                    "baseline NOT re-run (run_baseline=False): volume comes "
                    "from the plan's recorded step metrics — possibly stale, "
                    "bbox/is_solid unavailable")
            else:
                caveats.append(
                    "baseline unavailable: run_baseline=False and the plan "
                    "carries no recorded step metrics — deltas are None")

        # ------------------------------------------------ variant side
        variant, variant_body, variant_drift, step_report = _run_side(
            variant_plan, args.mode, body)

        if variant["error_count"]:
            warnings.append(
                f"variant run FAILED {variant['error_count']} step(s) — see "
                f"step_report; deltas compare the LAST GOOD body, not the "
                f"intended final state")
        if variant_drift:
            warnings.append(
                f"selector drift on {len(variant_drift)} step(s): the "
                f"parameter change moved/changed topology and frozen "
                f"selectors re-resolved to NEW entities (LOOSE re-capture). "
                f"Verify each drifted feature landed on the intended "
                f"face/edge — this is the SolidWorks rebuild-error analogue.")

        # ------------------------------------------------ deltas (JSON-safe)
        dv = dpct = dbox = None
        if (variant["volume_mm3"] is not None
                and baseline["volume_mm3"] is not None):
            dv = _round_or_none(variant["volume_mm3"] - baseline["volume_mm3"])
            if baseline["volume_mm3"] > 1e-9:
                dpct = _round_or_none(
                    100.0 * (variant["volume_mm3"] - baseline["volume_mm3"])
                    / baseline["volume_mm3"], 4)
        if (variant["bbox_mm"] is not None and baseline["bbox_mm"] is not None
                and None not in variant["bbox_mm"]
                and None not in baseline["bbox_mm"]):
            dbox = [_round_or_none(a - b)
                    for a, b in zip(variant["bbox_mm"], baseline["bbox_mm"])]

        has_params = plan.parameters is not None
        reexecute = {
            "ok": bool(variant["outcome"] == "PASS"
                       and variant["volume_mm3"] is not None),
            "grade": "reexecuted",
            "mode": args.mode,
            "plan_name": plan.plan_name,
            "plan_source": plan_source,
            "parameters_base": dict(base_table) if has_params else None,
            "parameters_resolved": dict(variant_table) if has_params else None,
            "overrides_applied": dict(args.parameter_overrides),
            "baseline": baseline,
            "variant": variant,
            "deltas": {"volume_mm3": dv, "volume_pct": dpct, "bbox_mm": dbox},
            "selector_drift": variant_drift,
            "step_report": step_report,
            "warnings": warnings,
            "caveats": caveats,
        }
        return SkillResult(
            body=variant_body if variant_body is not None else body,
            history=EntityHistoryMap(),
            extras={"reexecute": reexecute},
        )
