"""normalize_varied_catalog — atomic, read-only (plan item P6).

After :func:`vary_catalog` has scaled / edited a feature_catalog, several
DERIVED fields go stale: a hole that matched M3 at 1× still says M3 at 2×
(its 3.4 mm clearance bore is now 6.8 mm — an M6, or nothing); a circular
pattern whose ``pitch_radius_mm`` was edited still carries the OLD member
positions; counterbore diameter invariants can be silently broken by
absolute overrides; a mirror plane's origin can drift outside the (scaled)
body bbox.

This skill recomputes / re-validates those dependent fields **in catalog
space** (pure dict work — no OCCT):

  1. thread/standard re-match — re-run classify_holes'
     :func:`_best_standard_match` on each hole's scaled ``diameters_mm``;
     replace the stale ``standard_match``, null it below confidence 0.6.
     Holes whose ``standard_match`` was already None are only re-matched
     when ``scale_hint`` says the catalog was actually scaled (≠ 1.0) —
     otherwise None stays None (the original pipeline chose not to match).
  2. pattern coherence — when a linear pattern's ``spacing_mm`` (or a
     circular pattern's ``pitch_radius_mm`` / ``radius_mm``) disagrees with
     its ``positions`` by > 0.5 %, recompute the positions analytically
     from anchor+spacing / center+pitch and append a warning.
  3. counterbore invariants — multi-diameter holes must satisfy
     ``max(diameters) > min(diameters)``; counterbore-family types require
     2+ DISTINCT diameters. Violations append warnings (never mutated).
  4. mirror coherence — every symmetry ``plane_origin`` must sit inside the
     catalog's (already-scaled) ``initial_bbox_mm`` — else warning.
  5. relation coherence (Pillar VARIANTS, 2026-06-14) — OPT-IN, fires ONLY
     when a ``relations`` graph (from recover_design_relations) is supplied.
     After a SINGLE-driver edit some coupled fields can go stale even though
     passes 1-4 are clean: a counterbore seat that no longer holds its
     cb_d/through_d ratio, a mirror pair whose reflected hole is no longer
     the same size, a pattern whose members drifted off the declared pitch,
     a concentric group that fell off its shared axis. Pass 5 re-measures
     each relation's residual against the VARIED catalog and appends a
     ``variation_warnings`` entry per violation. WARN-ONLY — it never mutates
     a field. ``relations=None`` ⇒ this pass is skipped entirely and the
     function is byte-identical to the pre-pillar behaviour.

A pure uniform scale produces ZERO warnings by construction (spacing and
positions scale together; invariants are scale-equivariant; and a uniform
scale holds every relation's ratio / equality / pitch) — that is the
P6 acceptance criterion, preserved across pass 5.

extras schema::

    {"normalized_catalog": {...}, "variation_warnings": ["...", ...]}

Body is unchanged (post ``body_present``). The input catalog dict is never
mutated — a deep copy is normalized and returned.
"""
from __future__ import annotations

import copy
import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.inspect.classify_holes import _best_standard_match

# Same floor the planner applies when deciding whether a standard_match is
# trustworthy enough to emit thread-spec'd hole skills.
_STD_MATCH_MIN_CONF = 0.6

# Relative disagreement between a pattern's declared spacing / pitch radius
# and the spacing / radius implied by its member positions before we
# recompute the positions analytically.
_PATTERN_COHERENCE_RTOL = 0.005  # 0.5 %


# ──────────────────────────────────────────────────────────────────────────────
# small vector helpers (pure python — positions are 3-lists or tuples)


def _v3(p: Any) -> tuple[float, float, float]:
    return (float(p[0]), float(p[1]), float(p[2]))


def _sub(a, b) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, s: float) -> tuple[float, float, float]:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a) -> tuple[float, float, float] | None:
    n = _norm(a)
    if n < 1e-12:
        return None
    return (a[0] / n, a[1] / n, a[2] / n)


