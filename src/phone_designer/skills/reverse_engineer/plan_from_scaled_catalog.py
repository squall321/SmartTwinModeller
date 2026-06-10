"""plan_from_scaled_catalog — thin macro wrapper.

Run :func:`vary_catalog_ex` on the supplied feature_catalog, optionally
enforce geometric constraints in CATALOG space (P7), optionally normalize
the varied catalog's derived fields (P6), then drive
:class:`PlanFromFeatureCatalog` with the result. Useful for "give me a 2×
scaled variant of this part" prompts where the caller wants both the varied
catalog AND the generated plan in a single skill call.

extras carries:
    - ``varied_catalog``        — the dict produced by vary_catalog_ex
                                  (post constraint clamping, when active)
    - ``normalized_catalog``    — the catalog actually given to the planner
                                  (== varied_catalog when normalize=False)
    - ``generated_plan``        — the plan dict from PlanFromFeatureCatalog
    - ``variation_warnings``    — normalize_varied_catalog warnings (P6)
    - ``unresolved_overrides``  — dotted override keys that did not resolve
                                  (P8 — no more silent typo swallowing)
    - ``constraint_violations`` — P7 violation records (containment /
                                  min-wall), each with the policy action

P7 ``constraints`` dict keys (all optional)::

    {
      "preserve_wall_thickness": bool,   # exempt base_thickness_mm from
                                         # the uniform scale_factor
      "min_wall_mm": float | None,       # lateral wall floor in mm
      "containment": "warn"|"clamp"|"fail",  # violation policy (default warn)
    }

The containment pre-check runs in catalog space: each hole/pocket envelope
(entry_origin/axis_origin ± d/2 laterally, depth along axis_dir) must lie
inside the catalog's (scaled) ``initial_bbox_mm``; min-wall additionally
requires nearest-lateral-bbox-face distance minus d/2 >= min_wall_mm.
Policy 'warn' records, 'clamp' pulls the feature inward / shrinks its depth
minimally and records, 'fail' raises ValueError listing every violation.
Geometry remains the final truth (P3 validate_rebuilt_body) — this is the
cheap early gate.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.reverse_engineer.normalize_varied_catalog import (
    normalize_catalog,
)
from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
    PlanFromFeatureCatalog,
)
from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
    vary_catalog_ex,
)

_ALLOWED_CONSTRAINT_KEYS = frozenset({
    "preserve_wall_thickness", "min_wall_mm", "containment",
})
_CONTAINMENT_POLICIES = ("warn", "clamp", "fail")

# |axis_dir component| above which a coordinate axis is treated as the
# drilling axis (no lateral wall requirement there — the entry legitimately
# opens onto the bbox face).
_AXIAL_DOMINANCE = 0.7

_EPS = 1e-9


def _validate_constraints(constraints: dict) -> dict:
    """Reject typo'd / out-of-vocabulary constraint keys & values (the P8
    no-silent-swallowing principle applied to P7's own arg)."""
    unknown = sorted(set(constraints) - _ALLOWED_CONSTRAINT_KEYS)
    if unknown:
        raise ValueError(
            f"plan_from_scaled_catalog: unknown constraints key(s) {unknown} "
            f"— allowed: {sorted(_ALLOWED_CONSTRAINT_KEYS)}"
        )
    policy = constraints.get("containment", "warn")
    if policy not in _CONTAINMENT_POLICIES:
        raise ValueError(
            f"plan_from_scaled_catalog: constraints.containment={policy!r} "
            f"— must be one of {_CONTAINMENT_POLICIES}"
        )
    out = dict(constraints)
    out["containment"] = policy
    return out


# ──────────────────────────────────────────────────────────────────────────────
# P7 containment / min-wall pre-check (pure catalog-space math)


def _feature_radius_mm(feat: dict) -> float | None:
    """Envelope radius of a hole/pocket from whichever diameter fields it
    carries. None when no usable diameter exists (feature skipped)."""
    diams = [float(d) for d in (feat.get("diameters_mm") or [])
             if isinstance(d, (int, float))]
    for key in ("top_d_mm", "bottom_d_mm", "diameter_mm", "profile_diameter_mm"):
        v = feat.get(key)
        if isinstance(v, (int, float)):
            diams.append(float(v))
    if not diams:
        return None
    return max(diams) / 2.0


