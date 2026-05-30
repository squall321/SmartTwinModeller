"""explain_skill_failure — atomic, read-only.

Given a failed step's id, the failure exception message, and (optionally) the
plan YAML path, diagnose the most likely root cause and suggest a fix. Pure
heuristic — no body mutation, no resolver execution. Plan parsing is best-
effort: if the YAML is absent / unparseable the diagnosis still proceeds from
the message alone.

Diagnosis ladder (first match wins):
    "selector matched 0"           → likely "selector matched 0"
    "matched N faces" / "ambiguous" → "selector matched many"
    "depth"+"thickness"/"exits body" → "depth exceeds body"
    "sketch outside" / "outside face" → "sketch placed outside face"
    "post_condition" + "volume_decreased" → "no material removed"
    "post_condition" + "volume_increased" → "no material added"
    "BRep" / "cut failed" / "boolean" → "OCCT boolean fault"
    "unknown skill"                → "skill name typo / not registered"
    "pydantic" / "validation"      → "args schema validation"
    Fall-back                      → "unrecognised — see message"

For each diagnosis, a `suggested_fix` is provided that names the introspection
or selector skill the LLM should run next.

result.extras["diagnosis"] schema:
    {"failed_step_id": str,
     "failure_message": str,
     "skill_name": str | None,             # extracted from plan if available
     "likely_cause": str,
     "suggested_fix": str,
     "related_skills_to_run": [str, ...],
     "plan_step_found": bool,
     "plan_step_excerpt": dict | None,
     "error": str | None}
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import registry, skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _load_plan(plan_path: str | None) -> tuple[dict | None, str | None]:
    if not plan_path:
        return None, None
    p = Path(plan_path)
    if not p.exists():
        return None, f"plan file not found: {plan_path}"
    try:
        import yaml
        text = p.read_text(encoding="utf-8")
        return yaml.safe_load(text), None
    except Exception as ex:
        return None, f"failed to parse plan: {type(ex).__name__}: {ex}"


def _find_step(plan: dict | None, step_id: str) -> dict | None:
    if not plan or not isinstance(plan, dict):
        return None
    steps = plan.get("steps") or []
    for s in steps:
        if isinstance(s, dict) and s.get("id") == step_id:
            return s
    return None


def _diagnose(
    message: str,
    skill_name: str | None,
    step: dict | None,
) -> tuple[str, str, list[str]]:
    """Return (likely_cause, suggested_fix, related_skills_to_run)."""
    m = message.lower()

    # 1. selector matched 0
    if (
        "matched 0" in m
        or "selector matched 0" in m
        or "no faces matched" in m
        or "no edges matched" in m
    ):
        return (
            "Selector matched 0 entities. The face_named / tagged / "
            "axis_aligned_edges selector did not resolve to any face or edge "
            "on the current body.",
            "Run dry_run on the same skill+args to confirm matched_count=0, "
            "then use suggest_selector_from_phrase or selector_preview to find "
            "a stable selector. selector_robustness_score can rank candidates.",
            ["dry_run", "suggest_selector_from_phrase",
             "selector_preview", "selector_robustness_score",
             "inspect_geometry"],
        )

    # 2. selector matched many / ambiguous
    if (
        "ambiguous" in m
        or "matched many" in m
        or "expected 1 face" in m
        or "expected one face" in m
        or "too many" in m
    ):
        return (
            "Selector matched more than 1 entity but the skill expects a "
            "single face. Constrain with first_n / largest_n or a tagged "
            "selector applied via tag_face.",
            "Wrap the current selector with {'kind': 'first_n', 'n': 1, "
            "'inner': <current>} or tag the intended face first with tag_face.",
            ["selector_preview", "tag_face", "selector_robustness_score"],
        )

    # 3. depth exceeds body
    if (
        ("depth" in m and ("thickness" in m or "exceeds" in m or "exit" in m))
        or "exits body" in m
        or "hole_exits_body" in m
        or "pocket_exits_body" in m
    ):
        return (
            "Pocket / hole depth is greater than the local body thickness — "
            "the cut exits the body instead of producing a blind feature.",
            "Reduce depth_mm, or switch to extrude_through if a through-cut "
            "is intended. Use inspect_geometry on the target face to read "
            "the body bbox size along the normal.",
            ["inspect_geometry", "predict_post_conditions",
             "pocket_aspect_ratio_check"],
        )

    # 4. sketch outside face
    if (
        "sketch outside" in m
        or "outside face" in m
        or "sketch_outside_face" in m
        or "out of face" in m
    ):
        return (
            "The 2D sketch was placed outside the target face's parametric "
            "domain — the boolean cut produced 0 mm³ of removal.",
            "Re-center the sketch on the face. Run inspect_geometry to read "
            "face center + extents, then move the sketch origin to that "
            "point or shrink the sketch.",
            ["inspect_geometry", "selector_preview", "dry_run"],
        )

    # 5. post_condition volume_decreased — silent no-op cut
    if "post_condition" in m and "volume_decreased" in m:
        return (
            "Skill ran but removed less than min_delta_mm3 of material. "
            "Likely a face-orientation bug, sketch placed outside the face, "
            "or depth=0.",
            "Inspect the target face with inspect_geometry; confirm the "
            "sketch is inside the face bounds and depth_mm > 0.01. Use "
            "predict_post_conditions to estimate ΔV before re-running.",
            ["inspect_geometry", "predict_post_conditions", "dry_run"],
        )

    if "post_condition" in m and "volume_increased" in m:
        return (
            "Skill ran but added less than min_delta_mm3 of material. The "
            "boss / plateau sketch likely overlapped existing material 100% "
            "(no new volume) or had height=0.",
            "Confirm sketch position is outside the existing body, or that "
            "height_mm > 0.01. inspect_geometry + dry_run will catch this.",
            ["inspect_geometry", "predict_post_conditions", "dry_run"],
        )

    if "post_condition" in m and "face_count_changed" in m:
        return (
            "Skill ran but produced no new faces or edges. Likely the fillet "
            "/ chamfer selector matched 0 sharp edges, or the operation was "
            "a no-op on the given geometry.",
            "Verify the edge selector with selector_preview; consider "
            "allow_no_change=True if the no-op is legitimate.",
            ["selector_preview", "dry_run"],
        )

    # 6. OCCT boolean fault
    if (
        "brep" in m
        or "boolean" in m
        or "cut" in m and "failed" in m
        or "fuse" in m and "failed" in m
        or "boolop" in m
    ):
        return (
            "OCCT boolean operation failed — typically caused by tangent "
            "faces, near-coincident edges, or a degenerate tool shape.",
            "Run shape_heal on the body first, then retry; or perturb the "
            "sketch by ~0.01 mm to break the tangency.",
            ["shape_heal", "inspect_geometry", "continuity_audit"],
        )

    # 7. unknown skill
    if "unknown skill" in m or "not registered" in m:
        return (
            "The skill name in the plan is not in the registry — typo or "
            "the module was not imported in export_manifest.py.",
            "Use find_skill_by_intent on the intended verb (e.g., 'add a "
            "boss') to discover the correct skill name.",
            ["find_skill_by_intent", "skill_schema_introspect"],
        )

    # 8. args validation
    if (
        "validation error" in m
        or "pydantic" in m
        or "field required" in m
        or "value_error" in m
        or "type_error" in m
    ):
        return (
            "Args failed Pydantic schema validation — a required field is "
            "missing or the value is out of the declared range.",
            "Run skill_schema_introspect on this skill to read the exact "
            "Args JSON schema and field constraints.",
            ["skill_schema_introspect", "find_skill_by_intent"],
        )

    # Fall-back
    return (
        "Could not classify the failure from the message alone.",
        "Re-run skill_schema_introspect + dry_run with the same args; inspect "
        "the message verbatim for OCCT error codes.",
        ["skill_schema_introspect", "dry_run", "inspect_geometry"],
    )


@skill(
    name="explain_skill_failure",
    category="inspect",
    level="atomic",
    summary="Diagnose a failed Plan step from its failure message (and "
            "optionally the source plan YAML). Returns likely_cause + "
            "suggested_fix + a list of follow-up inspect skills the LLM "
            "should run to confirm. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["failure_diagnosis"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.02,
    post_conditions=[PostCondition(kind="body_present")],
)
class ExplainSkillFailure(SkillBase):
    class Args(BaseModel):
        failed_step_id: str = Field(min_length=1)
        failure_message: str
        plan_path: str | None = None

    def _apply(self, body: Any, args: Args) -> SkillResult:
        plan, plan_err = _load_plan(args.plan_path)
        step = _find_step(plan, args.failed_step_id)
        skill_name = step.get("skill") if isinstance(step, dict) else None

        likely, fix, related = _diagnose(
            args.failure_message, skill_name, step,
        )

        # If we know the skill name, enrich related with its declared
        # post_conditions for the LLM to scan.
        if skill_name:
            try:
                spec = registry.get(skill_name)
                if spec.post_conditions and "predict_post_conditions" not in related:
                    related = list(related) + ["predict_post_conditions"]
            except KeyError:
                pass

        diagnosis = {
            "failed_step_id": args.failed_step_id,
            "failure_message": args.failure_message,
            "skill_name": skill_name,
            "likely_cause": likely,
            "suggested_fix": fix,
            "related_skills_to_run": related,
            "plan_step_found": step is not None,
            "plan_step_excerpt": step if isinstance(step, dict) else None,
            "error": plan_err,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"diagnosis": diagnosis},
        )