# ──────────────────────────────────────────────────────────────────────────────
# pass 1 — thread / standard re-match


def _rematch_hole_standards(holes: list, scale_hint: float | None) -> None:
    """Re-run classify_holes' standard matcher on each hole's (scaled)
    diameters. Mutates ``holes`` in place (they live in the deep copy).

    No warnings are appended for the re-match itself — replacing a stale
    match IS the normalization, not a violation (a pure uniform scale must
    yield zero warnings).
    """
    scaled = scale_hint is not None and float(scale_hint) != 1.0
    for hole in holes:
        if not isinstance(hole, dict):
            continue
        had_match = isinstance(hole.get("standard_match"), dict)
        if not had_match and not scaled:
            # The original pipeline chose not to (or could not) match this
            # hole and nothing says the diameters moved — leave None alone.
            continue
        diams = [float(d) for d in (hole.get("diameters_mm") or [])
                 if isinstance(d, (int, float))]
        if not diams:
            continue
        primary_d = min(diams)
        distinct = {round(d, 6) for d in diams}
        cb_d = max(diams) if len(distinct) >= 2 else None
        try:
            match = _best_standard_match(primary_d, cb_d, None)
        except Exception:
            match = None
        if isinstance(match, dict):
            conf = float(match.get("confidence") or 0.0)
            if conf < _STD_MATCH_MIN_CONF:
                match = None
        hole["standard_match"] = match


# ──────────────────────────────────────────────────────────────────────────────
# pass 2 — pattern coherence


def _pattern_path(idx: int, pat: dict) -> str:
    kind = pat.get("pattern_kind") or "pattern"
    return f"patterns.{idx} ({kind})"


def _recompute_linear_positions(
    idx: int, pat: dict, warnings: list[str],
) -> None:
    positions = pat.get("positions")
    spacing = pat.get("spacing_mm")
    if not positions or len(positions) < 2 or not spacing:
        return
    spacing = float(spacing)
    if spacing <= 0.0:
        return
    pts = [_v3(p) for p in positions]
    gaps = [_norm(_sub(pts[i + 1], pts[i])) for i in range(len(pts) - 1)]
    mean_gap = sum(gaps) / len(gaps)
    if abs(mean_gap - spacing) <= _PATTERN_COHERENCE_RTOL * spacing:
        return
    direction = pat.get("direction")
    d_unit = _unit(_v3(direction)) if direction else None
    if d_unit is None:
        d_unit = _unit(_sub(pts[-1], pts[0]))
    if d_unit is None:
        warnings.append(
            f"{_pattern_path(idx, pat)}: spacing_mm={spacing} disagrees with "
            f"positions (mean gap {mean_gap:.4f} mm) but no usable direction "
            "to recompute from — positions left as-is"
        )
        return
    anchor = pts[0]
    pat["positions"] = [
        list(_add(anchor, _mul(d_unit, spacing * i)))
        for i in range(len(pts))
    ]
    warnings.append(
        f"{_pattern_path(idx, pat)}: spacing_mm={spacing} disagreed with "
        f"member positions (mean gap {mean_gap:.4f} mm, "
        f">{_PATTERN_COHERENCE_RTOL:.1%}) — positions recomputed from "
        "anchor + spacing*direction"
    )