def _feature_geometry(
    feat: dict,
) -> tuple[tuple[float, float, float], tuple[float, float, float], float, float] | None:
    """(origin, unit_axis, depth_mm, radius_mm) — or None when the feature
    lacks the fields needed for an envelope check."""
    origin = feat.get("entry_origin") or feat.get("axis_origin")
    if not isinstance(origin, (list, tuple)) or len(origin) < 3:
        return None
    radius = _feature_radius_mm(feat)
    if radius is None:
        return None
    axis = feat.get("axis_dir") or [0.0, 0.0, -1.0]
    try:
        ax = (float(axis[0]), float(axis[1]), float(axis[2]))
        o = (float(origin[0]), float(origin[1]), float(origin[2]))
    except (TypeError, ValueError, IndexError):
        return None
    n = math.sqrt(ax[0] ** 2 + ax[1] ** 2 + ax[2] ** 2)
    if n < 1e-12:
        ax = (0.0, 0.0, -1.0)
    else:
        ax = (ax[0] / n, ax[1] / n, ax[2] / n)
    depth = feat.get("entry_depth_mm")
    if not isinstance(depth, (int, float)):
        depth = feat.get("depth_mm")
    depth = float(depth) if isinstance(depth, (int, float)) else 0.0
    return o, ax, max(depth, 0.0), radius


def _envelope(o, ax, depth: float, radius: float):
    """Cylinder AABB: per-axis (env_min_i, env_max_i, lateral_factor_i)."""
    end = (o[0] + ax[0] * depth, o[1] + ax[1] * depth, o[2] + ax[2] * depth)
    out = []
    for i in range(3):
        lat = math.sqrt(max(0.0, 1.0 - ax[i] * ax[i]))
        lo = min(o[i], end[i]) - radius * lat
        hi = max(o[i], end[i]) + radius * lat
        out.append((lo, hi, lat))
    return out


