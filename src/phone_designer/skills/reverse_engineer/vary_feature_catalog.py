"""vary_feature_catalog — atomic, read-only.

Produce a *parametric variation* of a ``feature_catalog`` (the dict shape
returned by :class:`ExtractFeatureCatalog`) by uniformly scaling its
dimensional fields, then applying per-feature scale multipliers, then
applying absolute overrides. The input catalog is not mutated; a deep copy
is created and returned in ``extras["varied_catalog"]``.

Application order is fixed::

    1. ``scale_factor`` (uniform multiplier on every dimensional field)
    2. ``per_feature_scale`` (multiplier on a single dotted-key field)
    3. ``absolute_overrides`` (final wins — exact assignment)

Dimensional fields recognised (matched by name on any nested dict, list,
or tuple)::

    top_d_mm, bottom_d_mm, depth_mm, diameter_mm,
    length_mm, width_mm, height_mm, thickness_mm,
    radius_mm, pitch_radius_mm, separation_mm,
    profile_diameter_mm, lower_diameter_mm, upper_diameter_mm,
    spacing_mm, font_size_mm, base_thickness_mm,
    entry_depth_mm, mean_distance_mm,
    axis_origin, entry_origin, plane_origin, centroid
        (3-vectors, each component scaled),
    center, center_xy, position (1-D coordinate lists),
    diameters_mm (per-element scaled),

Direction vectors (``axis_dir``, ``plane_normal``, ``axis_direction``) are
NEVER scaled — they are unit directions, not dimensions.

Dotted keys for ``per_feature_scale`` / ``absolute_overrides`` traverse
list indices and dict keys, e.g. ``"pockets.0.depth_mm"`` or
``"holes.2.diameters_mm.0"``.

Body is unchanged (post body_present).
"""
from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# Scalar dimension field names. A uniform ``scale_factor`` multiplies any
# numeric value found under one of these keys (anywhere in the catalog).
_SCALAR_DIM_FIELDS: frozenset[str] = frozenset({
    "top_d_mm",
    "bottom_d_mm",
    "depth_mm",
    "diameter_mm",
    "length_mm",
    "width_mm",
    "height_mm",
    "thickness_mm",
    "radius_mm",
    "pitch_radius_mm",
    "separation_mm",
    "profile_diameter_mm",
    "lower_diameter_mm",
    "upper_diameter_mm",
    "spacing_mm",
    "font_size_mm",
    "base_thickness_mm",
    "diameter_or_size_mm",
    "anchor_z",
    # P1 whitelist repair (2026-06-10): entry_depth_mm is the pass-23
    # classify_holes sibling field that plan_from_feature_catalog PREFERS
    # for box-mode hole cuts — leaving it unscaled made every scaled
    # variant emit holes at the original depth/position (zero-delta SKIP
    # → bare slab). mean_distance_mm (detect_mirror_symmetry residual)
    # and _ref_radius_mm (loft cone reference radius) are the remaining
    # *_mm scalars the detectors actually emit into the catalog.
    "entry_depth_mm",
    "mean_distance_mm",
    "_ref_radius_mm",
})

# Vector / list fields where every numeric component is scaled.
_VECTOR_DIM_FIELDS: frozenset[str] = frozenset({
    "axis_origin",
    "center",
    "center_xy",
    "position",
    "position_xy",
    "diameters_mm",
    "bbox",
    "initial_bbox_mm",
    "path_points",
    "positions",
    # P1 whitelist repair (2026-06-10): entry_origin (classify_holes
    # pass-23) is PREFERRED by plan_from_feature_catalog for box-mode hole
    # positions; plane_origin anchors detect_mirror_symmetry planes;
    # centroid is an accepted coordinate key in feature_fidelity_diff.
    # Direction vectors (axis_dir / plane_normal / axis_direction) stay
    # OUT — unit directions must never scale.
    "entry_origin",
    "plane_origin",
    "centroid",
})


def _scale_value(value: Any, factor: float) -> Any:
    """Recursively scale numerics in ``value`` by ``factor``.

    - int / float       → value * factor (returned as float)
    - list / tuple      → element-wise scale (same container kind)
    - everything else   → returned unchanged
    """
    if isinstance(value, bool):
        # bools are ints in Python — but never a dimensional scalar.
        return value
    if isinstance(value, (int, float)):
        return float(value) * float(factor)
    if isinstance(value, list):
        return [_scale_value(v, factor) for v in value]
    if isinstance(value, tuple):
        return tuple(_scale_value(v, factor) for v in value)
    return value


