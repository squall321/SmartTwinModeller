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

A pure uniform scale produces ZERO warnings by construction (spacing and
positions scale together; invariants are scale-equivariant) — that is the
P6 acceptance criterion.

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
# pure entry point


def normalize_catalog(
    catalog: dict, scale_hint: float | None = None,
) -> tuple[dict, list[str]]:
    """Deep-copy ``catalog``, normalize the variation-dependent fields, and
    return ``(normalized_catalog, variation_warnings)``.

    Exposed at module level so plan_from_scaled_catalog can reuse it without
    the skill wrapper.
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
            "initial_bbox_mm. Body unchanged. extras carries "
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

    def _apply(self, body: Any, args: Args) -> SkillResult:
        normalized, warnings = normalize_catalog(
            args.catalog, scale_hint=args.scale_hint,
        )
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "normalized_catalog": normalized,
                "variation_warnings": warnings,
            },
        )
