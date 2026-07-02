"""generate_from_spec — create macro: build a part FROM SCRATCH from a spec.

The generative front door. Where the rest of the pipeline reverse-ENGINEERS an
existing CAD file, this GENERATES a new solid from a declarative spec — a list of
(skill op + args) steps — by dispatching the project's whole registered skill
library (~380 create + modify/feature skills: box/cylinder/gear/spring/lattice
primitives, then hole/pocket/boss/counterbore/thread/bezel/groove… features)
through the existing PlanExecutor, starting from nothing (initial_body=None).

So "a zero-base instructed structure" IS generatable today: author the spec, get a
real solid (exportable STEP) that flows straight into the analysis suite
(estimate_cost / recognize_fits / recommend_process). What this adds over calling
the executor directly: schema-validated args per step (the skill's own pydantic
model), per-step failure ISOLATION (a bad step is recorded, the rest still build),
an is-solid validation of the result, and a clean generated-part manifest.

HONEST scope: this COMPOSES existing skills — it does not invent new geometry kinds
or interpret natural language. The spec is the (structured) instruction; turning a
free-text instruction into a spec is a separate, not-yet-built front door.

extras["generated"] = { n_steps, n_ok, ok, is_solid, volume_mm3, bbox_mm,
                        steps:[{op, status, error}], spec_errors[], grade }.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _bbox_mm(body) -> list[float] | None:
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        shape = body.wrapped if hasattr(body, "wrapped") else body
        bb = Bnd_Box()
        BRepBndLib.Add_s(shape, bb)
        if bb.IsVoid():
            return None
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        return [round(xmax - xmin, 3), round(ymax - ymin, 3), round(zmax - zmin, 3)]
    except Exception:  # noqa: BLE001
        return None


@skill(
    name="generate_from_spec",
    category="create",
    level="macro",
    summary="GENERATE a new solid FROM SCRATCH from a declarative spec — a list of "
            "(skill op + args) steps dispatched through the registered skill "
            "library via the PlanExecutor (initial_body=None). Schema-validates "
            "each step's args, isolates per-step failures, validates the result is "
            "a solid, and returns a generated-part manifest. Composes existing "
            "skills (does not interpret free text or invent geometry kinds).",
    selector_kinds=[],
    history_rules={},
    produces_features=["generated"],
    preserves=[],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.5,
    post_conditions=[PostCondition(kind="body_present")],
)
class GenerateFromSpec(SkillBase):
    class Args(BaseModel):
        spec: list[dict] = Field(
            description="Ordered build steps: [{op: <skill_name>, args: {...}}, ...]. "
                        "The first step is usually a create skill (box/cylinder/"
                        "gear/…); later steps are features (hole/pocket/boss/…).")
        validate_solid: bool = Field(
            default=True,
            description="Validate the result is a CLOSED SOLID (volume > 0 AND at "
                        "least one TopAbs_SOLID — an open shell honestly fails "
                        "this). Pass False for surface-first workflows whose final "
                        "body is an OPEN surface (create_open_surface / "
                        "fill_surface_patch / surface blends).")
        plan_name: str = Field(default="generated")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.plan.executor import PlanExecutor, _import_all_skills
        from phone_designer.plan.model import Plan, Step
        from phone_designer.skills._registry import registry

        # register the WHOLE skill library before the per-step name pre-check —
        # otherwise a skill not yet imported is wrongly rejected as 'unknown'.
        _import_all_skills()

        spec_errors: list[str] = []
        steps: list[Step] = []
        for i, item in enumerate(args.spec):
            if not isinstance(item, dict):
                spec_errors.append(f"step {i}: not an object")
                continue
            op = item.get("op") or item.get("skill")
            sargs = item.get("args") or {}
            if not op:
                spec_errors.append(f"step {i}: missing 'op'/'skill'")
                continue
            if not isinstance(sargs, dict):
                # a non-object 'args' would crash dict(sargs) — isolate it.
                spec_errors.append(
                    f"step {i}: 'args' must be an object, got {type(sargs).__name__}")
                continue
            try:
                registry.get(op)   # pre-check the skill exists (clear error)
            except Exception:  # noqa: BLE001
                spec_errors.append(f"step {i}: unknown skill '{op}'")
                continue
            steps.append(Step(id=f"s{i}_{op}", skill=op, args=dict(sargs)))

        out_body = body
        step_report: list[dict] = []
        if steps:
            plan = Plan(plan_name=args.plan_name, steps=steps,
                        continue_on_step_failure=True)
            result = PlanExecutor(plan).run(initial_body=body)
            if result.final_body is not None:
                out_body = result.final_body
            for s in plan.steps:
                status = getattr(s.status, "value", str(s.status))
                step_report.append({
                    "op": s.skill, "status": status,
                    "error": (s.failure.message if getattr(s, "failure", None)
                              else None),
                })

        n_ok = sum(1 for s in step_report if s["status"] == "pass")
        is_solid = False
        volume_mm3 = 0.0
        if out_body is not None:
            try:
                from phone_designer.skills.inspect.mass_properties import (
                    MassProperties,
                )
                volume_mm3 = float(
                    MassProperties().apply(out_body, {}).extras.get("volume_mm3")
                    or 0.0)
                # volume>0 alone LIES for an OPEN shell: VolumeProperties_s applies
                # the divergence theorem and returns a nonzero pseudo-mass for an
                # un-capped skin (e.g. create_open_surface). Topology never lies —
                # also require at least one actual TopAbs_SOLID sub-shape.
                from OCP.TopAbs import TopAbs_SOLID
                from OCP.TopExp import TopExp_Explorer
                shp = (out_body.wrapped if hasattr(out_body, "wrapped")
                       else out_body)
                has_solid = TopExp_Explorer(shp, TopAbs_SOLID).More()
                is_solid = volume_mm3 > 0.0 and has_solid
            except Exception as exc:  # noqa: BLE001
                spec_errors.append(f"solid validation failed: {type(exc).__name__}")

        ok = bool(out_body is not None and not spec_errors
                  and n_ok == len(step_report)
                  and (not args.validate_solid or is_solid))

        generated = {
            "n_steps": len(step_report),
            "n_ok": n_ok,
            "ok": ok,
            "is_solid": is_solid,
            "volume_mm3": round(volume_mm3, 3),
            "bbox_mm": _bbox_mm(out_body) if out_body is not None else None,
            "steps": step_report,
            "spec_errors": spec_errors,
            "grade": "constructed",
        }
        return SkillResult(body=out_body, history=EntityHistoryMap(),
                           extras={"generated": generated})
