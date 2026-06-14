"""generate_variant_family — macro, read-only (Pillar VARIANTS, 2026-06-14).

Sweep a SINGLE design driver over N target values and produce N *validated*
variant rebuilds — the "parametric family" a designer asks for ("give me this
linkrod at 1.0×, 1.25×, 1.5× and 2.0× length"). Each variant is built through
the EXACT pipeline ``apply_variant_drivers`` already pins (named-driver →
relation-propagated absolute overrides → normalize → plan), then the plan is
EXECUTED, the rebuilt body is geometrically validated
(``validate_rebuilt_body``: features_lost + BREP health) and its fidelity is
measured RELATIVE to the value-1.0 (or value[0]) rebuild
(``geometry_deviation`` hausdorff, or a catalog match ratio).

Why fidelity is RELATIVE, not absolute (the roadmap honest caveat): the base
reconstruction is itself imperfect (box-mode rebuilds drop some catalog holes,
round-body inscribed-circle guards, etc.), so a perfectly COHERENT edit still
rebuilds with non-zero deviation against the original STEP. Measuring each
variant against the value-1.0 REBUILD isolates the variation's own fidelity:
the scale-1.0 variant reads ~0 deviation (it IS the baseline), and the others
report how far the edit moved the geometry — a number that should track the
edit magnitude, not the base recon error. Read fidelity_vs_baseline relative,
never absolute.

Driver values — supply ONE of:
  * ``values`` : an explicit list of ABSOLUTE target values for the driver
    (e.g. ``[5.02, 6.28, 7.53, 10.04]`` for housing_length in mm), OR
  * ``sweep``  : ``{"start": s, "stop": e, "steps": n}`` → ``n`` evenly spaced
    absolute values from ``s`` to ``e`` inclusive (n>=2).

The driver itself is either a ``key_dimensions`` NAME (e.g.
``"housing_length"``, ``"primary_bore_diameter"`` — resolved through
``identify_key_dimensions`` exactly as ``apply_variant_drivers`` does) or a raw
dotted_key the variation pipeline resolves directly. A name is preferred (it
carries envelope min-side conversion + relation propagation); a dotted_key is
applied as a raw absolute override (still relation-propagated when it matches a
relation member).

extras schema::

    {"variant_family": {
        "driver":        str,            # the driver name / dotted_key swept
        "driver_role":   str | None,     # resolved key_dimensions role
        "fidelity_metric": "hausdorff_mm" | "catalog_match_ratio",
        "baseline_value": float,         # the value used as the fidelity ref
        "variants": [
            {"value": float,
             "valid": bool,              # features_lost==0 AND brep_valid
             "bbox": [dx, dy, dz] | None,
             "volume_mm3": float | None,
             "fidelity_vs_baseline": float | None,  # RELATIVE to baseline
             "features_expected": int,
             "features_lost": int,
             "brep_valid": bool | None,
             "drivers_unresolved": [str, ...],
             "plan_path": str | None,    # per-variant plan file (when written)
             "error": str | None},
            ...
        ],
        "summary": {"n": int, "n_valid": int},
     }}

Body unchanged (post ``body_present``) — the supplied body is only the base /
fidelity anchor; each variant rebuilds its OWN body internally and discards it.
The input catalog is never mutated.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.reverse_engineer.apply_variant_drivers import (
    apply_variant_drivers,
)
from phone_designer.skills.reverse_engineer.identify_key_dimensions import (
    identify_key_dimensions,
)
from phone_designer.skills.reverse_engineer.normalize_varied_catalog import (
    normalize_catalog,
)
from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
    PlanFromFeatureCatalog,
)
from phone_designer.skills.reverse_engineer.recover_design_relations import (
    recover_design_relations,
)
from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
    vary_catalog_ex,
)


# ──────────────────────────────────────────────────────────────────────────────
# value resolution


def _as_float(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_sweep_values(
    values: list | None, sweep: dict | None,
) -> list[float]:
    """Turn the (mutually exclusive) ``values`` / ``sweep`` args into an
    explicit ordered list of absolute driver targets.

    Exactly one of the two must be supplied. ``sweep`` produces ``steps``
    evenly spaced values from ``start`` to ``stop`` INCLUSIVE (steps>=2).
    """
    has_values = values is not None
    has_sweep = sweep is not None
    if has_values == has_sweep:
        raise ValueError(
            "generate_variant_family: supply exactly one of 'values' (a list "
            "of absolute targets) or 'sweep' ({start, stop, steps})"
        )
    if has_values:
        out: list[float] = []
        for v in values:
            f = _as_float(v)
            if f is None:
                raise ValueError(
                    f"generate_variant_family: non-numeric value in 'values': "
                    f"{v!r}"
                )
            out.append(f)
        if not out:
            raise ValueError("generate_variant_family: 'values' is empty")
        return out

    start = _as_float(sweep.get("start"))
    stop = _as_float(sweep.get("stop"))
    steps = sweep.get("steps")
    if start is None or stop is None or not isinstance(steps, int):
        raise ValueError(
            "generate_variant_family: 'sweep' needs numeric start/stop and an "
            f"int steps, got {sweep!r}"
        )
    if steps < 2:
        raise ValueError(
            "generate_variant_family: 'sweep.steps' must be >= 2 "
            f"(got {steps})"
        )
    span = stop - start
    return [start + span * i / (steps - 1) for i in range(steps)]


# ──────────────────────────────────────────────────────────────────────────────
# geometry helpers (OCCT — measured on the rebuilt bodies)


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _bbox_extents(body: Any) -> list[float] | None:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    try:
        shape = _occt_shape(body)
        bb = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, bb)
        if bb.IsVoid():
            return None
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        return [
            round(xmax - xmin, 6),
            round(ymax - ymin, 6),
            round(zmax - zmin, 6),
        ]
    except Exception:
        return None


def _volume_mm3(body: Any) -> float | None:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    try:
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(_occt_shape(body), props)
        return round(float(props.Mass()), 6)
    except Exception:
        return None


def _write_step(body: Any, out_path: str) -> bool:
    """Write ``body`` to a plain STEP file (fidelity reference for
    geometry_deviation). Returns True on success."""
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer

    try:
        writer = STEPControl_Writer()
        status = writer.Transfer(
            _occt_shape(body), STEPControl_StepModelType.STEPControl_AsIs,
        )
        if status != IFSelect_ReturnStatus.IFSelect_RetDone:
            return False
        return writer.Write(out_path) == IFSelect_ReturnStatus.IFSelect_RetDone
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# pure-ish per-value build + execute (uses OCCT through the plan executor)


def _build_one(
    body: Any,
    catalog: dict,
    relations: list[dict] | None,
    driver: str,
    is_name: bool,
    value: float,
    constraints: dict | None,
    base_step_kind: str,
    plan_out_path: str | None,
) -> tuple[dict | None, Any, list[str]]:
    """apply_variant_drivers (or raw override) → normalize → plan → execute.

    Returns ``(plan_dict, rebuilt_body, drivers_unresolved)``. ``rebuilt_body``
    is None when planning produced no steps or the executor produced no body.
    """
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.plan.model import Plan

    drivers_unresolved: list[str] = []
    if is_name:
        resolution = apply_variant_drivers(
            catalog, {driver: value}, relations=relations, propagate=True,
        )
        absolute_overrides = resolution["absolute_overrides"]
        drivers_unresolved = list(resolution["drivers_unresolved"])
    else:
        # raw dotted_key — apply as a direct absolute override (the variation
        # pipeline will report it in unresolved_overrides if it does not land).
        absolute_overrides = {driver: value}

    varied, vary_warnings = vary_catalog_ex(
        catalog, absolute_overrides=absolute_overrides,
    )
    if not is_name:
        drivers_unresolved = list(
            vary_warnings.get("unresolved_overrides") or []
        )

    planner_catalog, _warnings = normalize_catalog(varied, scale_hint=None)

    plan_args: dict[str, Any] = {
        "catalog": planner_catalog,
        "base_step_kind": base_step_kind,
    }
    if plan_out_path is not None:
        plan_args["plan_out_path"] = plan_out_path
    plan_res = PlanFromFeatureCatalog().apply(body, plan_args)
    plan_dict = plan_res.extras.get("generated_plan")
    if not isinstance(plan_dict, dict) or not plan_dict.get("steps"):
        return plan_dict, None, drivers_unresolved

    plan = Plan.model_validate(plan_dict)
    exec_result = PlanExecutor(plan).run()
    return plan_dict, exec_result.final_body, drivers_unresolved


def _fidelity_vs_step(
    body: Any, reference_step_path: str, linear_deflection_mm: float,
) -> float | None:
    """hausdorff_mm of ``body`` vs the reference STEP (bbox-centre aligned —
    box-mode rebuilds share a frame, the alignment is a no-op there but keeps
    the comparison robust to any base-frame offset). None on failure."""
    from phone_designer.skills.inspect.geometry_deviation import GeometryDeviation

    try:
        dev = GeometryDeviation().apply(body, {
            "reference_step_path": reference_step_path,
            "align": "bbox_center",
            "linear_deflection_mm": linear_deflection_mm,
        }).extras["geometry_deviation"]
        return round(float(dev["hausdorff_mm"]), 6)
    except Exception:
        return None


def _validate(body: Any, plan_dict: dict) -> tuple[int, int, bool | None]:
    """validate_rebuilt_body features + brep → (features_expected,
    features_lost_count, brep_valid)."""
    from phone_designer.skills.inspect.validate_rebuilt_body import (
        ValidateRebuiltBody,
    )

    rep = ValidateRebuiltBody().apply(body, {
        "plan": plan_dict, "checks": ["features", "brep"],
    }).extras["rebuilt_validation"]
    return (
        int(rep.get("features_expected") or 0),
        len(rep.get("features_lost") or []),
        rep.get("brep_valid"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="generate_variant_family",
    category="reverse_engineer",
    level="macro",
    summary="Sweep ONE design driver (a key_dimensions name like "
            "'housing_length' / 'primary_bore_diameter', or a raw dotted_key) "
            "over N absolute values (explicit 'values' list or a "
            "'sweep' {start, stop, steps}) and produce N VALIDATED variant "
            "rebuilds. Each value: apply_variant_drivers (relation-propagated) "
            "-> normalize -> plan -> execute -> validate_rebuilt_body "
            "(features_lost==0 AND brep_valid) + geometry_deviation hausdorff "
            "measured RELATIVE to the value[0] rebuild (the base recon is "
            "imperfect, so read fidelity relative not absolute). extras carries "
            "'variant_family' (driver, per-variant valid/bbox/volume/"
            "fidelity_vs_baseline/plan_path, summary n/n_valid). Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["variant_family"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.6,
    post_conditions=[PostCondition(kind="body_present")],
)
class GenerateVariantFamily(SkillBase):
    class Args(BaseModel):
        # extra='forbid' — unknown keys are typos, not silently-dropped extras
        # (the P8 no-silent-swallowing principle the variation pipeline pins).
        model_config = ConfigDict(extra="forbid")

        catalog: dict | None = Field(
            default=None,
            description="The feature_catalog dict to vary. When None, the last "
                        "catalog shared by PlanFromFeatureCatalog on this "
                        "process is used.",
        )
        driver: str = Field(
            ...,
            description="The single driver to sweep — a key_dimensions name "
                        "(identify_key_dimensions, e.g. 'housing_length') or a "
                        "raw vary_feature_catalog dotted_key.",
        )
        values: list | None = Field(
            default=None,
            description="Explicit list of ABSOLUTE target values for the "
                        "driver. Supply exactly one of values / sweep.",
        )
        sweep: dict | None = Field(
            default=None,
            description="{'start','stop','steps'} → steps (>=2) evenly spaced "
                        "absolute values, inclusive. Supply exactly one of "
                        "values / sweep.",
        )
        relations: list | None = Field(
            default=None,
            description="design_relations from recover_design_relations. When "
                        "None they are recovered from the catalog once and "
                        "reused for every variant.",
        )
        constraints: dict | None = Field(
            default=None,
            description="Pass-through constraints (reserved for "
                        "apply_variant_drivers / planning policy). Currently "
                        "carried for forward-compat; does not alter the "
                        "named-driver override set.",
        )
        base_step_kind: str = Field(
            default="box",
            description="Base step kind handed to plan_from_feature_catalog "
                        "('box' | 'import_step' | 'preserve_brep').",
        )
        fidelity_linear_deflection_mm: float = Field(
            default=0.1, gt=0.0,
            description="Tessellation chord tolerance for the "
                        "geometry_deviation fidelity measurement.",
        )
        write_plans: bool = Field(
            default=True,
            description="Write each variant's plan to its OWN file under a temp "
                        "dir (per-variant plan_out_path) so the shared "
                        "plans/reconstructed_plan.yaml is never clobbered. The "
                        "path is reported in each variant's plan_path.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        catalog = args.catalog
        if catalog is None:
            catalog = getattr(PlanFromFeatureCatalog, "_LAST_CATALOG", None)
        catalog = catalog or {}

        sweep_values = resolve_sweep_values(args.values, args.sweep)

        # Resolve the driver once: is it a key_dimensions name, and what role?
        key_dims = identify_key_dimensions(catalog)
        by_name = {e["name"]: e for e in key_dims}
        is_name = args.driver in by_name
        driver_role = (
            by_name[args.driver].get("role") if is_name else None
        )

        relations = args.relations
        if relations is None:
            relations = recover_design_relations(catalog)

        baseline_value = sweep_values[0]

        tmpdir: str | None = None
        if args.write_plans:
            tmpdir = tempfile.mkdtemp(prefix="variant_family_")

        # Build the baseline (value[0]) first — it is the fidelity reference.
        # Every other variant's hausdorff is measured against THIS rebuild's
        # geometry (written to a temp STEP), making fidelity RELATIVE to the
        # value-1.0 rebuild per the roadmap honest caveat.
        variants: list[dict] = []
        baseline_step: str | None = None
        ref_dir = tmpdir or tempfile.mkdtemp(prefix="variant_family_ref_")

        n_valid = 0
        for idx, value in enumerate(sweep_values):
            plan_path = (
                os.path.join(tmpdir, f"variant_{idx}.yaml")
                if tmpdir is not None else None
            )
            record: dict[str, Any] = {
                "value": round(float(value), 6),
                "valid": False,
                "bbox": None,
                "volume_mm3": None,
                "fidelity_vs_baseline": None,
                "features_expected": 0,
                "features_lost": 0,
                "brep_valid": None,
                "drivers_unresolved": [],
                "plan_path": plan_path,
                "error": None,
            }
            try:
                plan_dict, rebuilt, unresolved = _build_one(
                    body, catalog, relations, args.driver, is_name, value,
                    args.constraints, args.base_step_kind, plan_path,
                )
                record["drivers_unresolved"] = unresolved
                if rebuilt is None:
                    record["error"] = (
                        "rebuild produced no body (no plan steps or executor "
                        "returned no final body)"
                    )
                    variants.append(record)
                    continue

                record["bbox"] = _bbox_extents(rebuilt)
                record["volume_mm3"] = _volume_mm3(rebuilt)
                feat_exp, feat_lost, brep_valid = _validate(rebuilt, plan_dict)
                record["features_expected"] = feat_exp
                record["features_lost"] = feat_lost
                record["brep_valid"] = brep_valid
                record["valid"] = bool(feat_lost == 0 and brep_valid is True)

                # establish / reuse the fidelity reference STEP.
                if idx == 0:
                    baseline_step = os.path.join(ref_dir, "baseline.step")
                    if not _write_step(rebuilt, baseline_step):
                        baseline_step = None
                if baseline_step is not None:
                    record["fidelity_vs_baseline"] = _fidelity_vs_step(
                        rebuilt, baseline_step,
                        args.fidelity_linear_deflection_mm,
                    )
                if record["valid"]:
                    n_valid += 1
            except Exception as exc:  # noqa: BLE001 — surface per-variant
                record["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            variants.append(record)

        fidelity_metric = "hausdorff_mm"
        variant_family = {
            "driver": args.driver,
            "driver_role": driver_role,
            "fidelity_metric": fidelity_metric,
            "baseline_value": round(float(baseline_value), 6),
            "variants": variants,
            "summary": {"n": len(variants), "n_valid": n_valid},
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"variant_family": variant_family},
        )
