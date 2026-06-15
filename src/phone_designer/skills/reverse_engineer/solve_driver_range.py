"""solve_driver_range — macro, read-only (Pillar VARIANTS, phase-4 2026-06-15).

For a single NAMED driver, estimate the FEASIBLE RANGE ``[min, max]`` over which
the parametric variant stays valid — features survive (features_lost == 0), the
min-wall floor is respected, no containment violation / self-intersection —
WITHOUT brute-force rebuilding every candidate value.

Approach (analytic envelope + a few verify-by-rebuild probes):

  1. ANALYTIC ENVELOPE. The variation pipeline already owns a pure catalog-space
     containment / min-wall pre-check (``plan_from_scaled_catalog.
     _check_and_clamp_features`` with policy ``"fail"``): it raises the moment a
     hole / pocket envelope (plus its min-wall margin) pokes outside the scaled
     ``initial_bbox_mm``. We reuse that as a cheap VALIDITY ORACLE on the
     catalog produced by ``apply_variant_drivers`` at a candidate driver value —
     no OCCT rebuild needed. Bisection between the seed value (known valid) and a
     far probe in each direction finds the tightest valid bound to a small
     resolution. This handles the dims whose validity is governed by the box /
     wall envelope: envelope length/width/height, bore diameter, wall thickness.

  2. PROBE-VERIFY. The analytic bound is then CONFIRMED by an actual rebuild
     through ``generate_variant_family`` (the exact apply_variant_drivers →
     normalize → plan → execute → validate_rebuilt_body pipeline a real variant
     uses) at FOUR values: just inside each bound (expect valid) and just
     outside (expect invalid OR clamped). The probes either ratify the analytic
     range (``method="probed"``) or, if a probe disagrees, the range is reported
     ``method="analytic"`` with the disagreement noted (honest — analytic only).

  3. HONEST FALLBACK. For COUPLED / FREEFORM / LOFT-BASE drivers the analytic
     envelope is unreliable (a loft cross-section's feasible span is not a box
     containment problem; a coupled driver drags several fields whose joint
     envelope the pre-check does not model). Those return
     ``feasible_range=None, method="none"`` with a ``reason`` — we DO NOT fake a
     range. Triggers:
       * the driver name is not a key_dimensions name (a raw freeform handle);
       * the driver's role is not one of the box/wall-governed roles
         (envelope / primary_bore / wall);  pitch / pocket are reported None
         (their feasibility is pattern / footprint coupled, not a box envelope);
       * the catalog carries loft / sweep / revolve features (a loft-base body —
         the box pre-check does not bound the freeform geometry).

emit / extras schema::

    {"driver_range": {
        "driver":         str,
        "driver_role":    str | None,
        "seed_value":     float | None,    # the catalog's current value
        "seed_slack_mm":  float,           # bbox slack absorbing extraction
                                           # noise so a REAL part's seed anchors
        "feasible_range": [min, max] | None,
        "method":         "analytic" | "probed" | "none",
        "reason":         str | None,      # WHY none / analytic-only (honest)
        "min_wall_mm":    float,
        "bounds_constrained": {"min": bool, "max": bool},  # real limit vs
                                                           # search-span edge
        "probes":         [{value, position, expected, valid, confirmed,
                            features_expected, features_lost, brep_valid,
                            clamped, error}],
     }}

``position`` ∈ {inside_min, outside_min, inside_max, outside_max}. A probe is
"valid" when the rebuild reports features_lost == 0 AND brep_valid (the same
gate generate_variant_family pins). An OUTSIDE probe at a CONSTRAINED bound is
"confirmed" when the rebuild DEGRADES past it (a feature dropped / lost / brep
broke / clamped); at a search-span-edge bound (expected="span_edge") the
outside probe is informational and confirmed by definition (an unbounded grow
direction never falsely demotes the range). method becomes "probed" only when
EVERY probe ratifies the analytic range, else stays "analytic" with a note.
Body unchanged (post body_present); the input catalog is never mutated.
"""
from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.reverse_engineer.apply_variant_drivers import (
    apply_variant_drivers,
)
from phone_designer.skills.reverse_engineer.generate_variant_family import (
    GenerateVariantFamily,
)
from phone_designer.skills.reverse_engineer.identify_key_dimensions import (
    identify_key_dimensions,
)
from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
    PlanFromFeatureCatalog,
)
from phone_designer.skills.reverse_engineer.plan_from_scaled_catalog import (
    _check_and_clamp_features,
)
from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
    vary_catalog_ex,
)


