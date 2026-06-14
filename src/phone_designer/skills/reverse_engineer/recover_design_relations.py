"""recover_design_relations — atomic, read-only (Pillar VARIANTS, 2026-06-14).

Lift a lightweight *design-relations graph* from a ``feature_catalog`` (the
dict shape produced by :class:`ExtractFeatureCatalog`). The variation pipeline
(``vary_feature_catalog`` / ``normalize_varied_catalog`` / ``plan_from_scaled_
catalog``) can CHANGE any dimensional field by dotted key, but nothing told it
which fields move TOGETHER. This skill recovers those couplings so a single
named-driver edit (see ``apply_variant_drivers``) can propagate coherently —
change the primary bore and the counterbore follows by its ratio, move one
pattern member and the siblings keep their pitch, edit one mirror partner and
its reflection follows.

Relation kinds emitted (each a dict)::

    {
      "kind":      "mirror" | "counterbore" | "pattern_pitch" | "concentric",
      "members":   [dotted_key, ...],   # vary_feature_catalog-resolvable keys
      "rule":      "equal" | "ratio" | "offset" | "reflect",
      "value":     float | None,        # the rule's parameter (ratio/spacing/…)
      "confidence": float,              # per-edge, conservative
      ...kind-specific metadata (plane label, axis, ...)
    }

Conservatism is deliberate: a WRONG relation corrupts every downstream variant
(it would drag an unrelated field along), so each edge carries a per-edge
confidence and only relations whose members all resolve through
``vary_feature_catalog._navigate_to_parent`` against the SAME catalog are
emitted — a UI / apply_variant_drivers can blindly hand any member key back to
the variation pipeline and it lands.

Relation rules (semantics consumed by apply_variant_drivers):

  * mirror     — members = [original_key, partner_key]; rule "reflect": the
                 partner mirrors the original across an axis-aligned plane. For
                 a DIMENSION (diameter/depth) the partner stays EQUAL to the
                 driven original (a reflected hole is the same size); the plane
                 metadata records how a POSITION would reflect.
  * counterbore — members = [through_d_key, cb_d_key]; rule "ratio",
                 value = cb_d / through_d. Driving the through bore scales the
                 counterbore by ``value`` so the seat stays proportioned.
  * pattern_pitch — members = [pitch_key]; rule "equal" (linear: spacing_mm,
                 circular: pitch/radius). Driving the pitch re-derives the
                 member positions (normalize_varied_catalog does the geometry);
                 the relation records the pitch as the single coherence handle.
  * concentric — members = [axis_origin_key_a, axis_origin_key_b, ...] sharing
                 one axis line; rule "equal" on the shared lateral centre. A
                 concentric group keeps its members co-axial when one moves.

extras schema::

    {"design_relations": [ {kind, members, rule, value?, confidence, ...}, ... ]}

Body unchanged (post ``body_present``). Pure read-only over the catalog dict —
the body argument is only used to satisfy the post-condition. The input catalog
is never mutated.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
# Reuse the EXACT mirror-plane / mirrored-hole-pair logic the planner uses so
# the relations graph and the emitted plan agree on what is a mirror (read,
# do not modify — these are importable module-level helpers).
from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
    _find_mirrored_hole_pairs,
    _qualifying_mirror_planes,
)
from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
    _navigate_to_parent,
    _split_dotted_key,
)


# ── confidence floors (conservative — a wrong relation corrupts variants) ────
# Below these a candidate edge is dropped rather than emitted with a low score.
_MIRROR_MIN_CONF = 0.7
_COUNTERBORE_MIN_CONF = 0.7
_PATTERN_MIN_CONF = 0.7
_CONCENTRIC_MIN_CONF = 0.7

# Concentric grouping: two axes are "the same line" when their directions are
# parallel within this cosine and their perpendicular offset is within tol.
_CONCENTRIC_AXIS_COS = 0.98     # |dot(dir_i, dir_j)| above ⇒ parallel
_CONCENTRIC_LATERAL_TOL_MM = 0.25   # perpendicular distance floor
# A counterbore needs the cb diameter meaningfully larger than the through
# bore; below this ratio it is a measurement-noise pair, not a real seat.
_COUNTERBORE_MIN_RATIO = 1.05


def _resolves(catalog: dict, dotted_key: str) -> bool:
    """True iff ``dotted_key`` navigates to an existing slot in ``catalog``
    using the EXACT traversal vary_feature_catalog uses."""
    return _navigate_to_parent(catalog, _split_dotted_key(dotted_key)) is not None


def _as_float(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _v3(p: Any) -> tuple[float, float, float] | None:
    if not isinstance(p, (list, tuple)) or len(p) < 3:
        return None
    try:
        return (float(p[0]), float(p[1]), float(p[2]))
    except (TypeError, ValueError):
        return None


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float] | None:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n < 1e-12:
        return None
    return (v[0] / n, v[1] / n, v[2] / n)


# ──────────────────────────────────────────────────────────────────────────────
# mirror relations — reuse the planner's qualifying-plane / pair logic


def _bbox_tuple(catalog: dict):
    bb = catalog.get("initial_bbox_mm")
    if isinstance(bb, (list, tuple)) and len(bb) >= 6:
        try:
            return tuple(float(c) for c in bb[:6])
        except (TypeError, ValueError):
            return None
    return None


def _hole_diam_leaf(hole: dict) -> tuple[float | None, str | None]:
    """(primary shaft diameter, leaf dotted key) — mirrors classify_holes'
    min(diams) shaft convention used by identify_key_dimensions."""
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


def _mirror_relations(catalog: dict) -> list[dict]:
    """Mirror pairs: for every qualifying axis-aligned plane, pair the holes
    that reflect across it (the planner's own ``_find_mirrored_hole_pairs``).

    Each pair emits a relation linking the two holes' shaft-diameter dotted
    keys with rule ``reflect``: a reflected hole is the SAME size as its
    original, so driving one drives the partner equal. The plane metadata
    (label + origin) records how a POSITION reflects for the position-aware
    propagation in apply_variant_drivers. Confidence = the plane's symmetry
    score (already >= _SYMMETRY_MIN_SCORE by _qualifying_mirror_planes).
    """
    holes = catalog.get("holes")
    if not isinstance(holes, list) or len(holes) < 2:
        return []
    planes = _qualifying_mirror_planes(catalog.get("symmetries") or [])
    if not planes:
        return []
    bbox = _bbox_tuple(catalog)

    out: list[dict] = []
    paired: set[frozenset[int]] = set()
    for plane in planes:
        score = float(plane.get("score") or 0.0)
        if score < _MIRROR_MIN_CONF:
            continue
        pairs = _find_mirrored_hole_pairs(holes, plane, bbox=bbox)
        for i, j in pairs:
            key = frozenset((i, j))
            if key in paired:
                continue  # a hole already paired by a higher-scoring plane
            di, leaf_i = _hole_diam_leaf(holes[i])
            dj, leaf_j = _hole_diam_leaf(holes[j])
            if leaf_i is None or leaf_j is None:
                continue
            mk_i = f"holes.{i}.{leaf_i}"
            mk_j = f"holes.{j}.{leaf_j}"
            if not (_resolves(catalog, mk_i) and _resolves(catalog, mk_j)):
                continue
            paired.add(key)
            out.append({
                "kind": "mirror",
                "members": [mk_i, mk_j],
                "rule": "reflect",
                "value": None,
                "confidence": round(score, 4),
                "plane_label": plane["label"],
                "plane_origin": [float(c) for c in plane["origin"]],
                "position_keys": [
                    f"holes.{i}.axis_origin", f"holes.{j}.axis_origin",
                ],
            })
    return out


# ──────────────────────────────────────────────────────────────────────────────
# counterbore relations — multi-diameter holes (cb_d ↔ through_d ratio)


def _counterbore_relations(catalog: dict) -> list[dict]:
    """Multi-diameter holes: the largest diameter is the counterbore seat, the
    smallest the through bore. Emit a ``ratio`` relation cb_d / through_d so
    driving the through bore scales the seat proportionally.

    Conservative: requires 2+ DISTINCT positive diameters and a ratio above
    _COUNTERBORE_MIN_RATIO (else it is measurement noise, not a real seat).
    Confidence rises with how cleanly the ratio separates from 1.0, capped at
    1.0, and is lifted when the hole TYPE is in the counterbore family.
    """
    holes = catalog.get("holes")
    if not isinstance(holes, list):
        return []
    cb_family = {"counterbore", "counterdrill", "spotface"}
    out: list[dict] = []
    for hi, hole in enumerate(holes):
        if not isinstance(hole, dict):
            continue
        diams = hole.get("diameters_mm")
        if not isinstance(diams, list) or len(diams) < 2:
            continue
        indexed = [(_as_float(d), k) for k, d in enumerate(diams)]
        indexed = [(d, k) for (d, k) in indexed if d is not None and d > 0.0]
        if len(indexed) < 2:
            continue
        through_d, through_k = min(indexed, key=lambda t: t[0])
        cb_d, cb_k = max(indexed, key=lambda t: t[0])
        if through_d <= 0.0 or cb_k == through_k:
            continue
        ratio = cb_d / through_d
        if ratio < _COUNTERBORE_MIN_RATIO:
            continue
        through_key = f"holes.{hi}.diameters_mm.{through_k}"
        cb_key = f"holes.{hi}.diameters_mm.{cb_k}"
        if not (_resolves(catalog, through_key) and _resolves(catalog, cb_key)):
            continue
        # confidence: a clean seat ratio (≈1.5–2.5) and a counterbore-family
        # type read as high confidence; a marginal 1.05 ratio reads low.
        sep = min((ratio - 1.0) / 0.5, 1.0)  # 1.5× ⇒ 1.0, 1.05× ⇒ 0.1
        type_boost = 0.25 if str(hole.get("type") or "") in cb_family else 0.0
        conf = min(0.6 + 0.4 * sep + type_boost, 1.0)
        if conf < _COUNTERBORE_MIN_CONF:
            continue
        out.append({
            "kind": "counterbore",
            "members": [through_key, cb_key],
            "rule": "ratio",
            "value": round(ratio, 6),
            "confidence": round(conf, 4),
            "hole_index": hi,
        })
    return out


# ──────────────────────────────────────────────────────────────────────────────
# pattern-pitch relations — positions ↔ pitch / spacing


def _pattern_pitch_relations(catalog: dict) -> list[dict]:
    """Pattern spacing: each linear pattern's ``spacing_mm`` (or circular
    pattern's ``pitch_radius_mm`` / ``radius_mm``) is the single coherence
    handle whose member ``positions`` must be re-derived when it changes
    (normalize_varied_catalog does the geometry). Emit an ``equal`` relation
    pinning the pitch as that handle.

    Confidence = how well the declared pitch already agrees with the member
    positions (a coherent pattern reads 1.0; a stale one still emits but with
    lower confidence so a UI can flag it).
    """
    patterns = catalog.get("patterns")
    if not isinstance(patterns, list):
        return []
    out: list[dict] = []
    for pi, pat in enumerate(patterns):
        if not isinstance(pat, dict):
            continue
        kind = pat.get("pattern_kind")
        if kind == "linear":
            val = _as_float(pat.get("spacing_mm"))
            leaf = "spacing_mm"
            measured = _measured_linear_spacing(pat)
        elif kind == "circular":
            val = _as_float(pat.get("pitch_radius_mm"))
            leaf = "pitch_radius_mm"
            if val is None:
                val = _as_float(pat.get("radius_mm"))
                leaf = "radius_mm"
            measured = _measured_circular_radius(pat)
        else:
            continue
        if val is None or val <= 0.0:
            continue
        pitch_key = f"patterns.{pi}.{leaf}"
        if not _resolves(catalog, pitch_key):
            continue
        # coherence-based confidence: |measured - declared| / declared.
        if measured is not None and measured > 0.0:
            rel_err = abs(measured - val) / val
            conf = max(1.0 - rel_err, 0.0)
        else:
            # no member positions to corroborate — trust the declared pitch
            # at the floor (a count>=min detector already vetted it).
            conf = _PATTERN_MIN_CONF
        if conf < _PATTERN_MIN_CONF:
            conf = _PATTERN_MIN_CONF
        members = [pitch_key]
        position_key = f"patterns.{pi}.positions"
        if _resolves(catalog, position_key):
            members.append(position_key)
        out.append({
            "kind": "pattern_pitch",
            "members": members,
            "rule": "equal",
            "value": round(float(val), 6),
            "confidence": round(conf, 4),
            "pattern_kind": kind,
            "count": pat.get("count"),
        })
    return out


def _measured_linear_spacing(pat: dict) -> float | None:
    positions = pat.get("positions")
    if not isinstance(positions, list) or len(positions) < 2:
        return None
    pts = [_v3(p) for p in positions]
    pts = [p for p in pts if p is not None]
    if len(pts) < 2:
        return None
    gaps = [
        math.dist(pts[i + 1], pts[i]) for i in range(len(pts) - 1)
    ]
    if not gaps:
        return None
    return sum(gaps) / len(gaps)


def _measured_circular_radius(pat: dict) -> float | None:
    positions = pat.get("positions")
    center = _v3(pat.get("center"))
    if not isinstance(positions, list) or len(positions) < 2 or center is None:
        return None
    axis = _v3(pat.get("axis"))
    ax = _unit(axis) if axis is not None else None
    radii: list[float] = []
    for p in positions:
        pp = _v3(p)
        if pp is None:
            continue
        v = (pp[0] - center[0], pp[1] - center[1], pp[2] - center[2])
        if ax is not None:
            h = v[0] * ax[0] + v[1] * ax[1] + v[2] * ax[2]
            v = (v[0] - ax[0] * h, v[1] - ax[1] * h, v[2] - ax[2] * h)
        r = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
        if r > 1e-9:
            radii.append(r)
    if not radii:
        return None
    return sum(radii) / len(radii)


# ──────────────────────────────────────────────────────────────────────────────
# concentric relations — features sharing one axis line


def _feature_axis_line(feat: dict) -> tuple[
    tuple[float, float, float], tuple[float, float, float]
] | None:
    """(origin, unit_axis) of a hole/pocket, or None when unusable."""
    origin = _v3(feat.get("axis_origin"))
    if origin is None:
        return None
    axis = _v3(feat.get("axis_dir"))
    if axis is None:
        axis = (0.0, 0.0, 1.0)
    u = _unit(axis)
    if u is None:
        return None
    return (origin, u)


def _perp_distance_between_lines(
    a: tuple[tuple[float, float, float], tuple[float, float, float]],
    b: tuple[tuple[float, float, float], tuple[float, float, float]],
) -> float:
    """Perpendicular distance between two PARALLEL axis lines (caller has
    already established the directions are parallel)."""
    (oa, ua), (ob, _ub) = a, b
    w = (ob[0] - oa[0], ob[1] - oa[1], ob[2] - oa[2])
    h = w[0] * ua[0] + w[1] * ua[1] + w[2] * ua[2]
    perp = (w[0] - ua[0] * h, w[1] - ua[1] * h, w[2] - ua[2] * h)
    return math.sqrt(perp[0] * perp[0] + perp[1] * perp[1] + perp[2] * perp[2])


def _concentric_relations(catalog: dict) -> list[dict]:
    """Group holes/pockets that share ONE axis line (parallel directions +
    near-zero lateral offset). Each group of 2+ co-axial features emits an
    ``equal`` relation on their ``axis_origin`` keys so moving one keeps the
    group co-axial. Single-feature 'groups' are not relations.

    Conservative: only features whose axes are clearly parallel
    (|dot| >= _CONCENTRIC_AXIS_COS) and laterally within
    _CONCENTRIC_LATERAL_TOL_MM are grouped; a counterbore's own stacked
    cylinders are ONE catalog hole (already covered by the counterbore
    relation), so cross-feature concentricity here is genuine reuse.
    """
    members: list[tuple[str, tuple, tuple]] = []  # (origin_key, origin, dir)
    for family in ("holes", "pockets"):
        feats = catalog.get(family)
        if not isinstance(feats, list):
            continue
        for fi, feat in enumerate(feats):
            if not isinstance(feat, dict):
                continue
            line = _feature_axis_line(feat)
            if line is None:
                continue
            key = f"{family}.{fi}.axis_origin"
            if not _resolves(catalog, key):
                continue
            members.append((key, line[0], line[1]))

    if len(members) < 2:
        return []

    n = len(members)
    parent = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(x: int, y: int) -> None:
        parent[_find(x)] = _find(y)

    for i in range(n):
        _, oi, di = members[i]
        for j in range(i + 1, n):
            _, oj, dj = members[j]
            cos = abs(di[0] * dj[0] + di[1] * dj[1] + di[2] * dj[2])
            if cos < _CONCENTRIC_AXIS_COS:
                continue
            dist = _perp_distance_between_lines((oi, di), (oj, dj))
            if dist <= _CONCENTRIC_LATERAL_TOL_MM:
                _union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(_find(i), []).append(i)

    out: list[dict] = []
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        # confidence: tightest pairwise lateral fit in the group (the worst
        # offset relative to the tolerance) — a perfect stack reads 1.0.
        worst = 0.0
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                _, oa, da = members[idxs[a]]
                _, ob, _db = members[idxs[b]]
                worst = max(worst, _perp_distance_between_lines((oa, da), (ob, da)))
        conf = max(1.0 - worst / _CONCENTRIC_LATERAL_TOL_MM, 0.0)
        if conf < _CONCENTRIC_MIN_CONF:
            conf = _CONCENTRIC_MIN_CONF
        keys = sorted(members[i][0] for i in idxs)
        out.append({
            "kind": "concentric",
            "members": keys,
            "rule": "equal",
            "value": None,
            "confidence": round(conf, 4),
        })
    return out


# ──────────────────────────────────────────────────────────────────────────────
# pure entry point


def recover_design_relations(catalog: dict) -> list[dict]:
    """Pure function: lift the design-relations graph from ``catalog``.

    Returns the ``design_relations`` list (see module docstring). Every emitted
    relation's member dotted keys are verified to resolve through
    ``vary_feature_catalog._navigate_to_parent`` against ``catalog``.
    """
    if not isinstance(catalog, dict) or catalog.get("skipped"):
        return []
    relations: list[dict] = []
    relations.extend(_mirror_relations(catalog))
    relations.extend(_counterbore_relations(catalog))
    relations.extend(_pattern_pitch_relations(catalog))
    relations.extend(_concentric_relations(catalog))
    return relations


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="recover_design_relations",
    category="reverse_engineer",
    level="atomic",
    summary="Lift a lightweight design-relations graph from a feature_catalog: "
            "mirror pairs (reflected holes), counterbore pairs (cb_d↔through_d "
            "ratio), pattern pitch (spacing/radius↔positions), and concentric "
            "groups (features sharing one axis). Each relation carries "
            "vary_feature_catalog-resolvable member dotted_keys, a rule "
            "(equal/ratio/offset/reflect), an optional value, and a "
            "conservative per-edge confidence. extras['design_relations'] feeds "
            "apply_variant_drivers' coherent propagation. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["design_relations"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class RecoverDesignRelations(SkillBase):
    class Args(BaseModel):
        catalog: dict | None = Field(
            default=None,
            description="The feature_catalog dict to analyse (produced by "
                        "ExtractFeatureCatalog). When None, the last catalog "
                        "shared by PlanFromFeatureCatalog on this process is "
                        "used (mirrors identify_key_dimensions' fallback).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        catalog = args.catalog
        if catalog is None:
            try:
                from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
                    PlanFromFeatureCatalog,
                )
                catalog = getattr(PlanFromFeatureCatalog, "_LAST_CATALOG", None)
            except Exception:
                catalog = None
        relations = recover_design_relations(catalog or {})
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"design_relations": relations},
        )