def _apply_uniform_scale(
    node: Any, factor: float, exclude: frozenset[str] = frozenset(),
) -> None:
    """In-place: walk ``node`` and scale every dimensional field by ``factor``.

    Dicts: for keys in ``_SCALAR_DIM_FIELDS`` ∪ ``_VECTOR_DIM_FIELDS`` scale
    the value; recurse into every value regardless of key.
    Lists/tuples: recurse into elements (cannot scale by key — list parents
    pass key context via the field they live under).

    ``exclude`` (P7, 2026-06-10): field names that must NOT be uniformly
    scaled even though they are whitelisted — e.g. ``base_thickness_mm``
    under ``constraints.preserve_wall_thickness``. Excluded fields are left
    untouched entirely (no scaling, no recursion into them).
    """
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if k in exclude:
                continue
            if k in _SCALAR_DIM_FIELDS or k in _VECTOR_DIM_FIELDS:
                node[k] = _scale_value(v, factor)
            else:
                _apply_uniform_scale(v, factor, exclude)
    elif isinstance(node, list):
        for item in node:
            _apply_uniform_scale(item, factor, exclude)
    # scalars and other types — nothing to recurse into.


def _split_dotted_key(key: str) -> list[str]:
    return [part for part in str(key).split(".") if part != ""]


def _navigate_to_parent(root: Any, parts: list[str]) -> tuple[Any, str | int] | None:
    """Walk ``parts[:-1]`` from ``root``; return ``(parent, last_key)``.

    List indices use integer keys; dict keys stay as strings. Returns None
    when any intermediate step is missing or out of range — the caller then
    silently skips that override (no exception so a stale dotted key on an
    older catalog version cannot crash variation).
    """
    if not parts:
        return None
    cur = root
    for part in parts[:-1]:
        if isinstance(cur, list):
            try:
                idx = int(part)
            except ValueError:
                return None
            if idx < 0 or idx >= len(cur):
                return None
            cur = cur[idx]
        elif isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        else:
            return None
    last = parts[-1]
    if isinstance(cur, list):
        try:
            last_key: str | int = int(last)
        except ValueError:
            return None
        if not (0 <= last_key < len(cur)):
            return None
    elif isinstance(cur, dict):
        last_key = last
        if last_key not in cur:
            return None
    else:
        return None
    return (cur, last_key)


def _apply_per_feature_scale(
    root: dict,
    per_feature_scale: dict,
    unresolved: list[str] | None = None,
) -> None:
    """In-place: for each dotted key in ``per_feature_scale`` multiply the
    targeted value by its scale (after the uniform pass has already run).
    Missing keys are skipped; when ``unresolved`` is given (P8) the skipped
    dotted keys are appended to it so callers can surface / raise on them.
    """
    for dotted_key, scale in per_feature_scale.items():
        parts = _split_dotted_key(dotted_key)
        nav = _navigate_to_parent(root, parts)
        if nav is None:
            if unresolved is not None:
                unresolved.append(str(dotted_key))
            continue
        parent, last = nav
        current = parent[last]
        parent[last] = _scale_value(current, scale)


def _apply_absolute_overrides(
    root: dict,
    absolute_overrides: dict,
    unresolved: list[str] | None = None,
) -> None:
    """In-place: for each dotted key in ``absolute_overrides`` assign the
    given value EXACTLY (no scaling). Missing parent paths are skipped; when
    ``unresolved`` is given (P8) the skipped dotted keys are appended to it.
    """
    for dotted_key, value in absolute_overrides.items():
        parts = _split_dotted_key(dotted_key)
        nav = _navigate_to_parent(root, parts)
        if nav is None:
            if unresolved is not None:
                unresolved.append(str(dotted_key))
            continue
        parent, last = nav
        parent[last] = value


