"""snap_to_standards — atomic, read-only (Pillar VARIANTS, phase-4 2026-06-15).

After a parametric variant edit, SNAP driven dimensions to the nearest catalog
standard *where appropriate* — the conservative finishing pass a designer runs
when a free-typed driver value lands a hair off a real fastener / stock size.
A bore driven to 8.4 mm snaps to the M8 close-clearance bore (8.4 mm exactly);
a bore typed to 8.55 mm snaps to the nearest catalog drill / clearance; a wall
typed to 1.47 mm snaps to a 1.5 mm stock sheet; a hole-pattern pitch typed to
1.24 mm snaps to the M8 thread pitch (1.25 mm).

Why a SEPARATE pass (not folded into apply_variant_drivers): snapping is an
opt-in, *conservative* policy — only when the caller asks (``snap=True``) and
ONLY when the standard sits within ``tolerance`` of the driven value. A driver
deliberately set to a non-standard value (a custom bore, an odd wall) must be
LEFT ALONE — we never *force* a value onto a standard. The result reports
exactly which dims snapped and their original→snapped delta so nothing is
silently rewritten (the P8 no-silent-swallowing principle).

Reuse, not reinvention:
  * BORE / fastener-clearance dims reuse ``classify_holes._best_standard_match``
    (the same metric/imperial thread matcher the catalog path uses) to pick the
    thread family + fit, then look the EXACT catalog diameter up in
    ``catalogs/standards/threads_metric.yaml`` for that fit. The snap target is
    a real catalogued number, never a recomputed estimate.
  * PITCH dims snap to the matched thread's ``pitch_mm`` (the screw's real lead)
    — only when a bore in the SAME catalog already matched a thread family,
    otherwise to the nearest pitch in the thread table.
  * WALL / stock dims snap to the nearest entry of a preferred metric sheet /
    plate stock ladder (ISO-style 0.5/0.6/.../1.0/1.2/1.5/2.0/2.5/3.0 … mm).

Each candidate dim is classified by the key_dimensions ROLE that owns its
dotted_key (primary_bore → bore snap, pitch → pitch snap, wall → stock snap).
Envelope and pocket dims are NOT snapped (no meaningful single standard ladder).

Args::
    snap:         bool  — master gate. False ⇒ pure no-op report (nothing snaps).
    tolerance:    float — max |driven − standard| (mm) to accept a snap. A
                          standard further than this is rejected (conservative).
    roles:        list[str] | None — which roles to consider; default all of
                          ('primary_bore', 'pitch', 'wall'). An explicit subset
                          lets a caller snap ONLY bores, etc.

Operates over the variant result of ``apply_variant_drivers`` (the catalog +
``drivers_resolved``). The DRIVEN dims are the snap candidates — we never touch
a dimension the caller did not drive.

extras schema::

    {"snap_result": {
        "snapped":     [{name, dotted_key, role, from_value, to_value,
                         standard, delta_mm, source}],   # what changed
        "unchanged":   [{name, dotted_key, role, value, reason}],  # why not
        "absolute_overrides": {dotted_key: snapped_value, ...},    # to re-apply
        "tolerance_mm": float,
        "snap": bool,
     }}

The ``absolute_overrides`` are the snapped values a caller feeds straight back
through ``vary_catalog_ex`` / ``apply_variant_drivers`` to rebuild the snapped
variant. The input catalog is never mutated. Body unchanged (post body_present).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.inspect.classify_holes import (
    _best_standard_match,
    _load,
)
from phone_designer.skills.reverse_engineer.apply_variant_drivers import (
    apply_variant_drivers,
)
from phone_designer.skills.reverse_engineer.identify_key_dimensions import (
    identify_key_dimensions,
)
from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
    PlanFromFeatureCatalog,
)


# Roles eligible for snapping. Envelope / pocket are intentionally excluded —
# there is no single standard ladder a length / pocket footprint should snap to
# (forcing one would be a fake win). Default order is bore, pitch, wall.
_SNAPPABLE_ROLES = ("primary_bore", "pitch", "wall")

# The catalog field each _best_standard_match fit-label resolves to in
# threads_metric.yaml. _best_standard_match scored the driven diameter against
# exactly these fields, so the matched fit's field IS the snap target.
_FIT_TO_FIELD: dict[str, str] = {
    "close": "clearance_close_mm",
    "medium": "clearance_medium_mm",
    "coarse": "clearance_coarse_mm",
    "tap": "tap_drill_mm",
    "outer": "outer_d_mm",
    # 'imperial' fits resolve through the imperial table below, not here.
}

# Preferred metric sheet / plate stock thickness ladder (mm). A pragmatic
# subset of the common ISO / EN preferred series — enough to snap a wall typed
# a hair off a real stock gauge without claiming an exhaustive standard.
_STOCK_THICKNESS_MM = (
    0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0,
    8.0, 10.0, 12.0,
)


def _as_float(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _bore_standard_diameter(
    driven_d: float,
) -> tuple[float, str] | None:
    """Nearest catalogued bore diameter for ``driven_d``.

    Reuses ``_best_standard_match`` to choose the thread family + fit, then
    looks the EXACT catalog diameter up for that fit in threads_metric.yaml.
    Returns ``(standard_diameter_mm, label)`` or None when no usable catalog
    field exists. ``label`` reads e.g. ``"M8 close (clearance_close_mm)"``.
    """
    match = _best_standard_match(driven_d, None, None)
    if not isinstance(match, dict):
        return None
    spec = match.get("thread_spec")
    fit = match.get("fit")
    field = _FIT_TO_FIELD.get(fit)
    if spec is None:
        return None

    data = _load("standards", "threads_metric")
    if isinstance(data, dict):
        entry = (data.get("threads") or {}).get(spec)
        if isinstance(entry, dict) and field is not None:
            d = _as_float(entry.get(field))
            if d is not None:
                return d, f"{spec} {fit} ({field})"
            # fit field missing — fall back to the nearest field on this spec.
            near = _nearest_entry_field(entry, driven_d)
            if near is not None:
                d2, fld = near
                return d2, f"{spec} {fld}"

    # Imperial / unmapped fit: re-derive the closest catalogued diameter by
    # scanning every diameter-like field across both tables.
    fallback = _nearest_catalog_diameter(driven_d)
    if fallback is not None:
        d3, lbl = fallback
        return d3, lbl
    return None


_DIAMETER_FIELDS = (
    "clearance_close_mm", "clearance_medium_mm", "clearance_coarse_mm",
    "tap_drill_mm", "outer_d_mm",
)


def _nearest_entry_field(entry: dict, target: float) -> tuple[float, str] | None:
    best: tuple[float, str] | None = None
    best_dist = float("inf")
    for fld in _DIAMETER_FIELDS:
        d = _as_float(entry.get(fld))
        if d is None:
            continue
        dist = abs(d - target)
        if dist < best_dist:
            best_dist = dist
            best = (d, fld)
    return best


def _nearest_catalog_diameter(target: float) -> tuple[float, str] | None:
    best: tuple[float, str] | None = None
    best_dist = float("inf")
    for table in ("threads_metric", "threads_imperial"):
        data = _load("standards", table)
        if not isinstance(data, dict):
            continue
        for spec, entry in (data.get("threads") or {}).items():
            if not isinstance(entry, dict):
                continue
            near = _nearest_entry_field(entry, target)
            if near is None:
                # imperial entries may only carry outer_d_mm
                d = _as_float(entry.get("outer_d_mm"))
                if d is None:
                    continue
                near = (d, "outer_d_mm")
            d, fld = near
            dist = abs(d - target)
            if dist < best_dist:
                best_dist = dist
                best = (d, f"{spec} {fld}")
    return best


def _pitch_standard(
    driven_pitch: float, bore_thread_spec: str | None,
) -> tuple[float, str] | None:
    """Nearest standard thread pitch for ``driven_pitch``.

    When a bore in the same catalog already matched a thread family
    (``bore_thread_spec``), prefer THAT spec's pitch (the fastener's real lead).
    Otherwise snap to the nearest ``pitch_mm`` across the metric thread table.
    """
    data = _load("standards", "threads_metric")
    if not isinstance(data, dict):
        return None
    threads = data.get("threads") or {}

    if bore_thread_spec is not None:
        entry = threads.get(bore_thread_spec)
        if isinstance(entry, dict):
            p = _as_float(entry.get("pitch_mm"))
            if p is not None:
                return p, f"{bore_thread_spec} pitch_mm"

    best: tuple[float, str] | None = None
    best_dist = float("inf")
    for spec, entry in threads.items():
        if not isinstance(entry, dict):
            continue
        p = _as_float(entry.get("pitch_mm"))
        if p is None:
            continue
        dist = abs(p - driven_pitch)
        if dist < best_dist:
            best_dist = dist
            best = (p, f"{spec} pitch_mm")
    return best


def _stock_standard(driven_wall: float) -> tuple[float, str] | None:
    """Nearest preferred stock thickness for ``driven_wall``."""
    best: tuple[float, str] | None = None
    best_dist = float("inf")
    for t in _STOCK_THICKNESS_MM:
        dist = abs(t - driven_wall)
        if dist < best_dist:
            best_dist = dist
            best = (t, f"{t:g} mm stock")
    return best


def snap_to_standards(
    catalog: dict,
    drivers_resolved: list[dict],
    snap: bool = True,
    tolerance: float = 0.25,
    roles: list[str] | None = None,
) -> dict:
    """Pure function: snap the DRIVEN dims of a variant to nearest catalog
    standards, conservatively (only within ``tolerance``).

    ``drivers_resolved`` is the apply_variant_drivers ``drivers_resolved`` list
    (each carries name / dotted_key / role / to_value). Returns the snap_result
    dict (see module docstring). Side-effect-free: the input catalog is never
    mutated and ``absolute_overrides`` is the snapped-value map a caller feeds
    back to rebuild.
    """
    active_roles = set(roles) if roles else set(_SNAPPABLE_ROLES)

    # A bore thread family in the catalog seeds pitch snapping (the screw's
    # real lead). Found from the primary_bore key dimension's standard_match.
    bore_spec: str | None = None
    if isinstance(catalog, dict):
        for kd in identify_key_dimensions(catalog):
            if kd.get("role") == "primary_bore":
                sm = kd.get("standard_match")
                if isinstance(sm, dict):
                    bore_spec = sm.get("thread_spec")
                break

    snapped: list[dict] = []
    unchanged: list[dict] = []
    absolute_overrides: dict[str, Any] = {}

    for d in drivers_resolved or []:
        name = d.get("name")
        dotted = d.get("dotted_key")
        role = d.get("role")
        driven = _as_float(d.get("to_value"))
        base = {"name": name, "dotted_key": dotted, "role": role}

        if driven is None:
            unchanged.append({**base, "value": d.get("to_value"),
                              "reason": "non-numeric driven value"})
            continue
        if not snap:
            unchanged.append({**base, "value": driven,
                              "reason": "snap disabled"})
            continue
        if role not in active_roles:
            unchanged.append({**base, "value": driven,
                              "reason": f"role '{role}' not snappable"})
            continue

        if role == "primary_bore":
            std = _bore_standard_diameter(driven)
            source = "threads_metric"
        elif role == "pitch":
            std = _pitch_standard(driven, bore_spec)
            source = "threads_metric:pitch"
        elif role == "wall":
            std = _stock_standard(driven)
            source = "stock_thickness"
        else:  # pragma: no cover — guarded by active_roles
            std = None
            source = "none"

        if std is None:
            unchanged.append({**base, "value": driven,
                              "reason": "no catalog standard found"})
            continue

        std_value, std_label = std
        delta = std_value - driven
        if abs(delta) > tolerance:
            unchanged.append({
                **base, "value": driven,
                "reason": (
                    f"nearest standard {std_label}={std_value:g} is "
                    f"{abs(delta):.4f} mm away (> tolerance {tolerance:g})"
                ),
            })
            continue
        if abs(delta) == 0.0:
            # Already exactly standard — record as snapped (zero delta) so the
            # caller still sees it landed on a standard, and re-emit the value
            # as an override for a clean rebuild.
            pass

        absolute_overrides[dotted] = std_value
        snapped.append({
            **base,
            "from_value": round(driven, 6),
            "to_value": round(std_value, 6),
            "standard": std_label,
            "delta_mm": round(delta, 6),
            "source": source,
        })

    return {
        "snapped": snapped,
        "unchanged": unchanged,
        "absolute_overrides": absolute_overrides,
        "tolerance_mm": tolerance,
        "snap": snap,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="snap_to_standards",
    category="reverse_engineer",
    level="atomic",
    summary="After a parametric variant edit, SNAP driven dimensions to the "
            "nearest catalog standard where appropriate — a bore driven to "
            "8.4 mm snaps to the M8 close clearance (reusing classify_holes."
            "_best_standard_match + threads_metric.yaml), a hole-pattern pitch "
            "to the matched thread's pitch_mm, a wall to the nearest preferred "
            "stock thickness. CONSERVATIVE: only snaps within 'tolerance', "
            "never forces a custom value onto a standard, never touches a "
            "dimension the caller did not drive. Envelope / pocket dims are not "
            "snapped. extras['snap_result'] reports each snapped dim's "
            "original->snapped delta + the standard, the unchanged dims with a "
            "reason, and the absolute_overrides to rebuild the snapped variant. "
            "Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["snap_result"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class SnapToStandards(SkillBase):
    class Args(BaseModel):
        # extra='forbid' — unknown keys are typos, not silently-dropped extras
        # (the P8 no-silent-swallowing principle the variation pipeline pins).
        model_config = ConfigDict(extra="forbid")

        catalog: dict | None = Field(
            default=None,
            description="The feature_catalog dict the variant was built from. "
                        "When None, the last catalog shared by "
                        "PlanFromFeatureCatalog on this process is used.",
        )
        drivers: dict = Field(
            default_factory=dict,
            description="Named-dimension → target value, the same shape "
                        "apply_variant_drivers takes (e.g. "
                        "{'primary_bore_diameter': 8.4}). These are resolved to "
                        "drivers_resolved internally, then the driven dims are "
                        "the snap candidates. Supply this OR drivers_resolved.",
        )
        drivers_resolved: list | None = Field(
            default=None,
            description="Pre-resolved drivers_resolved list from "
                        "apply_variant_drivers (each {name, dotted_key, role, "
                        "to_value}). When supplied, 'drivers' is ignored — use "
                        "this to snap an already-applied variant.",
        )
        snap: bool = Field(
            default=True,
            description="Master gate. False ⇒ pure no-op report (no dim snaps); "
                        "every driven dim lands in 'unchanged' with reason "
                        "'snap disabled'.",
        )
        tolerance: float = Field(
            default=0.25, ge=0.0,
            description="Max |driven - standard| (mm) to accept a snap. A "
                        "standard further than this is rejected (conservative — "
                        "a deliberately non-standard value is left alone).",
        )
        roles: list | None = Field(
            default=None,
            description="Which key_dimensions roles to snap; default all of "
                        "('primary_bore', 'pitch', 'wall'). Envelope / pocket "
                        "are never snapped.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        catalog = args.catalog
        if catalog is None:
            catalog = getattr(PlanFromFeatureCatalog, "_LAST_CATALOG", None)
        catalog = catalog or {}

        drivers_resolved = args.drivers_resolved
        if drivers_resolved is None:
            # Resolve the named drivers through the SAME pipeline
            # apply_variant_drivers uses, so role / dotted_key / to_value match.
            resolution = apply_variant_drivers(
                catalog, dict(args.drivers or {}), propagate=False,
            )
            drivers_resolved = resolution["drivers_resolved"]

        roles = [str(r) for r in args.roles] if args.roles is not None else None
        snap_result = snap_to_standards(
            catalog,
            list(drivers_resolved or []),
            snap=args.snap,
            tolerance=float(args.tolerance),
            roles=roles,
        )
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"snap_result": snap_result},
        )
