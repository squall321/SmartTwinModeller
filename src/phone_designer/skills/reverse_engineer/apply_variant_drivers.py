"""apply_variant_drivers — atomic, read-only (Pillar VARIANTS, 2026-06-14).

A clean *named-driver* API over the parametric-variation pipeline. A caller
hands a dict of human dimension names → target values (the names
``identify_key_dimensions`` emits, e.g. ``{"housing_length": 50.0,
"primary_bore_diameter": 8.5}``) and gets back a coherently varied +
normalized catalog AND the rebuild plan — without ever touching a raw dotted
key.

Why this layer exists: ``vary_feature_catalog`` already changes any field by
dotted key, but (a) callers must know the dotted key and (b) a single edit
does NOT keep coupled fields coherent — change the primary bore and the
counterbore seat stays the old size; move a pattern pitch and the member
positions go stale. This skill resolves names → keys (via
``identify_key_dimensions``) and PROPAGATES each edit through a
``design_relations`` graph (from ``recover_design_relations``) so the variant
rebuilds as a designer would expect.

Flow::

    1. identify_key_dimensions(catalog)  → name → dotted_key + current value
    2. for each driver: target value → absolute_override on its dotted_key,
       and a scale_ratio = target / current_value
    3. PROPAGATE through design_relations (when propagate=True):
         counterbore ratio  — driving the through bore scales the cb seat by
                               the relation's ratio (cb_target = ratio*through)
         mirror reflect      — driving one partner sets the other EQUAL
         pattern pitch       — driving the pitch is enough; the member
                               positions are re-derived by normalize_varied_
                               catalog (the relation only records the handle)
         concentric equal    — driving one member's axis_origin shifts the
                               whole co-axial group by the same delta
    4. vary_catalog_ex(catalog, absolute_overrides=..., per_feature_scale=...)
    5. normalize_varied_catalog (standard re-match, pattern coherence, …)
    6. plan_from_scaled_catalog wiring → generated_plan

``propagate=False`` applies the raw driver edits ONLY (no relation
propagation) — useful to A/B the coherent vs raw rebuild.

extras schema::

    {"variant_result": {
        "drivers_resolved":   [{name, dotted_key, role, from_value,
                                to_value, scale_ratio}, ...],
        "drivers_unresolved": [name, ...],   # name not in the key-dim menu
        "propagated":         [{relation_kind, source_key, target_key,
                                rule, to_value}, ...],
        "absolute_overrides": {dotted_key: value, ...},  # what was applied
        "varied_catalog":     {...},
        "normalized_catalog": {...},
        "generated_plan":     {...},
        "variation_warnings": [...],
        "unresolved_overrides": [...],
     }}

Body unchanged (post ``body_present``). The input catalog is never mutated.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
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
    _navigate_to_parent,
    _split_dotted_key,
    vary_catalog_ex,
)


def _as_float(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _value_at(catalog: dict, dotted_key: str) -> Any:
    nav = _navigate_to_parent(catalog, _split_dotted_key(dotted_key))
    if nav is None:
        return None
    parent, last = nav
    try:
        return parent[last]
    except Exception:
        return None


def _bbox_component_index(dotted_key: str) -> int | None:
    """For an envelope dotted_key ``initial_bbox_mm.<i>`` return the integer
    component index (3/4/5 = xmax/ymax/zmax), else None."""
    parts = _split_dotted_key(dotted_key)
    if len(parts) == 2 and parts[0] == "initial_bbox_mm":
        try:
            return int(parts[1])
        except ValueError:
            return None
    return None


def _envelope_min_side(catalog: dict, comp_idx: int | None) -> float | None:
    """The MIN-side bbox coordinate paired with a MAX-side component
    (index 3↔0, 4↔1, 5↔2). identify_key_dimensions targets the max-side
    component, so the extent maps to coordinate = min_side + extent."""
    if comp_idx is None or comp_idx < 3:
        return None
    bb = catalog.get("initial_bbox_mm")
    if not isinstance(bb, (list, tuple)) or len(bb) < 6:
        return None
    try:
        return float(bb[comp_idx - 3])
    except (TypeError, ValueError, IndexError):
        return None


def apply_variant_drivers(
    catalog: dict,
    drivers: dict[str, float],
    relations: list[dict] | None = None,
    propagate: bool = True,
) -> dict:
    """Pure function: resolve named drivers, build the override set (with
    relation propagation when ``propagate``), and return a result dict.

    Returns a dict with keys ``absolute_overrides`` (the dotted-key → value
    map to feed vary_catalog_ex), ``drivers_resolved``, ``drivers_unresolved``
    and ``propagated`` (the planning / normalization is done by the skill
    wrapper / caller — keeping this function side-effect-free and OCCT-free).
    """
    if not isinstance(catalog, dict):
        return {
            "absolute_overrides": {},
            "drivers_resolved": [],
            "drivers_unresolved": list(drivers or {}),
            "propagated": [],
        }

    # name → key-dimension entry (dotted_key + current value + role).
    key_dims = identify_key_dimensions(catalog)
    by_name: dict[str, dict] = {e["name"]: e for e in key_dims}

    if relations is None:
        relations = recover_design_relations(catalog)

    absolute_overrides: dict[str, Any] = {}
    drivers_resolved: list[dict] = []
    drivers_unresolved: list[str] = []
    # dotted_key → scale_ratio for keys a driver set directly (drives
    # relation propagation that scales coupled fields).
    driven_ratio: dict[str, float] = {}
    # dotted_key → absolute target a driver set directly.
    driven_value: dict[str, float] = {}

    for name, target in (drivers or {}).items():
        entry = by_name.get(name)
        tgt = _as_float(target)
        if entry is None or tgt is None:
            drivers_unresolved.append(name)
            continue
        dotted = entry["dotted_key"]
        role = entry.get("role")

        # The salient "current value" the caller reasons about is the
        # entry's value_mm (the EXTENT for envelope dims, the resolved field
        # value for every other role). For envelope dims the dotted_key
        # targets the MAX-side bbox coordinate, NOT the extent — so a target
        # EXTENT must be converted to the absolute coordinate
        # (min_side + target_extent) before it becomes an absolute_override.
        # Without this an offset-from-origin body shrinks instead of growing
        # (occt__linkrods xmin=3.125: setting xmax to the 1.5x extent 7.53
        # is SMALLER than the original 8.145 — net 0.88x). Reporting stays in
        # extent space so scale_ratio reads truthfully (1.5x, not 0.92x).
        current = _as_float(entry.get("value_mm"))
        if current is None:
            current = _as_float(_value_at(catalog, dotted))

        override_value: Any = tgt
        if role == "envelope":
            comp_idx = _bbox_component_index(dotted)
            min_side = _envelope_min_side(catalog, comp_idx)
            if min_side is not None:
                override_value = min_side + tgt

        absolute_overrides[dotted] = override_value
        driven_value[dotted] = override_value
        ratio = (tgt / current) if (current not in (None, 0.0)) else None
        if ratio is not None:
            driven_ratio[dotted] = ratio
        drivers_resolved.append({
            "name": name,
            "dotted_key": dotted,
            "role": role,
            "from_value": round(current, 6) if current is not None else None,
            "to_value": round(tgt, 6),
            "scale_ratio": round(ratio, 6) if ratio is not None else None,
        })

    propagated: list[dict] = []
    if propagate and absolute_overrides:
        propagated = _propagate(
            catalog, relations, driven_value, driven_ratio,
            absolute_overrides,
        )

    return {
        "absolute_overrides": absolute_overrides,
        "drivers_resolved": drivers_resolved,
        "drivers_unresolved": drivers_unresolved,
        "propagated": propagated,
    }


def _propagate(
    catalog: dict,
    relations: list[dict],
    driven_value: dict[str, float],
    driven_ratio: dict[str, float],
    absolute_overrides: dict[str, Any],
) -> list[dict]:
    """Walk the relations graph and add coupled overrides for every relation
    whose driven member moved. Records each propagation. ``absolute_overrides``
    is extended in place; a key a driver set DIRECTLY is never overwritten by
    propagation (explicit driver beats derived).
    """
    propagated: list[dict] = []

    def _set(target_key: str, value: float, rec: dict) -> None:
        if target_key in driven_value:
            return  # an explicit driver owns this key
        # last propagation wins for a given target — but record each.
        absolute_overrides[target_key] = value
        propagated.append(rec)

    for rel in relations or []:
        if not isinstance(rel, dict):
            continue
        kind = rel.get("kind")
        members = rel.get("members") or []
        rule = rel.get("rule")

        if kind == "counterbore" and len(members) == 2 and rule == "ratio":
            through_key, cb_key = members[0], members[1]
            ratio = _as_float(rel.get("value"))
            if ratio is None:
                continue
            # driving the through bore scales the cb seat by the relation ratio.
            if through_key in driven_value:
                cb_target = driven_value[through_key] * ratio
                _set(cb_key, cb_target, {
                    "relation_kind": "counterbore",
                    "source_key": through_key,
                    "target_key": cb_key,
                    "rule": "ratio",
                    "to_value": round(cb_target, 6),
                })
            # driving the cb seat keeps the through bore at the inverse ratio.
            elif cb_key in driven_value and ratio != 0.0:
                through_target = driven_value[cb_key] / ratio
                _set(through_key, through_target, {
                    "relation_kind": "counterbore",
                    "source_key": cb_key,
                    "target_key": through_key,
                    "rule": "ratio",
                    "to_value": round(through_target, 6),
                })

        elif kind == "mirror" and len(members) == 2 and rule == "reflect":
            a_key, b_key = members[0], members[1]
            # a reflected hole is the SAME size — drive the partner equal.
            if a_key in driven_value and b_key not in driven_value:
                val = driven_value[a_key]
                _set(b_key, val, {
                    "relation_kind": "mirror",
                    "source_key": a_key,
                    "target_key": b_key,
                    "rule": "reflect",
                    "to_value": round(val, 6),
                })
            elif b_key in driven_value and a_key not in driven_value:
                val = driven_value[b_key]
                _set(a_key, val, {
                    "relation_kind": "mirror",
                    "source_key": b_key,
                    "target_key": a_key,
                    "rule": "reflect",
                    "to_value": round(val, 6),
                })

        elif kind == "concentric" and rule == "equal" and len(members) >= 2:
            # axis_origin members: when a driver moves one member's origin,
            # apply the SAME 3-vector delta to the siblings so the group stays
            # co-axial. (identify_key_dimensions does not name axis_origin, so
            # this only fires when a caller passes a position-style driver via
            # a future name; harmless no-op otherwise.)
            driven_member = next((m for m in members if m in driven_value), None)
            if driven_member is None:
                continue
            # positions are vectors — absolute_overrides on a vector key are
            # assigned verbatim; only propagate when the driven value is a
            # 3-vector (scalar drivers can't move a position coherently).
            src_val = absolute_overrides.get(driven_member)
            orig = _value_at(catalog, driven_member)
            if not (isinstance(src_val, (list, tuple)) and len(src_val) >= 3):
                continue
            if not (isinstance(orig, (list, tuple)) and len(orig) >= 3):
                continue
            delta = [float(src_val[i]) - float(orig[i]) for i in range(3)]
            for m in members:
                if m == driven_member or m in driven_value:
                    continue
                base = _value_at(catalog, m)
                if not (isinstance(base, (list, tuple)) and len(base) >= 3):
                    continue
                moved = [float(base[i]) + delta[i] for i in range(3)] + [
                    float(v) for v in base[3:]
                ]
                _set(m, moved, {
                    "relation_kind": "concentric",
                    "source_key": driven_member,
                    "target_key": m,
                    "rule": "equal",
                    "to_value": [round(c, 6) for c in moved[:3]],
                })

        # pattern_pitch needs NO override here — driving the pitch key is the
        # whole edit; normalize_varied_catalog re-derives the positions.

    return propagated


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="apply_variant_drivers",
    category="reverse_engineer",
    level="atomic",
    summary="Named-driver parametric variation: resolve human dimension names "
            "(from identify_key_dimensions, e.g. 'housing_length', "
            "'primary_bore_diameter') to dotted keys, build absolute overrides, "
            "PROPAGATE through a design_relations graph (counterbore ratio, "
            "mirror reflect, pattern pitch, concentric), normalize the varied "
            "catalog and emit the rebuild plan via plan_from_scaled_catalog. "
            "propagate=False applies raw driver edits only. extras carries "
            "'variant_result' (drivers_resolved/unresolved, propagated, "
            "varied/normalized catalog, generated_plan). Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["variant_result"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="body_present")],
)
class ApplyVariantDrivers(SkillBase):
    class Args(BaseModel):
        # Unknown arg keys raise instead of being silently dropped (the P8
        # no-silent-swallowing principle the variation pipeline pins).
        model_config = ConfigDict(extra="forbid")

        catalog: dict | None = Field(
            default=None,
            description="The feature_catalog dict to vary. When None, the last "
                        "catalog shared by PlanFromFeatureCatalog on this "
                        "process is used.",
        )
        drivers: dict = Field(
            default_factory=dict,
            description="Named-dimension → target value, where the names are "
                        "those identify_key_dimensions emits (e.g. "
                        "{'housing_length': 50.0, 'primary_bore_diameter': "
                        "8.5}). A name absent from the key-dimension menu is "
                        "reported in drivers_unresolved.",
        )
        relations: list | None = Field(
            default=None,
            description="design_relations from recover_design_relations. When "
                        "None they are recovered from the catalog.",
        )
        propagate: bool = Field(
            default=True,
            description="Propagate each driver through the relations graph "
                        "(counterbore ratio, mirror reflect, concentric). "
                        "False ⇒ raw driver overrides only.",
        )
        base_step_kind: str = Field(
            default="box",
            description="Base step kind handed to plan_from_feature_catalog "
                        "('box' | 'import_step' | 'preserve_brep').",
        )
        normalize: bool = Field(
            default=True,
            description="Run normalize_varied_catalog before planning (standard "
                        "re-match, pattern position re-derivation).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        catalog = args.catalog
        if catalog is None:
            catalog = getattr(PlanFromFeatureCatalog, "_LAST_CATALOG", None)
        catalog = catalog or {}

        resolution = apply_variant_drivers(
            catalog,
            dict(args.drivers or {}),
            relations=args.relations,
            propagate=args.propagate,
        )
        absolute_overrides = resolution["absolute_overrides"]

        # Build the varied catalog from the resolved overrides.
        varied, vary_warnings = vary_catalog_ex(
            catalog, absolute_overrides=absolute_overrides,
        )
        unresolved_overrides = vary_warnings.get("unresolved_overrides") or []

        # Normalize derived fields (standard re-match, pattern coherence — this
        # is where a driven pattern pitch re-derives its member positions).
        if args.normalize:
            planner_catalog, variation_warnings = normalize_catalog(
                varied, scale_hint=None,
            )
        else:
            planner_catalog, variation_warnings = varied, []

        plan_res = PlanFromFeatureCatalog().apply(body, {
            "catalog": planner_catalog,
            "base_step_kind": args.base_step_kind,
        })
        generated_plan = plan_res.extras.get("generated_plan")

        variant_result = {
            "drivers_resolved": resolution["drivers_resolved"],
            "drivers_unresolved": resolution["drivers_unresolved"],
            "propagated": resolution["propagated"],
            "absolute_overrides": absolute_overrides,
            "varied_catalog": varied,
            "normalized_catalog": planner_catalog,
            "generated_plan": generated_plan,
            "variation_warnings": variation_warnings,
            "unresolved_overrides": unresolved_overrides,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"variant_result": variant_result},
        )