def _recompute_circular_positions(
    idx: int, pat: dict, warnings: list[str],
) -> None:
    positions = pat.get("positions")
    pitch = pat.get("pitch_radius_mm")
    if pitch is None:
        pitch = pat.get("radius_mm")
    center = pat.get("center")
    if not positions or len(positions) < 2 or not pitch or not center:
        return
    pitch = float(pitch)
    if pitch <= 0.0:
        return
    c = _v3(center)
    axis = pat.get("axis")
    ax_unit = _unit(_v3(axis)) if axis else None

    pts = [_v3(p) for p in positions]
    in_plane: list[tuple[tuple[float, float, float], float]] = []
    radii: list[float] = []
    for p in pts:
        v = _sub(p, c)
        if ax_unit is not None:
            h = _dot(v, ax_unit)
            v_in = _sub(v, _mul(ax_unit, h))
        else:
            h = 0.0
            v_in = v
        in_plane.append((v_in, h))
        radii.append(_norm(v_in))
    valid = [r for r in radii if r > 1e-12]
    if not valid:
        return
    mean_r = sum(valid) / len(valid)
    if abs(mean_r - pitch) <= _PATTERN_COHERENCE_RTOL * pitch:
        return
    new_positions: list[list[float]] = []
    for p, (v_in, h), r in zip(pts, in_plane, radii):
        if r <= 1e-12:
            # Degenerate member sitting at the center — angle undefined.
            new_positions.append(list(p))
            continue
        radial = _mul(v_in, pitch / r)
        out = _add(c, radial)
        if ax_unit is not None:
            out = _add(out, _mul(ax_unit, h))
        new_positions.append(list(out))
    pat["positions"] = new_positions
    warnings.append(
        f"{_pattern_path(idx, pat)}: pitch radius {pitch} mm disagreed with "
        f"member positions (mean radius {mean_r:.4f} mm, "
        f">{_PATTERN_COHERENCE_RTOL:.1%}) — positions recomputed from "
        "center + pitch (angles preserved)"
    )


def _check_pattern_coherence(patterns: list, warnings: list[str]) -> None:
    for idx, pat in enumerate(patterns):
        if not isinstance(pat, dict):
            continue
        kind = pat.get("pattern_kind")
        if kind == "linear":
            _recompute_linear_positions(idx, pat, warnings)
        elif kind == "circular":
            _recompute_circular_positions(idx, pat, warnings)


# ──────────────────────────────────────────────────────────────────────────────
# pass 3 — counterbore invariants (warn only, never mutate)

_CB_FAMILY_TYPES = frozenset({"counterbore", "counterdrill", "spotface"})


def _check_counterbore_invariants(holes: list, warnings: list[str]) -> None:
    for hole in holes:
        if not isinstance(hole, dict):
            continue
        hid = hole.get("id")
        diams = [float(d) for d in (hole.get("diameters_mm") or [])
                 if isinstance(d, (int, float))]
        if len(diams) >= 2 and not (max(diams) > min(diams)):
            warnings.append(
                f"holes.{hid}: multi-diameter hole violates "
                f"max(diameters) > min(diameters) — diameters_mm={diams}"
            )
        htype = hole.get("type")
        if htype in _CB_FAMILY_TYPES:
            distinct = {round(d, 6) for d in diams}
            if len(distinct) < 2:
                warnings.append(
                    f"holes.{hid}: type '{htype}' implies 2+ distinct "
                    f"diameters but diameters_mm={diams}"
                )


# ──────────────────────────────────────────────────────────────────────────────
# pass 4 — mirror coherence (warn only)


def _check_mirror_coherence(catalog: dict, warnings: list[str]) -> None:
    bbox = catalog.get("initial_bbox_mm")
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 6:
        return
    try:
        xmin, ymin, zmin, xmax, ymax, zmax = (float(c) for c in bbox[:6])
    except (TypeError, ValueError):
        return
    extent = max(xmax - xmin, ymax - ymin, zmax - zmin, 1.0)
    eps = 1e-6 * extent
    for idx, sym in enumerate(catalog.get("symmetries") or []):
        if not isinstance(sym, dict):
            continue
        po = sym.get("plane_origin")
        if not isinstance(po, (list, tuple)) or len(po) < 3:
            continue
        try:
            x, y, z = (float(c) for c in po[:3])
        except (TypeError, ValueError):
            continue
        inside = (
            xmin - eps <= x <= xmax + eps
            and ymin - eps <= y <= ymax + eps
            and zmin - eps <= z <= zmax + eps
        )
        if not inside:
            warnings.append(
                f"symmetries.{idx}: plane_origin {[x, y, z]} lies outside "
                f"the catalog initial_bbox_mm {list(bbox[:6])}"
            )


