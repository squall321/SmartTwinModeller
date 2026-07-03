"""plan_edit — macro (Track 3-3 plan-as-feature-tree, 2026-07-03).

Edit a SAVED plan (YAML path or inline dict) through the feature-tree
dependency graph, then rebuild and report:

  op='suppress'  — remove a step PLUS everything that depends on it via the
                   feature tree (the suppressed CLOSURE is reported with the
                   edge evidence that pulled each member in). Suppressing a
                   step every other step depends on (e.g. the root create
                   step) would leave an EMPTY plan → structured refusal
                   ``fm.plan_edit_empty_plan`` (pinned behaviour).
  op='insert'    — insert a new step at position k (0-based; k==len appends).
  op='reorder'   — REFUSED BY DESIGN (``fm.plan_edit_reorder_unsupported``):
                   in a CSG sequence cut-then-boss != boss-then-cut, so
                   reordering is inherently unsafe (roadmap REJECT #3).
                   Express a reorder as suppress + insert, whose rebuild
                   verification then judges the result honestly.

Rebuild — GENUINE incremental (v1 semantics, stated honestly):
  plan_edit first executes the ORIGINAL plan once (the baseline — required to
  RECORD per-step EntityHistoryMap + intermediate bodies, since the executor
  keeps no persistent session snapshots), builds the feature tree from that
  execution, then re-executes ONLY steps k..N of the edited plan seeded with
  the baseline's recorded intermediate body before step k (k = first edited
  index). Steps 0..k-1 are NOT executed a second time. When the intermediate
  is unavailable (baseline failure before k, or k == 0) the rebuild falls
  back to a full re-run and the report SAYS SO (``rebuild.mode='full'`` +
  reason) — never a fake 'incremental' label.

Honest dependency guard: baseline-captured selector freezes are copied onto
the edited plan, so the rebuild re-verifies every surviving selector. A
dependency the tree missed (positional args implicitly targeting suppressed
material, selector count drift after an insert, ...) surfaces as a
FreezeMismatch / zero-delta skip / step failure in the rebuild report —
suppression mistakes are VISIBLE, never silent.

extras["plan_edit"] schema (strict-JSON-safe — no inf/nan, None for missing)::

    {"ok": bool, "grade": "edited", "op": "suppress"|"insert",
     "plan_name": str, "plan_source": "path"|"inline",
     "baseline": {"outcome", "error_count", "volume_mm3", "bbox_mm",
                  "is_solid"},
     "feature_tree": {step_id: {...}},          # build_feature_tree output
     "suppressed_closure": [{step_id, via, evidence}, ...] | None,
     "inserted": {"step_id", "position"} | None,
     "rebuild": {"mode": "incremental"|"full", "requested": str,
                 "reason": str|None, "first_affected_index": int,
                 "reused_steps": int, "reexecuted_steps": int,
                 "outcome": str, "error_count": int,
                 "freeze_mismatch_count": int, "zero_delta_skips": [...],
                 "volume_mm3": float|None, "bbox_mm": [..]|None,
                 "is_solid": bool|None},
     "edited_plan": {...},                       # save_plan-able dict
     "step_report": [{id, skill, status, reused, error}, ...],
     "warnings": [...], "caveats": [...]}

Structured refusals (all reachable in tests):
    fm.bad_args                       — not exactly one of plan_path/plan, or
                                        op-required args missing
    fm.plan_load_failed               — file missing / unparseable / invalid
    fm.expr_error                     — parametric plan whose table refuses
                                        (raised by plan/params.py, unmasked)
    fm.plan_edit_reorder_unsupported  — op='reorder' (REJECT #3 by design)
    fm.plan_edit_unknown_step         — suppress target id not in the plan
    fm.plan_edit_empty_plan           — suppression closure covers every step
    fm.plan_edit_invalid_step         — new_step fails Step schema validation
    fm.plan_edit_duplicate_id         — new_step id collides with an existing
    fm.plan_edit_unknown_skill        — new_step skill not in the registry
    fm.plan_edit_bad_position         — insert position outside [0, len]
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="plan_edit",
    category="reverse_engineer",
    level="macro",
    summary="Edit a SAVED plan through the feature-tree dependency graph: "
            "SUPPRESS a step plus its dependency closure, or INSERT a new "
            "step at position k, then rebuild incrementally (only steps k..N "
            "re-execute, seeded from the baseline run's recorded intermediate "
            "body) and report measured results. REORDER is refused by design "
            "(CSG order unsafe). Baseline-captured selector freezes guard the "
            "rebuild: a missed dependency surfaces as a FreezeMismatch or "
            "zero-delta skip in the report, never silently.",
    selector_kinds=[],
    history_rules={},
    produces_features=["edited_plan"],
    preserves=[],
    manufacturing={},
    failure_modes=[
        "fm.bad_args",
        "fm.plan_load_failed",
        "fm.expr_error",
        "fm.plan_edit_reorder_unsupported",
        "fm.plan_edit_unknown_step",
        "fm.plan_edit_empty_plan",
        "fm.plan_edit_invalid_step",
        "fm.plan_edit_duplicate_id",
        "fm.plan_edit_unknown_skill",
        "fm.plan_edit_bad_position",
    ],
    cost_hint=1.0,   # baseline execution + suffix re-execution
    # NO body_present gate: an edited plan whose rebuild failed still yields
    # the full honest report (ok=False) instead of a masked crash.
    post_conditions=[],
)
class PlanEdit(SkillBase):
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
        op: Literal["suppress", "insert", "reorder"] = Field(
            description="'suppress' | 'insert'. 'reorder' is accepted here "
                        "only to refuse it with a structured reason (CSG "
                        "order unsafe — roadmap REJECT #3).")
        step_id: str | None = Field(
            default=None,
            description="suppress: id of the step to suppress (its dependency "
                        "closure is suppressed with it).")
        new_step: dict[str, Any] | None = Field(
            default=None,
            description="insert: the new step {id, skill, args[, notes]}.")
        position: int | None = Field(
            default=None,
            description="insert: 0-based index for the new step; "
                        "position == len(steps) appends.")
        mode: Literal["strict", "loose"] = Field(
            default="strict",
            description="Executor selector mode for baseline + rebuild. "
                        "'strict' (default): a surviving selector freeze that "
                        "no longer matches FAILS its step — the honest guard "
                        "against dependencies the tree missed. 'loose': "
                        "mismatches re-resolve + re-capture and are reported "
                        "as drift.")
        rebuild: Literal["incremental", "full"] = Field(
            default="incremental",
            description="'incremental' (default): re-execute only steps k..N "
                        "from the baseline's recorded intermediate body. "
                        "'full': re-execute the edited plan from scratch. "
                        "Incremental falls back to full (honestly labelled) "
                        "when no intermediate is available.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.plan.executor import ExecutionMode, PlanExecutor
        from phone_designer.plan.feature_tree import (
            build_feature_tree,
            run_plan_suffix,
            snapshot_body_before,
            suppress_closure,
        )
        from phone_designer.plan.model import Plan, Step, StepStatus
        from phone_designer.plan.params import resolve_plan
        from phone_designer.plan.yaml_io import _migrate, load_plan
        from phone_designer.skills._registry import registry
        from phone_designer.skills.reverse_engineer.plan_reexecute import (
            _measure_body,
        )

        exec_mode = (ExecutionMode.LOOSE if args.mode == "loose"
                     else ExecutionMode.STRICT)

        # ------------------------------------------------ arg refusals
        if (args.plan_path is None) == (args.plan is None):
            raise ValueError(
                "fm.bad_args: exactly ONE of plan_path / plan must be given")
        if args.op == "reorder":
            raise ValueError(
                "fm.plan_edit_reorder_unsupported: reorder is REFUSED BY "
                "DESIGN — in a CSG sequence cut-then-boss != boss-then-cut, "
                "so reordering steps is inherently unsafe (roadmap REJECT "
                "#3). Express the change as suppress + insert; the rebuild "
                "verification then judges the result honestly.")
        if args.op == "suppress" and not args.step_id:
            raise ValueError(
                "fm.bad_args: op='suppress' requires step_id")
        if args.op == "insert" and (args.new_step is None
                                    or args.position is None):
            raise ValueError(
                "fm.bad_args: op='insert' requires new_step AND position")

        # ------------------------------------------------ load (refusals)
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

        # ------------------------------------------------ cheap edit checks
        step_ids = [s.id for s in plan.steps]
        if args.op == "suppress" and args.step_id not in step_ids:
            raise ValueError(
                f"fm.plan_edit_unknown_step: step_id '{args.step_id}' not in "
                f"plan '{plan.plan_name}' (steps: {step_ids})")

        new_step: Step | None = None
        if args.op == "insert":
            try:
                new_step = Step.model_validate(args.new_step)
            except Exception as exc:
                raise ValueError(
                    f"fm.plan_edit_invalid_step: new_step failed Step schema "
                    f"validation: {type(exc).__name__}: {exc}") from exc
            # runtime fields never come from the caller
            new_step.status = StepStatus.PENDING
            new_step.failure = None
            new_step.metrics = None
            if new_step.id in step_ids:
                raise ValueError(
                    f"fm.plan_edit_duplicate_id: new_step id '{new_step.id}' "
                    f"already exists in plan '{plan.plan_name}'")
            try:
                registry.get(new_step.skill)
            except KeyError as exc:
                raise ValueError(
                    f"fm.plan_edit_unknown_skill: new_step skill "
                    f"'{new_step.skill}' is not a registered skill") from exc
            if not (0 <= args.position <= len(plan.steps)):
                raise ValueError(
                    f"fm.plan_edit_bad_position: position {args.position} "
                    f"outside [0, {len(plan.steps)}]")

        # -------------------------------- baseline run (records histories +
        # intermediates; resolve_plan deep-copies so the loaded plan is never
        # mutated). ExprError (fm.expr_error) propagates unmasked.
        baseline_exec_plan, _ = resolve_plan(plan, None)
        baseline_result = PlanExecutor(
            baseline_exec_plan, mode=exec_mode).run(initial_body=body)
        baseline_measured = _measure_body(baseline_result.final_body)
        baseline = {
            "outcome": baseline_result.outcome,
            "error_count": baseline_result.error_count,
            "volume_mm3": baseline_measured["volume_mm3"],
            "bbox_mm": baseline_measured["bbox_mm"],
            "is_solid": baseline_measured["is_solid"],
        }

        tree = build_feature_tree(baseline_exec_plan, baseline_result)

        warnings: list[str] = []
        caveats: list[str] = [
            "dependency edges come from recorded history ROLES, entity-tag "
            "matches, and static arg references — NOT full entity-level "
            "geometry. Independence between two mutators of the same base is "
            "the CAD-conventional heuristic; the rebuild's selector-freeze "
            "verification (mode=strict) + zero-delta skip detection are the "
            "honest guard for edges the tree missed.",
            "rebuild is incremental RELATIVE TO the baseline run performed "
            "inside this call (the executor keeps no persistent session "
            "snapshots): steps before the edit point are executed once "
            "(baseline), not twice.",
        ]
        if baseline_result.error_count:
            warnings.append(
                f"baseline run FAILED {baseline_result.error_count} step(s) — "
                f"the feature tree degrades to 'unknown' edges for steps "
                f"without recorded history, and incremental rebuild may fall "
                f"back to full re-run")

        # ------------------------------------------------ apply the edit
        closure: list[dict[str, Any]] | None = None
        inserted: dict[str, Any] | None = None
        edited_plan = plan.model_copy(deep=True)

        if args.op == "suppress":
            closure = suppress_closure(tree, args.step_id)
            closure_ids = {c["step_id"] for c in closure}
            remaining = [s for s in edited_plan.steps
                         if s.id not in closure_ids]
            if not remaining:
                raise ValueError(
                    f"fm.plan_edit_empty_plan: suppressing '{args.step_id}' "
                    f"suppresses the ENTIRE plan (every step depends on it "
                    f"via the feature tree) — refusing to produce an empty "
                    f"plan. Closure: {sorted(closure_ids)}")
            first_affected = min(
                tree[c["step_id"]]["index"] for c in closure)
            edited_plan.steps = remaining
        else:  # insert (reorder already refused)
            edited_plan.steps = (
                edited_plan.steps[:args.position]
                + [new_step]
                + edited_plan.steps[args.position:]
            )
            first_affected = args.position
            inserted = {"step_id": new_step.id, "position": args.position}

        # honest dependency guard — carry baseline-captured freezes onto the
        # surviving steps so the rebuild RE-VERIFIES every selector.
        baseline_freeze = {
            s.id: s.selector_freeze for s in baseline_exec_plan.steps
        }
        for s in edited_plan.steps:
            fz = baseline_freeze.get(s.id)
            if fz is not None:
                s.selector_freeze = fz.model_copy(deep=True)

        # ------------------------------------------------ rebuild
        rebuild_exec_plan, _ = resolve_plan(edited_plan, None)
        n_edited = len(rebuild_exec_plan.steps)
        snapshot, snapshot_ok = snapshot_body_before(
            baseline_result, first_affected)
        reason: str | None = None

        if args.rebuild == "incremental" and first_affected == 0:
            rebuild_mode = "full"
            reason = ("first affected step is index 0 — nothing precedes it, "
                      "incremental degenerates to a full re-run")
        elif args.rebuild == "incremental" and not snapshot_ok:
            rebuild_mode = "full"
            reason = ("no recorded intermediate body before step index "
                      f"{first_affected} (a baseline step before it did not "
                      f"PASS) — honest fallback to a full re-run")
        elif args.rebuild == "full":
            rebuild_mode = "full"
            reason = "full re-run requested"
        else:
            rebuild_mode = "incremental"

        if rebuild_mode == "incremental":
            suffix_result = run_plan_suffix(
                rebuild_exec_plan, first_affected,
                initial_body=snapshot, mode=exec_mode)
            reused = first_affected
        else:
            suffix_result = PlanExecutor(
                rebuild_exec_plan, mode=exec_mode).run(initial_body=body)
            reused = 0

        rebuilt_body = suffix_result.final_body
        rebuilt_measured = _measure_body(rebuilt_body)
        rebuild_report = {
            "mode": rebuild_mode,
            "requested": args.rebuild,
            "reason": reason,
            "first_affected_index": first_affected,
            "reused_steps": reused,
            "reexecuted_steps": n_edited - reused,
            "outcome": suffix_result.outcome,
            "error_count": suffix_result.error_count,
            "freeze_mismatch_count": len(suffix_result.freeze_mismatches),
            "zero_delta_skips": [
                dict(s) for s in suffix_result.skipped_steps
            ],
            "volume_mm3": rebuilt_measured["volume_mm3"],
            "bbox_mm": rebuilt_measured["bbox_mm"],
            "is_solid": rebuilt_measured["is_solid"],
        }

        if suffix_result.error_count:
            warnings.append(
                f"rebuild FAILED {suffix_result.error_count} step(s) — if a "
                f"failure is a FreezeMismatch on a surviving step, the edit "
                f"likely removed/disturbed a dependency the feature tree "
                f"missed (see step_report)")
        if suffix_result.freeze_mismatches:
            warnings.append(
                f"selector drift on {len(suffix_result.freeze_mismatches)} "
                f"surviving step(s) after the edit — each drifted selector "
                f"re-resolved to different entities than the baseline froze; "
                f"verify those features (possible missed dependency)")
        if any("zero_delta" in (s.get("reason") or "")
               for s in suffix_result.skipped_steps):
            warnings.append(
                "a surviving material-removal step produced ZERO geometric "
                "change after the edit — it probably targeted material the "
                "suppressed step used to provide (missed dependency); it was "
                "SKIPPED, not silently absorbed")

        # ------------------------------------------------ step report
        baseline_status = {
            s.id: s.status.value for s in baseline_exec_plan.steps
        }
        step_report = []
        for i, s in enumerate(rebuild_exec_plan.steps):
            reused_step = i < reused
            step_report.append({
                "id": s.id,
                "skill": s.skill,
                "status": (baseline_status.get(s.id, "pass") if reused_step
                           else getattr(s.status, "value", str(s.status))),
                "reused": reused_step,
                "error": (None if reused_step
                          else (s.failure.message if s.failure else None)),
            })

        plan_edit = {
            "ok": bool(suffix_result.outcome == "PASS"
                       and rebuilt_measured["volume_mm3"] is not None),
            "grade": "edited",
            "op": args.op,
            "mode": args.mode,
            "plan_name": plan.plan_name,
            "plan_source": plan_source,
            "baseline": baseline,
            "feature_tree": tree,
            "suppressed_closure": closure,
            "inserted": inserted,
            "rebuild": rebuild_report,
            "edited_plan": edited_plan.model_dump(
                mode="json", exclude_none=True),
            "step_report": step_report,
            "warnings": warnings,
            "caveats": caveats,
        }
        return SkillResult(
            body=rebuilt_body if rebuilt_body is not None else body,
            history=EntityHistoryMap(),
            extras={"plan_edit": plan_edit},
        )