def _check_and_clamp_features(
    catalog: dict, constraints: dict,
) -> list[dict]:
    """Containment + min-wall pre-check over every hole and pocket, in
    catalog space against the (scaled) ``initial_bbox_mm``.

    Policy 'warn'  → record violations, mutate nothing.
    Policy 'clamp' → shift entry_origin/axis_origin inward and/or shrink
                     entry_depth_mm/depth_mm minimally, record what changed.
    Policy 'fail'  → collect everything, then raise ValueError listing all.

    Returns the violation records (also recorded by the caller in
    extras['constraint_violations']).
    """
    policy: str = constraints["containment"]
    min_wall = constraints.get("min_wall_mm")
    min_wall = float(min_wall) if isinstance(min_wall, (int, float)) else 0.0

    bbox = catalog.get("initial_bbox_mm")
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 6:
        return []  # nothing to check against — geometry gate (P3) owns it
    try:
        bb = [float(c) for c in bbox[:6]]
    except (TypeError, ValueError):
        return []
    bmin = (bb[0], bb[1], bb[2])
    bmax = (bb[3], bb[4], bb[5])

    violations: list[dict] = []

    for family in ("holes", "pockets"):
        feats = catalog.get(family) or []
        if not isinstance(feats, list):
            continue
        for fi, feat in enumerate(feats):
            if not isinstance(feat, dict):
                continue
            geo = _feature_geometry(feat)
            if geo is None:
                continue
            o, ax, depth, radius = geo
            env = _envelope(o, ax, depth, radius)
            tol = 1e-6 * max(bmax[0] - bmin[0], bmax[1] - bmin[1],
                             bmax[2] - bmin[2], 1.0)

            # per-axis required bounds: containment everywhere; the lateral
            # axes additionally owe min_wall of material.
            problems: list[str] = []
            shifts = [0.0, 0.0, 0.0]
            for i, axis_name in enumerate("xyz"):
                lateral = abs(ax[i]) <= _AXIAL_DOMINANCE
                wall = min_wall if lateral else 0.0
                lo_bound = bmin[i] + wall
                hi_bound = bmax[i] - wall
                lo, hi, _lat = env[i]
                under = lo_bound - lo  # >0 ⇒ pokes out / too close on low side
                over = hi - hi_bound   # >0 ⇒ pokes out / too close on high side
                if under > tol or over > tol:
                    kind = "min_wall" if (lateral and wall > 0.0 and
                                          lo >= bmin[i] - tol and
                                          hi <= bmax[i] + tol) else "containment"
                    problems.append(
                        f"{axis_name}: envelope [{lo:.4f}, {hi:.4f}] vs "
                        f"bound [{lo_bound:.4f}, {hi_bound:.4f}] ({kind})"
                    )
                    if lateral:
                        if under > tol and over <= tol:
                            shifts[i] = under
                        elif over > tol and under <= tol:
                            shifts[i] = -over
                        else:
                            # Feature (plus walls) wider than the box —
                            # center it; the residual is unfixable by
                            # translation alone.
                            shifts[i] = (under - over) / 2.0
                    else:
                        # Drilling-axis coordinate: only pull the ENTRY
                        # origin back inside the box; far-end (depth)
                        # overshoot is fixed by the depth shrink below,
                        # never by dragging the entry out the other side.
                        if o[i] < bmin[i] - tol:
                            shifts[i] = bmin[i] - o[i]
                        elif o[i] > bmax[i] + tol:
                            shifts[i] = bmax[i] - o[i]

            if not problems:
                continue

            record: dict[str, Any] = {
                "feature": f"{family}.{fi}",
                "policy": policy,
                "min_wall_mm": min_wall if min_wall > 0.0 else None,
                "details": problems,
            }

            if policy == "clamp":
                applied: dict[str, Any] = {}
                if any(abs(s) > _EPS for s in shifts):
                    for key in ("entry_origin", "axis_origin"):
                        val = feat.get(key)
                        if isinstance(val, (list, tuple)) and len(val) >= 3:
                            feat[key] = [
                                float(val[j]) + shifts[j] for j in range(3)
                            ] + [float(v) for v in val[3:]]
                    applied["shift_mm"] = [round(s, 6) for s in shifts]
                    o = (o[0] + shifts[0], o[1] + shifts[1], o[2] + shifts[2])
                # depth shrink: keep the centerline endpoint inside the bbox
                # after the shift (entry side stays where the shift put it).
                if depth > 0.0:
                    t_allow = depth
                    for i in range(3):
                        if abs(ax[i]) < 1e-9:
                            continue
                        bound = bmax[i] if ax[i] > 0.0 else bmin[i]
                        t_allow = min(t_allow, (bound - o[i]) / ax[i])
                    t_allow = max(t_allow, 0.0)
                    if t_allow < depth - _EPS:
                        for key in ("entry_depth_mm", "depth_mm"):
                            if isinstance(feat.get(key), (int, float)):
                                feat[key] = round(
                                    min(float(feat[key]), t_allow), 6,
                                )
                        applied["depth_mm"] = round(t_allow, 6)
                record["action"] = "clamped"
                record["clamp"] = applied
            else:
                record["action"] = "recorded"

            violations.append(record)

    if policy == "fail" and violations:
        lines = "; ".join(
            f"{v['feature']}: {', '.join(v['details'])}" for v in violations
        )
        raise ValueError(
            f"plan_from_scaled_catalog: constraints.containment='fail' — "
            f"{len(violations)} feature(s) violate the catalog-space "
            f"envelope/min-wall pre-check: {lines}"
        )
    return violations