# Roles whose validity is governed by the box-containment / min-wall envelope —
# the only roles the analytic pre-check can bound. pitch / pocket are coupled to
# pattern / footprint geometry the pre-check does not model, so they fall back.
_ENVELOPE_GOVERNED_ROLES = ("envelope", "primary_bore", "wall")

# Catalog families that mark a loft / sweep / revolve (freeform) body. When any
# is non-empty the box pre-check cannot bound the freeform geometry → honest
# None fallback.
_FREEFORM_FAMILIES = ("loft_features", "sweep_features", "revolve_features")

# Default search span as a multiple of the seed value, and the bisection
# resolution as a fraction of the seed (1% — tight enough for a plausible bound,
# cheap: ~7 catalog-space evaluations per side).
_DEFAULT_SPAN_FACTOR = 4.0
_BISECT_RESOLUTION_FRAC = 0.01
_MAX_BISECT_ITERS = 24


def _as_float(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_freeform(catalog: dict) -> str | None:
    """Return the name of the first non-empty freeform family, else None."""
    for fam in _FREEFORM_FAMILIES:
        feats = catalog.get(fam)
        if isinstance(feats, list) and feats:
            return fam
    return None


def _inflate_bbox(catalog: dict, slack_mm: float) -> None:
    """In-place: grow ``initial_bbox_mm`` by ``slack_mm`` on every side.

    Absorbs the sub-mm extraction noise a REAL part carries (a hole/pocket
    envelope extracted a few microns proud of the bounding box). The slack is
    measured FROM THE SEED (see ``_seed_containment_slack``) so a candidate is
    judged "no worse contained than the seed", never granted free extra room.
    """
    bb = catalog.get("initial_bbox_mm")
    if isinstance(bb, list) and len(bb) >= 6 and slack_mm > 0.0:
        catalog["initial_bbox_mm"] = [
            bb[0] - slack_mm, bb[1] - slack_mm, bb[2] - slack_mm,
            bb[3] + slack_mm, bb[4] + slack_mm, bb[5] + slack_mm,
        ] + list(bb[6:])


def _catalog_valid_at(
    catalog: dict, driver: str, value: float, min_wall_mm: float,
    slack_mm: float = 0.0,
) -> bool:
    """Analytic validity oracle: True iff the variant catalog at ``value``
    passes the catalog-space containment / min-wall pre-check (with a uniform
    ``slack_mm`` bbox inflation that absorbs seed extraction noise).

    Builds the variant catalog exactly as apply_variant_drivers does (named
    driver → relation-propagated absolute overrides → vary), then runs
    ``_check_and_clamp_features`` with policy 'fail' — which raises on any
    containment / min-wall violation. No OCCT. Never mutates ``catalog``.
    """
    resolution = apply_variant_drivers(
        catalog, {driver: value}, propagate=True,
    )
    overrides = resolution["absolute_overrides"]
    if not overrides:
        return False  # driver did not resolve at this value
    varied, _warn = vary_catalog_ex(catalog, absolute_overrides=overrides)
    varied = copy.deepcopy(varied)
    _inflate_bbox(varied, slack_mm)
    constraints = {"containment": "fail", "min_wall_mm": min_wall_mm}
    try:
        # _check_and_clamp_features may MUTATE on clamp; policy 'fail' only
        # records then raises, but the copy above keeps us defensive.
        _check_and_clamp_features(varied, constraints)
        return True
    except ValueError:
        return False
    except Exception:
        # Any other failure (malformed catalog at this value) ⇒ treat invalid.
        return False


# Upper cap on the seed-noise slack: an ABSOLUTE floor plus a small fraction of
# the part's bbox diagonal (extraction noise scales with part size — a 0.06 mm
# pocket overhang on a 6 mm part is the same ~1% relative noise as a 6 mm
# overhang on a 600 mm part). Beyond this a seed is GENUINELY infeasible (a
# feature really pokes well outside the box), not noise — we do NOT paper over
# a real violation.
_MAX_SEED_SLACK_ABS_MM = 0.05
_MAX_SEED_SLACK_DIAG_FRAC = 0.012


def _bbox_diag(catalog: dict) -> float:
    bb = catalog.get("initial_bbox_mm")
    if isinstance(bb, (list, tuple)) and len(bb) >= 6:
        try:
            dx, dy, dz = (
                float(bb[3]) - float(bb[0]),
                float(bb[4]) - float(bb[1]),
                float(bb[5]) - float(bb[2]),
            )
            return (dx * dx + dy * dy + dz * dz) ** 0.5
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _max_seed_slack(catalog: dict) -> float:
    return max(
        _MAX_SEED_SLACK_ABS_MM,
        _MAX_SEED_SLACK_DIAG_FRAC * _bbox_diag(catalog),
    )


def _seed_containment_slack(
    catalog: dict, driver: str, seed: float, min_wall_mm: float,
) -> float | None:
    """Smallest bbox inflation (mm, <= ``_max_seed_slack``) that makes the SEED
    pass the containment pre-check, or None when even the cap is not enough
    (a genuinely infeasible seed — honest None upstream).

    A pristine synthetic seed needs slack 0.0. A real extracted part needs a
    sub-percent inflation to absorb envelope-vs-bbox extraction noise.
    """
    if _catalog_valid_at(catalog, driver, seed, min_wall_mm, slack_mm=0.0):
        return 0.0
    cap = _max_seed_slack(catalog)
    if not _catalog_valid_at(catalog, driver, seed, min_wall_mm, slack_mm=cap):
        return None  # even the cap fails ⇒ genuinely infeasible seed
    # bisect the smallest passing slack in (0, cap].
    lo, hi = 0.0, cap
    for _ in range(20):
        mid = 0.5 * (lo + hi)
        if _catalog_valid_at(catalog, driver, seed, min_wall_mm, slack_mm=mid):
            hi = mid
        else:
            lo = mid
    return hi


def _bisect_bound(
    catalog: dict,
    driver: str,
    seed: float,
    direction: int,
    min_wall_mm: float,
    span_factor: float,
    slack_mm: float = 0.0,
) -> tuple[float, bool]:
    """Find the feasible bound in ``direction`` (+1 = upper, -1 = lower).

    Returns ``(bound, constrained)``. ``constrained`` is True when a real
    valid→invalid TRANSITION was found inside the search span (the bound is a
    genuine geometric limit a rebuild probe should confirm degrades just past
    it). It is False when even the far probe stayed valid — then the bound is
    the SPAN EDGE, not a real constraint (e.g. growing an envelope has no
    containment ceiling; the limit is just where we stopped searching), and an
    "outside" probe there is informational only, not required to degrade.
    """
    far = seed * (span_factor if direction > 0 else (1.0 / span_factor))
    if far <= 0.0:
        far = max(seed + direction * seed, 1e-6)

    # If even the far probe is valid, the bound is >= far — span edge, no real
    # constraint transition was crossed.
    if _catalog_valid_at(catalog, driver, far, min_wall_mm, slack_mm):
        return far, False

    lo_valid = seed            # known valid
    hi_invalid = far           # known invalid
    resolution = max(abs(seed) * _BISECT_RESOLUTION_FRAC, 1e-4)
    for _ in range(_MAX_BISECT_ITERS):
        mid = 0.5 * (lo_valid + hi_invalid)
        if abs(hi_invalid - lo_valid) <= resolution:
            break
        if _catalog_valid_at(catalog, driver, mid, min_wall_mm, slack_mm):
            lo_valid = mid
        else:
            hi_invalid = mid
    return lo_valid, True


def _probe_rebuild(
    body: Any,
    catalog: dict,
    driver: str,
    value: float,
    relations: list[dict] | None,
    base_step_kind: str,
) -> dict:
    """Verify-by-rebuild ONE value through generate_variant_family (single-value
    family). Returns {valid, features_lost, brep_valid, clamped, error}."""
    rec = {
        "valid": False, "features_lost": None, "features_expected": None,
        "brep_valid": None, "clamped": False, "error": None,
    }
    try:
        res = GenerateVariantFamily().apply(body, {
            "catalog": catalog,
            "driver": driver,
            "values": [value],
            "relations": relations,
            "base_step_kind": base_step_kind,
            "write_plans": False,
        })
        variants = res.extras["variant_family"]["variants"]
        if not variants:
            rec["error"] = "no variant produced"
            return rec
        v = variants[0]
        rec["valid"] = bool(v.get("valid"))
        rec["features_lost"] = v.get("features_lost")
        rec["features_expected"] = v.get("features_expected")
        rec["brep_valid"] = v.get("brep_valid")
        rec["error"] = v.get("error")
    except Exception as exc:  # noqa: BLE001 — surface per-probe
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return rec


def solve_driver_range(
    catalog: dict,
    driver: str,
    min_wall_mm: float = 0.8,
    span_factor: float = _DEFAULT_SPAN_FACTOR,
) -> dict:
    """Pure-ANALYTIC range estimate (no OCCT rebuild). Returns the driver_range
    dict WITHOUT the verify-by-rebuild probes filled in (the skill wrapper adds
    those). ``feasible_range`` is None with a ``reason`` for the honest-fallback
    cases (freeform / coupled / non-envelope-governed driver).
    """
    out: dict[str, Any] = {
        "driver": driver,
        "driver_role": None,
        "seed_value": None,
        "feasible_range": None,
        "method": "none",
        "reason": None,
        "min_wall_mm": min_wall_mm,
        "bounds_constrained": {"min": False, "max": False},
        "probes": [],
    }
    if not isinstance(catalog, dict) or not catalog:
        out["reason"] = "empty / non-dict catalog"
        return out

    key_dims = identify_key_dimensions(catalog)
    entry = next((e for e in key_dims if e["name"] == driver), None)
    if entry is None:
        # Not a named key dimension — a raw / freeform handle we cannot bound
        # analytically. Honest fallback.
        out["reason"] = (
            f"driver '{driver}' is not a key_dimensions name — its feasible "
            "range is not analytically bounded (freeform / raw handle)"
        )
        return out

    role = entry.get("role")
    out["driver_role"] = role
    seed = _as_float(entry.get("value_mm"))
    out["seed_value"] = round(seed, 6) if seed is not None else None

    # Loft / sweep / revolve body ⇒ the box pre-check cannot bound it.
    freeform = _has_freeform(catalog)
    if freeform is not None:
        out["reason"] = (
            f"catalog carries {freeform} (loft/sweep/revolve base) — the "
            "box-containment pre-check does not bound freeform geometry; "
            "feasible range left None (honest)"
        )
        return out

    if role not in _ENVELOPE_GOVERNED_ROLES:
        out["reason"] = (
            f"driver role '{role}' is coupled (pattern / footprint), not "
            "governed by the box-containment envelope — analytic bound "
            "unreliable, feasible range left None (honest)"
        )
        return out

    if seed is None or seed <= 0.0:
        out["reason"] = "seed value missing / non-positive"
        return out

    # A REAL extracted part carries sub-mm envelope-vs-bbox extraction noise
    # that trips the (micron-tight) containment pre-check at the seed itself.
    # Measure the smallest bbox slack (<= _MAX_SEED_SLACK_MM) that makes the
    # SEED pass, and judge every candidate with that SAME slack — so the range
    # is anchored to the seed's actual (noisy) geometry, never granted free
    # room. None ⇒ the seed is GENUINELY infeasible (honest fallback).
    slack = _seed_containment_slack(catalog, driver, seed, min_wall_mm)
    if slack is None:
        cap = _max_seed_slack(catalog)
        out["reason"] = (
            "seed value already violates the containment / min-wall pre-check "
            f"by more than {cap:.4f} mm (min_wall_mm={min_wall_mm}) — a genuine "
            "infeasibility, not extraction noise; cannot anchor a feasible "
            "range (honest)"
        )
        return out
    out["seed_slack_mm"] = round(slack, 6)

    lo, lo_constrained = _bisect_bound(
        catalog, driver, seed, -1, min_wall_mm, span_factor, slack)
    hi, hi_constrained = _bisect_bound(
        catalog, driver, seed, +1, min_wall_mm, span_factor, slack)
    if not (lo < seed <= hi or lo <= seed < hi):
        # Degenerate (no width) — report None honestly.
        out["reason"] = "analytic bisection produced a degenerate range"
        return out

    out["feasible_range"] = [round(lo, 6), round(hi, 6)]
    # Which bound is a REAL geometric constraint (vs the search-span edge).
    # The skill wrapper only REQUIRES the outside-probe to degrade at a
    # constrained bound; a span-edge bound's outside probe is informational.
    out["bounds_constrained"] = {
        "min": lo_constrained, "max": hi_constrained,
    }
    out["method"] = "analytic"
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="solve_driver_range",
    category="reverse_engineer",
    level="macro",
    summary="Estimate the FEASIBLE RANGE [min,max] of a single named driver "
            "over which the variant stays valid (features_lost==0, min-wall "
            "respected, no containment violation) WITHOUT brute-force "
            "rebuilding every value. Analytic envelope from the catalog-space "
            "containment/min-wall pre-check (reused from plan_from_scaled_"
            "catalog) bisects the valid<->invalid bound on the apply_variant_"
            "drivers catalog; a few generate_variant_family rebuild probes at "
            "the bounds (valid inside, invalid/clamped outside) confirm it "
            "(method 'probed') or it stays 'analytic'. HONEST FALLBACK: for "
            "coupled / freeform / loft-base drivers (loft/sweep/revolve "
            "features, pitch/pocket roles, or a raw non-key driver) returns "
            "feasible_range=None + a reason — never a faked range. "
            "extras['driver_range'] carries driver/role/seed/feasible_range/"
            "method/reason/probes. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["driver_range"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.6,
    result_grade="estimate",
    post_conditions=[PostCondition(kind="body_present")],
)
class SolveDriverRange(SkillBase):
    class Args(BaseModel):
        # extra='forbid' — unknown keys are typos, not silently-dropped extras.
        model_config = ConfigDict(extra="forbid")

        catalog: dict | None = Field(
            default=None,
            description="The feature_catalog dict to analyse. When None, the "
                        "last catalog shared by PlanFromFeatureCatalog on this "
                        "process is used.",
        )
        driver: str = Field(
            ...,
            description="The single key_dimensions name to bound (e.g. "
                        "'housing_length', 'primary_bore_diameter', "
                        "'wall_thickness'). A non-key / freeform driver returns "
                        "feasible_range=None (honest fallback).",
        )
        min_wall_mm: float = Field(
            default=0.8, ge=0.0,
            description="Lateral wall floor (mm) the containment pre-check "
                        "enforces — the same constraint plan_from_scaled_"
                        "catalog uses. A tighter floor narrows the range.",
        )
        span_factor: float = Field(
            default=_DEFAULT_SPAN_FACTOR, gt=1.0,
            description="Search span as a multiple of the seed value (the far "
                        "probe for bisection). Larger ⇒ wider search.",
        )
        probe: bool = Field(
            default=True,
            description="Verify the analytic bounds by actual rebuild "
                        "(generate_variant_family) at four probe values — just "
                        "inside/outside each bound. False ⇒ analytic only "
                        "(method stays 'analytic', no OCCT).",
        )
        probe_margin_frac: float = Field(
            default=0.05, gt=0.0, lt=0.5,
            description="How far inside / outside each bound to place the "
                        "rebuild probes, as a fraction of the bound value.",
        )
        base_step_kind: str = Field(
            default="box",
            description="Base step kind for the rebuild probes "
                        "('box' | 'import_step' | 'preserve_brep').",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        catalog = args.catalog
        if catalog is None:
            catalog = getattr(PlanFromFeatureCatalog, "_LAST_CATALOG", None)
        catalog = catalog or {}

        result = solve_driver_range(
            catalog,
            args.driver,
            min_wall_mm=float(args.min_wall_mm),
            span_factor=float(args.span_factor),
        )

        # Verify-by-rebuild probes — only when an analytic range exists and the
        # caller asked for them. Honest-fallback (None) cases skip probing.
        fr = result.get("feasible_range")
        if args.probe and isinstance(fr, list) and len(fr) == 2:
            lo, hi = float(fr[0]), float(fr[1])
            m = float(args.probe_margin_frac)
            from phone_designer.skills.reverse_engineer.recover_design_relations import (  # noqa: E501
                recover_design_relations,
            )
            relations = recover_design_relations(catalog)

            # (value, position): just inside / just outside each bound.
            plan = [
                (lo * (1.0 + m), "inside_min"),
                (lo * (1.0 - m), "outside_min"),
                (hi * (1.0 - m), "inside_max"),
                (hi * (1.0 + m), "outside_max"),
            ]
            # Probe the two INSIDE bounds first to learn the expected feature
            # count of a healthy rebuild — the OUTSIDE probes ratify when they
            # DEGRADE relative to that (a feature dropped / lost / brep broke).
            recs: dict[str, dict] = {}
            for value, position in plan:
                recs[position] = _probe_rebuild(
                    body, catalog, args.driver, value, relations,
                    args.base_step_kind,
                )
            healthy_feat = max(
                (recs[p].get("features_expected") or 0)
                for p in ("inside_min", "inside_max")
            )

            def _degraded(rec: dict) -> bool:
                # An "outside" rebuild confirms the bound when it is invalid,
                # OR a feature was lost, OR the planner dropped a feature
                # (features_expected fell below the healthy inside count — the
                # box-mode signal that the envelope no longer contains it), OR
                # it errored / was clamped back inside.
                if rec.get("error"):
                    return True
                if rec.get("clamped"):
                    return True
                if not rec.get("valid"):
                    return True
                fl = rec.get("features_lost")
                if isinstance(fl, int) and fl > 0:
                    return True
                fe = rec.get("features_expected")
                if (isinstance(fe, int) and healthy_feat > 0
                        and fe < healthy_feat):
                    return True
                return False

            constrained = result.get("bounds_constrained") or {}
            probes: list[dict] = []
            all_agree = True
            for value, position in plan:
                rec = recs[position]
                inside = position.startswith("inside")
                bound_side = "min" if position.endswith("_min") else "max"
                is_constrained = bool(constrained.get(bound_side))
                if inside:
                    confirmed = bool(rec["valid"])
                elif is_constrained:
                    # a REAL geometric bound — the rebuild must degrade past it.
                    confirmed = _degraded(rec)
                else:
                    # span-edge bound (no real constraint) — the outside probe
                    # is informational; not required to degrade. Confirmed by
                    # definition so an unbounded grow-direction never falsely
                    # demotes the range to analytic-only.
                    confirmed = True
                rec_out = {
                    "value": round(value, 6),
                    "position": position,
                    "expected": (
                        "valid" if inside
                        else ("degraded" if is_constrained else "span_edge")
                    ),
                    "valid": bool(rec["valid"]),
                    "confirmed": confirmed,
                    "features_expected": rec["features_expected"],
                    "features_lost": rec["features_lost"],
                    "brep_valid": rec["brep_valid"],
                    "clamped": rec["clamped"],
                    "error": rec["error"],
                }
                probes.append(rec_out)
                if not confirmed:
                    all_agree = False
            result["probes"] = probes
            # Promote to 'probed' only when every probe ratified the analytic
            # range; otherwise stay 'analytic' and flag the disagreement
            # (honest — we never claim a probed confirmation we didn't get).
            if all_agree:
                result["method"] = "probed"
            else:
                result["method"] = "analytic"
                note = (
                    "rebuild probes did not all ratify the analytic bound — "
                    "range reported analytic-only"
                )
                result["reason"] = (
                    note if not result.get("reason")
                    else f"{result['reason']}; {note}"
                )

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"driver_range": result},
        )