# ──────────────────────────────────────────────────────────────────────────────
# pass 5 — relation coherence (Pillar VARIANTS, 2026-06-14; opt-in, warn only)
#
# Re-measure each supplied design_relation's residual against the VARIED
# catalog and warn when a coupled invariant no longer holds. Never mutates a
# field — a single-driver edit may LEGITIMATELY break a relation (the user
# asked for an incoherent edit), so this is advisory, exactly like passes 3/4.

# A counterbore ratio / mirror equality / concentric lateral offset is judged
# against this relative tolerance (matches the 0.5 % pattern-coherence rtol so
# a pure uniform scale — which holds every ratio exactly — never warns).
_RELATION_COHERENCE_RTOL = 0.005  # 0.5 %
# Concentric lateral drift floor (mm) — the same scale recover_design_relations
# groups co-axial features within. A residual offset above this reads "fell off
# the shared axis".
_RELATION_CONCENTRIC_TOL_MM = 0.25


def _value_at(catalog: dict, dotted_key: str) -> Any:
    """Read the value at ``dotted_key`` using the SAME traversal
    vary_feature_catalog / recover_design_relations use (so a member key that
    resolved when the relation was recovered resolves here too). None on miss.

    Imported lazily to avoid a hard import cycle at module load — the helper
    lives in vary_feature_catalog, the variation pipeline's root.
    """
    from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
        _navigate_to_parent,
        _split_dotted_key,
    )

    nav = _navigate_to_parent(catalog, _split_dotted_key(dotted_key))
    if nav is None:
        return None
    parent, last = nav
    try:
        return parent[last]
    except Exception:
        return None


def _rel_float(catalog: dict, dotted_key: str) -> float | None:
    v = _value_at(catalog, dotted_key)
    try:
        if isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _check_counterbore_relation(
    catalog: dict, rel: dict, warnings: list[str],
) -> None:
    members = rel.get("members") or []
    if len(members) != 2:
        return
    declared = rel.get("value")
    try:
        declared = float(declared) if declared is not None else None
    except (TypeError, ValueError):
        declared = None
    if declared is None or declared <= 0.0:
        return
    through = _rel_float(catalog, members[0])
    cb = _rel_float(catalog, members[1])
    if through is None or cb is None or through <= 0.0:
        return
    actual = cb / through
    if abs(actual - declared) > _RELATION_COHERENCE_RTOL * declared:
        warnings.append(
            f"relation counterbore {members[0]} ↔ {members[1]}: cb/through "
            f"ratio {actual:.4f} drifted from the recovered {declared:.4f} "
            f"(>{_RELATION_COHERENCE_RTOL:.1%}) after variation — the seat is "
            "no longer proportioned to the through bore"
        )


def _check_mirror_relation(
    catalog: dict, rel: dict, warnings: list[str],
) -> None:
    members = rel.get("members") or []
    if len(members) != 2:
        return
    a = _rel_float(catalog, members[0])
    b = _rel_float(catalog, members[1])
    if a is None or b is None:
        return
    ref = max(abs(a), abs(b), 1e-9)
    if abs(a - b) > _RELATION_COHERENCE_RTOL * ref:
        warnings.append(
            f"relation mirror {members[0]} ↔ {members[1]}: reflected "
            f"dimensions {a:.4f} vs {b:.4f} are no longer equal "
            f"(>{_RELATION_COHERENCE_RTOL:.1%}) after variation — a reflected "
            "hole must stay the same size as its partner"
        )


