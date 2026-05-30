"""plan_diff — atomic, read-only.

Compare two Plan YAMLs step-by-step. Output a list of diff entries describing
what changed for each step_id present in either plan. Useful when an LLM
iterates a Plan: it can see exactly which steps were added, removed, or had
their skill/args modified.

Algorithm:
    1. Parse both YAMLs (utf-8). Both are expected to expose `steps: [...]`.
       Missing / unparseable files produce a per-side `error` entry but the
       diff still proceeds for the other side.
    2. Index each side's steps by `id`. Iterate in the union order, preserving
       the order from plan A then appending A-missing ids from B.
    3. For each step_id emit one of:
        - "added"     id only in B
        - "removed"   id only in A
        - "skill_changed"  same id, different skill name
        - "args_changed"  same id+skill, args dict differ
        - "unchanged" identical
    4. For "args_changed" emit a nested per-field arg diff
       (before/after value) so callers can build a UI quickly.

result.extras["diff"] schema:
    [{"step_id": str,
      "change": "added"|"removed"|"skill_changed"|"args_changed"|"unchanged",
      "skill_a": str | None, "skill_b": str | None,
      "args_a": dict | None, "args_b": dict | None,
      "arg_field_diffs": [
          {"field": str, "before": Any, "after": Any}, ...
      ] | None}, ...]

extras also has:
    plan_a_path, plan_b_path, plan_a_step_count, plan_b_step_count,
    summary_counts={added,removed,skill_changed,args_changed,unchanged},
    error_a, error_b
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _load_yaml(path: str) -> tuple[dict | None, str | None]:
    p = Path(path)
    if not p.exists():
        return None, f"plan file not found: {path}"
    try:
        import yaml
        return yaml.safe_load(p.read_text(encoding="utf-8")), None
    except Exception as ex:
        return None, f"failed to parse: {type(ex).__name__}: {ex}"


def _index_steps(plan: dict | None) -> dict[str, dict]:
    if not isinstance(plan, dict):
        return {}
    steps = plan.get("steps") or []
    out: dict[str, dict] = {}
    for s in steps:
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        if isinstance(sid, str):
            out[sid] = s
    return out


def _ordered_step_ids(plan_a: dict | None, plan_b: dict | None) -> list[str]:
    a_ids = []
    if isinstance(plan_a, dict):
        for s in plan_a.get("steps") or []:
            if isinstance(s, dict) and isinstance(s.get("id"), str):
                a_ids.append(s["id"])
    seen = set(a_ids)
    b_extra: list[str] = []
    if isinstance(plan_b, dict):
        for s in plan_b.get("steps") or []:
            if isinstance(s, dict) and isinstance(s.get("id"), str):
                sid = s["id"]
                if sid not in seen:
                    b_extra.append(sid)
                    seen.add(sid)
    return a_ids + b_extra


def _arg_field_diffs(
    args_a: dict | None,
    args_b: dict | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    a = args_a if isinstance(args_a, dict) else {}
    b = args_b if isinstance(args_b, dict) else {}
    keys = sorted(set(a.keys()) | set(b.keys()))
    for k in keys:
        va = a.get(k, "__MISSING__")
        vb = b.get(k, "__MISSING__")
        if va != vb:
            out.append({
                "field": k,
                "before": None if va == "__MISSING__" else va,
                "after": None if vb == "__MISSING__" else vb,
                "added": va == "__MISSING__",
                "removed": vb == "__MISSING__",
            })
    return out


@skill(
    name="plan_diff",
    category="inspect",
    level="atomic",
    summary="Compare two Plan YAML files step-by-step and return a list of "
            "diff entries describing added / removed / skill_changed / "
            "args_changed / unchanged steps. Includes per-field arg diff for "
            "args_changed entries. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["plan_diff"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.02,
    post_conditions=[PostCondition(kind="body_present")],
)
class PlanDiff(SkillBase):
    class Args(BaseModel):
        plan_path_a: str = Field(min_length=1)
        plan_path_b: str = Field(min_length=1)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        plan_a, err_a = _load_yaml(args.plan_path_a)
        plan_b, err_b = _load_yaml(args.plan_path_b)

        idx_a = _index_steps(plan_a)
        idx_b = _index_steps(plan_b)
        order = _ordered_step_ids(plan_a, plan_b)

        diff: list[dict[str, Any]] = []
        counts = {
            "added": 0,
            "removed": 0,
            "skill_changed": 0,
            "args_changed": 0,
            "unchanged": 0,
        }
        for sid in order:
            a = idx_a.get(sid)
            b = idx_b.get(sid)
            if a is None and b is not None:
                change = "added"
                entry = {
                    "step_id": sid,
                    "change": change,
                    "skill_a": None,
                    "skill_b": b.get("skill"),
                    "args_a": None,
                    "args_b": b.get("args"),
                    "arg_field_diffs": None,
                }
            elif a is not None and b is None:
                change = "removed"
                entry = {
                    "step_id": sid,
                    "change": change,
                    "skill_a": a.get("skill"),
                    "skill_b": None,
                    "args_a": a.get("args"),
                    "args_b": None,
                    "arg_field_diffs": None,
                }
            else:
                # Both sides present.
                skill_a = a.get("skill") if a else None
                skill_b = b.get("skill") if b else None
                args_a = a.get("args") if a else None
                args_b = b.get("args") if b else None
                if skill_a != skill_b:
                    change = "skill_changed"
                elif args_a != args_b:
                    change = "args_changed"
                else:
                    change = "unchanged"
                entry = {
                    "step_id": sid,
                    "change": change,
                    "skill_a": skill_a,
                    "skill_b": skill_b,
                    "args_a": args_a,
                    "args_b": args_b,
                    "arg_field_diffs": (
                        _arg_field_diffs(args_a, args_b)
                        if change in ("args_changed", "skill_changed")
                        else None
                    ),
                }
            counts[change] += 1
            diff.append(entry)

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "diff": diff,
                "plan_a_path": args.plan_path_a,
                "plan_b_path": args.plan_path_b,
                "plan_a_step_count": len(idx_a),
                "plan_b_step_count": len(idx_b),
                "summary_counts": counts,
                "error_a": err_a,
                "error_b": err_b,
            },
        )