def vary_catalog_ex(
    catalog: dict,
    scale_factor: float | None = None,
    per_feature_scale: dict | None = None,
    absolute_overrides: dict | None = None,
    exclude_fields: set[str] | frozenset[str] | None = None,
) -> tuple[dict, dict]:
    """Pure function (P8): deep-copy ``catalog``, apply the variation rules,
    and return ``(varied_catalog, warnings)``.

    ``warnings`` schema::

        {"unresolved_overrides": [dotted_key, ...]}

    Every ``per_feature_scale`` / ``absolute_overrides`` dotted key whose
    path does not resolve inside the catalog (typo, stale index, older
    catalog version) is collected instead of being silently dropped — the
    pre-P8 behaviour produced bit-identical plans for typo'd keys with no
    signal at all.

    ``exclude_fields`` (P7): whitelisted field names exempted from the
    uniform ``scale_factor`` pass — e.g. ``{"base_thickness_mm"}`` under
    ``constraints.preserve_wall_thickness``. Per-feature / absolute
    overrides still reach excluded fields (explicit intent beats the
    exemption).
    """
    new_catalog = copy.deepcopy(catalog or {})
    unresolved: list[str] = []
    if scale_factor is not None and float(scale_factor) != 1.0:
        _apply_uniform_scale(
            new_catalog, float(scale_factor),
            frozenset(exclude_fields or ()),
        )
    if per_feature_scale:
        _apply_per_feature_scale(
            new_catalog, dict(per_feature_scale), unresolved,
        )
    if absolute_overrides:
        _apply_absolute_overrides(
            new_catalog, dict(absolute_overrides), unresolved,
        )
    return new_catalog, {"unresolved_overrides": unresolved}


def vary_catalog(
    catalog: dict,
    scale_factor: float | None = None,
    per_feature_scale: dict | None = None,
    absolute_overrides: dict | None = None,
    exclude_fields: set[str] | frozenset[str] | None = None,
) -> dict:
    """Back-compat thin wrapper around :func:`vary_catalog_ex` returning only
    the varied catalog (pre-P8 signature). New callers that care about
    unresolved dotted keys should use ``vary_catalog_ex`` instead.
    """
    new_catalog, _warnings = vary_catalog_ex(
        catalog,
        scale_factor=scale_factor,
        per_feature_scale=per_feature_scale,
        absolute_overrides=absolute_overrides,
        exclude_fields=exclude_fields,
    )
    return new_catalog


@skill(
    name="vary_feature_catalog",
    category="reverse_engineer",
    level="atomic",
    summary="Produce a parametric variation of a feature_catalog by applying "
            "a uniform scale_factor to all dimensional fields, followed by "
            "per_feature_scale multipliers and absolute_overrides on dotted "
            "keys (e.g. 'pockets.0.depth_mm'). Body unchanged. "
            "extras['varied_catalog'] carries the new catalog dict.",
    selector_kinds=[],
    history_rules={},
    produces_features=["varied_catalog"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class VaryFeatureCatalog(SkillBase):
    class Args(BaseModel):
        # P8 (2026-06-10): unknown arg keys raise ValidationError instead of
        # being silently dropped (e.g. 'absolut_overrides' typo previously
        # produced an unvaried catalog with no signal).
        model_config = ConfigDict(extra="forbid")

        catalog: dict = Field(
            default_factory=dict,
            description="The feature_catalog dict to vary (produced by "
                        "ExtractFeatureCatalog).",
        )
        scale_factor: float | None = Field(
            default=None,
            description="Uniform multiplier applied to every recognised "
                        "dimensional field (top_d_mm, depth_mm, diameter_mm, "
                        "length_mm, width_mm, height_mm, axis_origin, center, "
                        "diameters_mm, ...). None / 1.0 ⇒ no uniform scaling.",
        )
        per_feature_scale: dict = Field(
            default_factory=dict,
            description="Dotted-key → multiplier overrides applied AFTER the "
                        "uniform pass. Example: "
                        "{'pockets.0.depth_mm': 2.0, "
                        "'holes.2.diameter_mm': 5.0}. Missing paths are "
                        "silently ignored.",
        )
        absolute_overrides: dict = Field(
            default_factory=dict,
            description="Dotted-key → absolute value overrides applied LAST "
                        "(beats both scale_factor and per_feature_scale). "
                        "Example: {'holes.0.diameter_mm': 8.5}.",
        )
        strict: bool = Field(
            default=False,
            description="P8: when True, raise ValueError if any "
                        "per_feature_scale / absolute_overrides dotted key "
                        "does not resolve inside the catalog (typo / stale "
                        "index). When False the unresolved keys are reported "
                        "in extras['unresolved_overrides'] instead.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        varied, warnings = vary_catalog_ex(
            args.catalog,
            scale_factor=args.scale_factor,
            per_feature_scale=args.per_feature_scale,
            absolute_overrides=args.absolute_overrides,
        )
        unresolved = warnings.get("unresolved_overrides") or []
        if args.strict and unresolved:
            raise ValueError(
                "vary_feature_catalog: unresolved override dotted key(s) "
                f"(strict=True): {unresolved}"
            )
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "varied_catalog": varied,
                "unresolved_overrides": unresolved,
            },
        )