def _check_pattern_pitch_relation(
    catalog: dict, rel: dict, warnings: list[str],
) -> None:
    members = rel.get("members") or []
    if not members:
        return
    declared = _rel_float(catalog, members[0])  # the pitch handle
    if declared is None or declared <= 0.0:
        return
    # The pattern's positions live alongside the pitch handle
    # (patterns.<i>.<pitch_leaf> → patterns.<i>.positions). Re-measure the
    # pitch the member positions actually imply and compare.
    pitch_key = members[0]
    base = pitch_key.rsplit(".", 1)[0]  # patterns.<i>
    positions = _value_at(catalog, f"{base}.positions")
    if not isinstance(positions, list) or len(positions) < 2:
        return
    kind = rel.get("pattern_kind")
    if kind == "linear":
        pts = [_v3(p) for p in positions if isinstance(p, (list, tuple))]
        if len(pts) < 2:
            return
        gaps = [_norm(_sub(pts[i + 1], pts[i])) for i in range(len(pts) - 1)]
        measured = sum(gaps) / len(gaps) if gaps else None
        label = "spacing"
    elif kind == "circular":
        center = _value_at(catalog, f"{base}.center")
        if not isinstance(center, (list, tuple)) or len(center) < 3:
            return
        c = _v3(center)
        axis = _value_at(catalog, f"{base}.axis")
        ax = _unit(_v3(axis)) if isinstance(axis, (list, tuple)) else None
        radii: list[float] = []
        for p in positions:
            if not isinstance(p, (list, tuple)) or len(p) < 3:
                continue
            v = _sub(_v3(p), c)
            if ax is not None:
                v = _sub(v, _mul(ax, _dot(v, ax)))
            r = _norm(v)
            if r > 1e-9:
                radii.append(r)
        measured = sum(radii) / len(radii) if radii else None
        label = "pitch radius"
    else:
        return
    if measured is None or measured <= 0.0:
        return
    if abs(measured - declared) > _RELATION_COHERENCE_RTOL * declared:
        warnings.append(
            f"relation pattern_pitch {pitch_key}: declared {label} "
            f"{declared:.4f} disagrees with member positions "
            f"({measured:.4f}, >{_RELATION_COHERENCE_RTOL:.1%}) after "
            "variation — members drifted off the pitch (re-derive positions "
            "or restore the pitch)"
        )


def _check_concentric_relation(
    catalog: dict, rel: dict, warnings: list[str],
) -> None:
    members = rel.get("members") or []
    if len(members) < 2:
        return
    origins: list[tuple[str, tuple[float, float, float]]] = []
    for key in members:
        v = _value_at(catalog, key)
        p = _v3(v) if isinstance(v, (list, tuple)) and len(v) >= 3 else None
        if p is not None:
            origins.append((key, p))
    if len(origins) < 2:
        return
    # All members share ONE axis line; a concentric 'equal' relation pins the
    # lateral centre. Measure the worst pairwise lateral offset (axis assumed
    # +Z when unknown — recover_design_relations grouped on the shared axis;
    # here we conservatively use the full 3-distance, which upper-bounds the
    # lateral component, so a clean stack still reads ~0).
    _, base_o = origins[0]
    worst = 0.0
    for _key, o in origins[1:]:
        worst = max(worst, _norm(_sub(o, base_o)))
    if worst > _RELATION_CONCENTRIC_TOL_MM:
        warnings.append(
            f"relation concentric {members}: members drifted "
            f"{worst:.4f} mm apart (>{_RELATION_CONCENTRIC_TOL_MM} mm) after "
            "variation — the co-axial group is no longer concentric"
        )


def _check_relation_coherence(
    catalog: dict, relations: list, warnings: list[str],
) -> None:
    """Pass 5 dispatcher — re-measure each relation's residual (warn only)."""
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        kind = rel.get("kind")
        if kind == "counterbore":
            _check_counterbore_relation(catalog, rel, warnings)
        elif kind == "mirror":
            _check_mirror_relation(catalog, rel, warnings)
        elif kind == "pattern_pitch":
            _check_pattern_pitch_relation(catalog, rel, warnings)
        elif kind == "concentric":
            _check_concentric_relation(catalog, rel, warnings)


