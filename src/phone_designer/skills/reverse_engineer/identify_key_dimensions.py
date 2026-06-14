"""identify_key_dimensions — atomic, read-only (Pillar VARIANTS, 2026-06-14).

From a ``feature_catalog`` (the dict shape produced by
:class:`ExtractFeatureCatalog`), rank and name the *driving dimensions* a
designer would expose in a "main dimensions" menu — the small set of values a
UI / API surfaces for a user to tweak when generating a parametric variant.

The variation pipeline (``vary_feature_catalog`` / ``normalize_varied_catalog``
/ ``plan_from_scaled_catalog``) can already CHANGE any dimensional field, but
nothing told it WHICH fields are the salient, user-facing ones. This skill
builds that menu.

Roles emitted (priority order, highest first)::

    envelope      — overall bounding box length / width / height (the three
                    initial_bbox_mm extents). Almost always what a user means
                    by "make it bigger".
    primary_bore  — the largest / most-standard-matched hole diameter (the main
                    through-bore of a linkrod eye, a screw clearance hole, ...).
    pitch         — the dominant hole-pattern pitch: a circular array's
                    radius_mm (bolt-circle radius) or a linear array's
                    spacing_mm.
    wall          — base / wall thickness (catalog ``base_thickness_mm``).
    pocket        — the dominant pocket footprint (largest top_d_mm pocket).

Each emitted entry is a dict::

    {
      "name":           str,    # human label e.g. "housing_length"
      "value_mm":       float,  # the resolved value from the catalog
      "dotted_key":     str,    # RESOLVABLE vary_feature_catalog dotted key
      "role":           str,    # one of the roles above
      "rank":           int,    # 0-based, lower = more salient
      "standard_match": dict | None,  # present on primary_bore only
    }

The ``dotted_key`` of every emitted entry is guaranteed to resolve through
``vary_feature_catalog._navigate_to_parent`` against the SAME catalog — entries
whose key does not resolve are dropped (so a UI can blindly hand any dotted_key
back to ``vary_feature_catalog`` / ``apply_variant_drivers`` and it will land).

Salience score (lower rank = larger score) = role priority weight + a geometric
salience term = (dimension value / bbox diagonal). Envelope dims keep the
biggest role weight so they sort to the top, the primary bore comes next, then
pitch, wall, pocket — but within the same role the *bigger* dimension ranks
first, and a hole carrying a confident standard_match is boosted so the "real"
fastener bore beats an incidental larger drilled relief.

extras schema::

    {"key_dimensions": [ {name, value_mm, dotted_key, role, rank,
                          standard_match?}, ... ]}   # sorted by rank

Body is unchanged (post body_present). Pure read-only over the catalog dict —
the body argument is only used to satisfy the post-condition.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
    _navigate_to_parent,
    _split_dotted_key,
)


# ── role priority weights ──────────────────────────────────────────────────
# Higher weight ⇒ sorts earlier (lower rank). The gaps (1.0) dwarf the
# geometric salience term (always in [0, ~1]) so role order dominates while
# the salience term only ranks dims WITHIN a role. envelope > primary bore >
# pitch > wall > pocket per the pillar contract.
_ROLE_WEIGHT: dict[str, float] = {
    "envelope": 5.0,
    "primary_bore": 4.0,
    "pitch": 3.0,
    "wall": 2.0,
    "pocket": 1.0,
}

# Confident standard_match boost (added to a primary_bore's salience term).
# Small relative to the role gap so it never lifts a bore above an envelope
# dim, but it does let a standard-matched bore outrank a non-matched one and
# nudges a fastener bore ahead of an incidental larger relief drilling.
_STD_MATCH_BOOST = 0.5
# A best_match is "confident" when its diameter deviation is within this many
# mm of a catalogued thread spec (match_standard_hole emits deviation_mm).
_STD_MATCH_DEV_MM = 0.35


def _resolves(catalog: dict, dotted_key: str) -> bool:
    """True iff ``dotted_key`` navigates to an existing slot in ``catalog``.

    Uses the EXACT same traversal vary_feature_catalog uses, so a key that
    passes here is guaranteed to land when handed to vary_feature_catalog /
    apply_variant_drivers.
    """
    parts = _split_dotted_key(dotted_key)
    return _navigate_to_parent(catalog, parts) is not None


def _resolved_value(catalog: dict, dotted_key: str) -> Any:
    """Return the value at ``dotted_key`` (or None if it does not resolve)."""
    parts = _split_dotted_key(dotted_key)
    nav = _navigate_to_parent(catalog, parts)
    if nav is None:
        return None
    parent, last = nav
    try:
        return parent[last]
    except Exception:
        return None


def _as_float(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _bbox_extents(initial_bbox: Any) -> tuple[float, float, float] | None:
    """(length, width, height) from an ``initial_bbox_mm`` 6-tuple.

    initial_bbox_mm is (xmin, ymin, zmin, xmax, ymax, zmax) in world coords
    (see ExtractFeatureCatalog). Returns the three POSITIVE extents (x, y, z)
    or None when the bbox is missing / malformed.
    """
    if not isinstance(initial_bbox, (list, tuple)) or len(initial_bbox) != 6:
        return None
    try:
        xmin, ymin, zmin, xmax, ymax, zmax = (float(c) for c in initial_bbox)
    except (TypeError, ValueError):
        return None
    return (abs(xmax - xmin), abs(ymax - ymin), abs(zmax - zmin))


def _is_confident_match(best_match: Any) -> bool:
    """True if a standard_matches[i]['best_match'] is a close thread spec."""
    if not isinstance(best_match, dict):
        return False
    dev = _as_float(best_match.get("deviation_mm"))
    if dev is None:
        return True  # a match with no deviation field — treat as present
    return dev <= _STD_MATCH_DEV_MM


def _hole_primary_diameter(hole: dict) -> tuple[float | None, str | None]:
    """Return (primary_diameter_mm, dotted_key) for a hole.

    The primary (clearance shaft) diameter is the SMALLEST entry of
    ``diameters_mm`` (matches classify_holes / extract_feature_catalog's
    ``min(diams)`` shaft convention). The dotted_key targets that exact list
    element so a UI tweak rescales the shaft bore. Falls back to the scalar
    ``diameter_mm`` field when no diameters list is present.
    """
    diams = hole.get("diameters_mm")
    if isinstance(diams, list) and diams:
        floats = [(_as_float(d), i) for i, d in enumerate(diams)]
        floats = [(d, i) for (d, i) in floats if d is not None and d > 0.0]
        if floats:
            d, idx = min(floats, key=lambda t: t[0])
            return d, f"diameters_mm.{idx}"
    d = _as_float(hole.get("diameter_mm"))
    if d is not None and d > 0.0:
        return d, "diameter_mm"
    return None, None


def identify_key_dimensions(catalog: dict) -> list[dict]:
    """Pure function: rank the driving dimensions of ``catalog``.

    Returns the sorted ``key_dimensions`` list (see module docstring). Every
    emitted ``dotted_key`` is verified to resolve through
    ``vary_feature_catalog._navigate_to_parent`` against ``catalog`` — an
    entry whose key fails to resolve is never emitted.
    """
    if not isinstance(catalog, dict) or catalog.get("skipped"):
        return []

    # bbox diagonal normalises the geometric salience term so it is scale-free
    # (a 1 mm chip and a 1 m bracket both put their envelope dims near 1.0).
    extents = _bbox_extents(catalog.get("initial_bbox_mm"))
    if extents is not None:
        diag = math.sqrt(sum(e * e for e in extents)) or 1.0
    else:
        diag = 1.0

    candidates: list[dict] = []

    def _salience(role: str, value: float, extra: float = 0.0) -> float:
        return _ROLE_WEIGHT.get(role, 0.0) + (abs(value) / diag) + extra

    # ── envelope: the three initial_bbox_mm extents ────────────────────────
    # dotted_key targets the MAX-side coordinate of each axis (index 3/4/5);
    # scaling that component stretches the box along that axis. These are the
    # canonical "make it longer / wider / taller" handles.
    if extents is not None and _resolves(catalog, "initial_bbox_mm"):
        env_axes = [
            ("housing_length", 3, extents[0]),  # x-extent ← xmax
            ("housing_width", 4, extents[1]),   # y-extent ← ymax
            ("housing_height", 5, extents[2]),  # z-extent ← zmax
        ]
        for name, comp_idx, extent in env_axes:
            if extent <= 0.0:
                continue
            dotted = f"initial_bbox_mm.{comp_idx}"
            if not _resolves(catalog, dotted):
                continue
            candidates.append({
                "name": name,
                "value_mm": round(extent, 4),
                "dotted_key": dotted,
                "role": "envelope",
                "_salience": _salience("envelope", extent),
            })

    # ── primary bore: the largest hole's shaft diameter, standard-aware ────
    holes = catalog.get("holes")
    std_matches = catalog.get("standard_matches")
    std_by_hole: dict[Any, dict] = {}
    if isinstance(std_matches, list):
        for m in std_matches:
            if isinstance(m, dict):
                std_by_hole[m.get("hole_id")] = m
    if isinstance(holes, list) and holes:
        bore_rows: list[tuple[float, int, str, dict | None, bool]] = []
        for hi, hole in enumerate(holes):
            if not isinstance(hole, dict):
                continue
            d, leaf = _hole_primary_diameter(hole)
            if d is None or leaf is None:
                continue
            dotted = f"holes.{hi}.{leaf}"
            if not _resolves(catalog, dotted):
                continue
            match = std_by_hole.get(hole.get("id"))
            best = match.get("best_match") if isinstance(match, dict) else None
            confident = _is_confident_match(best)
            bore_rows.append((d, hi, dotted, best, confident))
        if bore_rows:
            # Pick the single primary bore: prefer a confident standard match,
            # then the largest diameter. (The driving bore a designer exposes
            # is the main fastener / shaft hole — usually both the standardised
            # one and among the largest.)
            bore_rows.sort(key=lambda r: (not r[4], -r[0]))
            d, hi, dotted, best, confident = bore_rows[0]
            extra = _STD_MATCH_BOOST if confident else 0.0
            candidates.append({
                "name": "primary_bore_diameter",
                "value_mm": round(d, 4),
                "dotted_key": dotted,
                "role": "primary_bore",
                "standard_match": best if isinstance(best, dict) else None,
                "_salience": _salience("primary_bore", d, extra),
            })

    # ── pitch: dominant hole-pattern pitch (circular radius / linear spacing)
    patterns = catalog.get("patterns")
    if isinstance(patterns, list) and patterns:
        pitch_rows: list[tuple[float, int, str, str]] = []
        for pi, pat in enumerate(patterns):
            if not isinstance(pat, dict):
                continue
            pkind = pat.get("pattern_kind")
            if pkind == "circular":
                val = _as_float(pat.get("radius_mm"))
                leaf, label = "radius_mm", "bolt_circle_radius"
            elif pkind == "linear":
                val = _as_float(pat.get("spacing_mm"))
                leaf, label = "spacing_mm", "hole_pattern_pitch"
            else:
                continue
            if val is None or val <= 0.0:
                continue
            dotted = f"patterns.{pi}.{leaf}"
            if not _resolves(catalog, dotted):
                continue
            pitch_rows.append((val, pi, dotted, label))
        if pitch_rows:
            # Dominant pitch = the largest one (the bolt circle / longest run).
            pitch_rows.sort(key=lambda r: -r[0])
            val, pi, dotted, label = pitch_rows[0]
            candidates.append({
                "name": label,
                "value_mm": round(val, 4),
                "dotted_key": dotted,
                "role": "pitch",
                "_salience": _salience("pitch", val),
            })

    # ── wall: base / wall thickness ────────────────────────────────────────
    base_t = _as_float(catalog.get("base_thickness_mm"))
    if base_t is not None and base_t > 0.0 and _resolves(catalog, "base_thickness_mm"):
        candidates.append({
            "name": "wall_thickness",
            "value_mm": round(base_t, 4),
            "dotted_key": "base_thickness_mm",
            "role": "wall",
            "_salience": _salience("wall", base_t),
        })

    # ── pocket: dominant pocket footprint (largest top_d_mm) ───────────────
    pockets = catalog.get("pockets")
    if isinstance(pockets, list) and pockets:
        pocket_rows: list[tuple[float, int]] = []
        for pi, pk in enumerate(pockets):
            if not isinstance(pk, dict):
                continue
            td = _as_float(pk.get("top_d_mm"))
            if td is None or td <= 0.0:
                continue
            if not _resolves(catalog, f"pockets.{pi}.top_d_mm"):
                continue
            pocket_rows.append((td, pi))
        if pocket_rows:
            pocket_rows.sort(key=lambda r: -r[0])
            td, pi = pocket_rows[0]
            candidates.append({
                "name": "pocket_footprint_diameter",
                "value_mm": round(td, 4),
                "dotted_key": f"pockets.{pi}.top_d_mm",
                "role": "pocket",
                "_salience": _salience("pocket", td),
            })

    # ── rank: descending salience, stable ──────────────────────────────────
    candidates.sort(key=lambda c: -c["_salience"])
    out: list[dict] = []
    for rank, c in enumerate(candidates):
        entry = {
            "name": c["name"],
            "value_mm": c["value_mm"],
            "dotted_key": c["dotted_key"],
            "role": c["role"],
            "rank": rank,
        }
        if c["role"] == "primary_bore":
            entry["standard_match"] = c.get("standard_match")
        out.append(entry)
    return out


@skill(
    name="identify_key_dimensions",
    category="reverse_engineer",
    level="atomic",
    summary="Rank and name the driving 'main dimensions' a designer would "
            "expose from a feature_catalog: overall envelope (length/width/"
            "height), primary bore, hole-pattern pitch, wall thickness, "
            "dominant pocket footprint. Each entry carries a vary_feature_"
            "catalog-resolvable dotted_key. extras['key_dimensions'] is the "
            "sorted menu apply_variant_drivers consumes. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["key_dimensions"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class IdentifyKeyDimensions(SkillBase):
    class Args(BaseModel):
        catalog: dict | None = Field(
            default=None,
            description="The feature_catalog dict to analyse (produced by "
                        "ExtractFeatureCatalog). When None, the last catalog "
                        "shared by ExtractFeatureCatalog on this process is "
                        "used (mirrors plan_from_feature_catalog's _LAST_"
                        "CATALOG convention).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        catalog = args.catalog
        if catalog is None:
            # Reuse the last extracted catalog if the caller did not pass one
            # (same fallback plan_from_feature_catalog offers).
            try:
                from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
                    PlanFromFeatureCatalog,
                )
                catalog = getattr(PlanFromFeatureCatalog, "_LAST_CATALOG", None)
            except Exception:
                catalog = None
        key_dimensions = identify_key_dimensions(catalog or {})
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"key_dimensions": key_dimensions},
        )