@skill(
    name="plan_from_scaled_catalog",
    category="reverse_engineer",
    level="atomic",
    summary="Vary a feature_catalog (uniform scale_factor + per_feature_scale "
            "+ absolute_overrides), optionally enforce catalog-space "
            "constraints (preserve_wall_thickness / min_wall_mm / containment "
            "warn|clamp|fail), normalize derived fields (standard re-match, "
            "pattern coherence), and then convert the result into a Plan YAML "
            "via plan_from_feature_catalog. Body unchanged. extras carries "
            "'varied_catalog', 'normalized_catalog', 'generated_plan', "
            "'variation_warnings', 'unresolved_overrides' and "
            "'constraint_violations'.",
    selector_kinds=[],
    history_rules={},
    produces_features=["varied_catalog", "generated_plan"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class PlanFromScaledCatalog(SkillBase):
    class Args(BaseModel):
        # P8 (2026-06-10): unknown arg keys raise ValidationError instead of
        # being silently dropped.
        model_config = ConfigDict(extra="forbid")

        catalog: dict = Field(
            default_factory=dict,
            description="The feature_catalog dict to vary and re-plan.",
        )
        scale_factor: float | None = Field(
            default=None,
            description="Uniform multiplier on every dimensional field (see "
                        "vary_feature_catalog for the full field list).",
        )
        per_feature_scale: dict = Field(
            default_factory=dict,
            description="Dotted-key → multiplier overrides applied after "
                        "scale_factor.",
        )
        absolute_overrides: dict = Field(
            default_factory=dict,
            description="Dotted-key → absolute value overrides applied LAST.",
        )
        base_step_kind: Literal["box", "import_step", "preserve_brep"] = "box"
        normalize: bool = Field(
            default=True,
            description="P6: run normalize_varied_catalog on the varied "
                        "catalog (standard re-match, pattern coherence, "
                        "counterbore invariants, mirror coherence) before "
                        "planning. Opt-out with False to plan from the raw "
                        "varied catalog.",
        )
        constraints: dict | None = Field(
            default=None,
            description="P7 constraint dict: {'preserve_wall_thickness': "
                        "bool, 'min_wall_mm': float|None, 'containment': "
                        "'warn'|'clamp'|'fail'}. None ⇒ no constraint "
                        "processing. Violations are surfaced in "
                        "extras['constraint_violations'].",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        constraints: dict | None = None
        exclude_fields: set[str] | None = None
        if args.constraints is not None:
            constraints = _validate_constraints(args.constraints)
            if constraints.get("preserve_wall_thickness"):
                # P7: the housing grows, the wall does not.
                exclude_fields = {"base_thickness_mm"}

        varied, vary_warnings = vary_catalog_ex(
            args.catalog,
            scale_factor=args.scale_factor,
            per_feature_scale=args.per_feature_scale,
            absolute_overrides=args.absolute_overrides,
            exclude_fields=exclude_fields,
        )
        unresolved = vary_warnings.get("unresolved_overrides") or []

        constraint_violations: list[dict] = []
        if constraints is not None:
            # May mutate `varied` in place (clamp) or raise (fail).
            constraint_violations = _check_and_clamp_features(
                varied, constraints,
            )

        if args.normalize:
            planner_catalog, variation_warnings = normalize_catalog(
                varied, scale_hint=args.scale_factor,
            )
        else:
            planner_catalog, variation_warnings = varied, []

        # COMPLEX-CAD pass-25 (2026-06-10, plan item P5): this macro KNOWS
        # the uniform scale of the catalog it hands the planner — thread it
        # through so the planner's absolute-mm clamps (200 mm depth caps,
        # 20 mm pad cap, _MIN_EMITTED_CUT_MM3 floor, …) and the
        # live-body-measured base dims in _pick_base_shape scale with the
        # variant. Per-feature-only / override-only edits keep 1.0 — they
        # are local tweaks, not a uniform rescale of the part.
        plan_res = PlanFromFeatureCatalog().apply(body, {
            "catalog": planner_catalog,
            "base_step_kind": args.base_step_kind,
            "dimension_scale": (
                float(args.scale_factor)
                if args.scale_factor is not None else 1.0
            ),
        })
        generated_plan = plan_res.extras.get("generated_plan")
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "varied_catalog": varied,
                "normalized_catalog": planner_catalog,
                "generated_plan": generated_plan,
                "variation_warnings": variation_warnings,
                "unresolved_overrides": unresolved,
                "constraint_violations": constraint_violations,
            },
        )