# ──────────────────────────────────────────────────────────────────────────────
# pure entry point


def normalize_catalog(
    catalog: dict,
    scale_hint: float | None = None,
    relations: list | None = None,
) -> tuple[dict, list[str]]:
    """Deep-copy ``catalog``, normalize the variation-dependent fields, and
    return ``(normalized_catalog, variation_warnings)``.

    Exposed at module level so plan_from_scaled_catalog can reuse it without
    the skill wrapper.

    ``relations`` (Pillar VARIANTS, 2026-06-14): when a design_relations graph
    (from recover_design_relations) is supplied, the OPT-IN pass-5 relation
    coherence check runs after the standard passes and appends a warning per
    violated relation (WARN-ONLY — never mutates). ``relations=None`` leaves
    the result byte-identical to the pre-pillar behaviour.
    """
    normalized = copy.deepcopy(catalog or {})
    warnings: list[str] = []

    holes = normalized.get("holes") or []
    patterns = normalized.get("patterns") or []
    if isinstance(holes, list):
        _rematch_hole_standards(holes, scale_hint)
        _check_counterbore_invariants(holes, warnings)
    if isinstance(patterns, list):
        _check_pattern_coherence(patterns, warnings)
    _check_mirror_coherence(normalized, warnings)
    # pass 5 — opt-in relation coherence (only when a graph is supplied).
    if relations:
        _check_relation_coherence(normalized, list(relations), warnings)
    return normalized, warnings


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="normalize_varied_catalog",
    category="reverse_engineer",
    level="atomic",
    summary="Recompute the derived fields of a varied feature_catalog: "
            "re-match each hole's standard_match against its scaled "
            "diameters_mm (null below confidence 0.6), recompute pattern "
            "positions when spacing_mm / pitch_radius_mm disagrees with "
            "them by >0.5%, check counterbore diameter invariants, and "
            "verify mirror plane_origins sit inside the scaled "
            "initial_bbox_mm. When a 'relations' graph "
            "(recover_design_relations) is supplied, an opt-in 5th pass "
            "re-measures each relation's residual after variation "
            "(counterbore ratio / mirror equality / pattern pitch / "
            "concentric) and warns per violation (warn-only, never mutates; "
            "relations=None ⇒ unchanged). Body unchanged. extras carries "
            "'normalized_catalog' and 'variation_warnings'.",
    selector_kinds=[],
    history_rules={},
    produces_features=["normalized_catalog"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class NormalizeVariedCatalog(SkillBase):
    class Args(BaseModel):
        catalog: dict = Field(
            default_factory=dict,
            description="The (varied) feature_catalog dict to normalize — "
                        "typically the output of vary_feature_catalog.",
        )
        scale_hint: float | None = Field(
            default=None,
            description="The uniform scale_factor that produced this "
                        "catalog, when known. Used to decide whether holes "
                        "whose standard_match was None should also be "
                        "re-matched (their diameters moved). None / 1.0 ⇒ "
                        "only holes with an existing standard_match are "
                        "re-matched.",
        )
        relations: list | None = Field(
            default=None,
            description="design_relations from recover_design_relations "
                        "(Pillar VARIANTS, 2026-06-14). When supplied, the "
                        "opt-in pass-5 relation coherence check re-measures "
                        "each relation's residual against the varied catalog "
                        "and appends a variation_warnings entry per violated "
                        "coupling (counterbore ratio, mirror equality, pattern "
                        "pitch, concentric). WARN-ONLY — never mutates. None ⇒ "
                        "byte-identical to the pre-pillar normalization.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        normalized, warnings = normalize_catalog(
            args.catalog, scale_hint=args.scale_hint,
            relations=args.relations,
        )
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "normalized_catalog": normalized,
                "variation_warnings": warnings,
            },
        )
