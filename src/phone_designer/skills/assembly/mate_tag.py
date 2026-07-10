"""mate_tag — atomic assembly. Attach a persistent KINEMATIC mate onto a compound.

A mate is ``{kind: revolute|slider|fixed, frame: origin+axis, between: [a, b]}``
where ``between[0]`` is the CHILD (the side that moves when the joint is driven)
and ``between[1]`` is the PARENT/reference. Consumed by ``assembly_dof``
(bookkeeping) and ``kinematic_sweep`` (serial drive + first contact).

Persistence — REUSES the spike-proven attach mechanism, does not invent one
------------------------------------------------------------------------------
``skills/assembly/_mate_persistence.py`` (pinned by
``tests/test_mate_persistence_spike.py``) proved that the ONLY body-level store
surviving every compound rebuild is the face-tag store ``body._pd_tags``, and
that the tag NAME is the only rebuild-immutable slot (refs are re-anchored by
bbox-center nearest-match at each rebuild; the recorded-area guard converts
silent mis-anchors into explicit statuses). This skill therefore encodes the
kinematic payload INTO the ``mate_type`` field of that codec:

    "revolute@ox,oy,oz@ax,ay,az"        (floats via repr — never contains ':')

and calls ``record_mate`` / ``list_mates`` / ``resolve_mate`` verbatim. One
anchor FACE per side (``face_selector_a`` on ``between[0]``, ``face_selector_b``
on ``between[1]``) carries the record through rebuilds.

STEP round-trip (honest mechanics)
----------------------------------
``body._pd_tags`` is a Python attribute — it does NOT ride inside the STEP
file itself. ``serialize_mate_tags`` / ``restore_mate_tags`` provide the
strict-JSON-safe sidecar; after reimport the refs re-resolve GEOMETRICALLY
(bbox-center nearest match + recorded-area guard), so a mate survives
export → reimport when the sidecar is reattached. Pinned in
``tests/skills/test_assembly_kinematics.py``.

Honest limits (inherited from the spike — documented, never masked)
-------------------------------------------------------------------
* ``frame.origin``/``frame.axis`` are WORLD coordinates frozen at record time.
  A later ``move_component`` of a mated part makes the frame stale: moves
  > 5 mm are caught downstream (resolve_mate → area_mismatch/tag_missing →
  ``kinematic_sweep`` refuses ``fm.mate_anchor_stale``); sub-5 mm moves are
  NOT detectable by the guard.
* Anchors are faces only (the tag store has no edge refs).
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, EntityRef
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.assembly._mate_persistence import (
    list_mates,
    parse_mate_tag,
    record_mate,
)

KINEMATIC_KINDS = ("revolute", "slider", "fixed")

# DOF each joint kind LEAVES free between its two components (rigid spatial
# body pair = 6). fixed constrains all 6; revolute leaves 1 rotation; slider
# leaves 1 translation.
KIND_FREEDOM_DOF: dict[str, int] = {"revolute": 1, "slider": 1, "fixed": 0}


# ---------------------------------------------------------------------------
# kind+frame codec — rides the mate_type field of the _mate_persistence tag
# name (the only rebuild-immutable slot). May not contain ':' (codec guard).
# ---------------------------------------------------------------------------

def encode_kind_frame(
    kind: str,
    origin: tuple[float, float, float],
    axis: tuple[float, float, float],
) -> str:
    """(kind, origin, axis) → 'kind@ox,oy,oz@ax,ay,az' (repr floats, exact)."""
    if kind not in KINEMATIC_KINDS:
        raise ValueError(f"mate_tag: unknown kinematic kind {kind!r}")
    vals = [float(v) for v in (*origin, *axis)]
    for v in vals:
        if not math.isfinite(v):
            raise ValueError(f"mate_tag: non-finite frame value {v!r}")
    o = ",".join(repr(v) for v in vals[:3])
    a = ",".join(repr(v) for v in vals[3:])
    return f"{kind}@{o}@{a}"


def decode_kind_frame(
    mate_type: str,
) -> tuple[str, tuple[float, float, float], tuple[float, float, float]] | None:
    """Encoded mate_type → (kind, origin, axis).

    Returns None for a NON-kinematic mate type (no '@' — e.g. a plain
    'concentric' recorded directly via record_mate). A type that carries '@'
    but does not parse is a corruption — raise, never mask.
    """
    if "@" not in mate_type:
        return None
    parts = mate_type.split("@")
    if len(parts) != 3 or parts[0] not in KINEMATIC_KINDS:
        raise ValueError(f"mate_tag: malformed kinematic mate type {mate_type!r}")
    try:
        origin = tuple(float(x) for x in parts[1].split(","))
        axis = tuple(float(x) for x in parts[2].split(","))
    except ValueError as exc:
        raise ValueError(
            f"mate_tag: malformed kinematic mate type {mate_type!r}"
        ) from exc
    if len(origin) != 3 or len(axis) != 3:
        raise ValueError(f"mate_tag: malformed kinematic mate type {mate_type!r}")
    return parts[0], origin, axis


# ---------------------------------------------------------------------------
# decoded views + graph helpers shared by assembly_dof / kinematic_sweep
# ---------------------------------------------------------------------------

def list_kinematic_mates(body) -> list[dict[str, Any]]:
    """All persisted mates, decoded. Strict-JSON-safe.

    Non-kinematic mates (recorded outside this skill) appear with kind=None —
    honest, so consumers can refuse instead of guessing DOF for them. A side
    dropped by tag propagation yields complete=False (never invented).
    """
    out: list[dict[str, Any]] = []
    for rec in list_mates(body):
        decoded = decode_kind_frame(rec["type"]) if rec["type"] else None
        out.append({
            "index": rec["index"],
            "kind": decoded[0] if decoded else None,
            "frame": (
                {
                    "origin": [float(v) for v in decoded[1]],
                    "axis": [float(v) for v in decoded[2]],
                }
                if decoded else None
            ),
            "between": [rec["component_a"], rec["component_b"]],
            "complete": bool(rec["complete"]),
            "raw_type": rec["type"],
            "sides": rec["sides"],
        })
    return out


def check_tree_mate_graph(
    mates: list[dict[str, Any]],
    component_names: list[str],
    caller: str,
) -> dict[str, list[tuple[str, int, int]]]:
    """Validate the mate graph is a kinematic FOREST and return its adjacency.

    adjacency: component → [(other_component, mate_index, freedom_dof)].

    Structured refusals (each reachable in tests, raw RuntimeError):
        fm.incomplete_mate        — a side's tag was dropped by propagation
        fm.unsupported_mate_kind  — a persisted mate is not revolute/slider/fixed
        fm.component_not_found    — a mate references a component not in the compound
        fm.closed_loop            — graph cycle (e.g. 4-bar linkage). Closed-loop
                                    kinematics (position solving) is out of scope
                                    by roadmap ruling; TREE graphs only.
    """
    parent: dict[str, str] = {n: n for n in component_names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    adjacency: dict[str, list[tuple[str, int, int]]] = {
        n: [] for n in component_names
    }
    for m in mates:
        idx = m["index"]
        if not m["complete"]:
            missing = [s for s in ("a", "b") if m["sides"].get(s) is None]
            raise RuntimeError(
                f"{caller}: fm.incomplete_mate — mate #{idx} lost side(s) "
                f"{missing} during a compound rebuild (tag dropped by "
                f"propagation); re-record the mate with mate_tag"
            )
        if m["kind"] is None:
            raise RuntimeError(
                f"{caller}: fm.unsupported_mate_kind — mate #{idx} has "
                f"non-kinematic type {m['raw_type']!r}; DOF accounting supports "
                f"only {KINEMATIC_KINDS} recorded via mate_tag"
            )
        a, b = m["between"]
        for comp in (a, b):
            if comp not in adjacency:
                raise RuntimeError(
                    f"{caller}: fm.component_not_found — mate #{idx} references "
                    f"component '{comp}' which is not in the compound "
                    f"(known: {sorted(component_names)})"
                )
        ra, rb = find(a), find(b)
        if ra == rb:
            raise RuntimeError(
                f"{caller}: fm.closed_loop — mate #{idx} ({m['kind']}) between "
                f"'{a}' and '{b}' closes a cycle (they are already connected "
                f"through other mates, e.g. a 4-bar linkage). Closed-loop "
                f"kinematics is out of scope (roadmap ruling): DOF bookkeeping "
                f"and kinematic_sweep support TREE mate graphs only."
            )
        parent[ra] = rb
        freedom = KIND_FREEDOM_DOF[m["kind"]]
        adjacency[a].append((b, idx, freedom))
        adjacency[b].append((a, idx, freedom))
    return adjacency


# ---------------------------------------------------------------------------
# STEP round-trip sidecar (tags are a Python attribute, not STEP payload)
# ---------------------------------------------------------------------------

def serialize_mate_tags(body) -> dict[str, Any]:
    """Mate tag store → strict-JSON-safe sidecar (reserved __mate__ tags only)."""
    from phone_designer.skills.compose.tag_face import get_tags

    tags: dict[str, Any] = {}
    for name, refs in get_tags(body).items():
        if parse_mate_tag(name) is None:
            continue
        tags[name] = [
            {
                "bbox_center": [float(c) for c in r.bbox_center],
                "measure": float(r.measure),
            }
            for r in refs
        ]
    return {"schema": "pd_mate_tags_v1", "tags": tags}


def restore_mate_tags(body, payload: dict[str, Any]) -> int:
    """Reattach a serialized mate tag store onto a (re)imported body.

    Existing non-mate tags on the body are preserved. Returns the number of
    restored tag entries. Raises on a foreign schema or a non-mate tag name —
    corruption is never masked.
    """
    from phone_designer.skills.compose.tag_face import get_tags, set_tags

    if payload.get("schema") != "pd_mate_tags_v1":
        raise ValueError(
            f"restore_mate_tags: unknown sidecar schema {payload.get('schema')!r}"
        )
    tags = dict(get_tags(body))
    n = 0
    for name, refs in payload["tags"].items():
        if parse_mate_tag(name) is None:  # raises on corrupt reserved names
            raise ValueError(f"restore_mate_tags: not a mate tag name: {name!r}")
        tags[name] = [
            EntityRef(
                tag=name,
                kind="face",
                bbox_center=tuple(float(c) for c in r["bbox_center"]),
                measure=float(r["measure"]),
            )
            for r in refs
        ]
        n += 1
    set_tags(body, tags)
    return n


# ---------------------------------------------------------------------------
# the skill
# ---------------------------------------------------------------------------

class MateFrame(BaseModel):
    origin: tuple[float, float, float] = Field(
        description="Joint frame origin in WORLD mm (a point on the joint axis)."
    )
    axis: tuple[float, float, float] = Field(
        description="Joint axis direction in WORLD coords (revolute: rotation "
                    "axis; slider: translation direction; fixed: informational)."
    )


@skill(
    name="mate_tag",
    category="assembly",
    level="atomic",
    summary="Attach a persistent kinematic mate (revolute/slider/fixed with a world "
            "origin+axis frame) between two assembly components. Persisted via the "
            "rebuild-surviving face-tag store; consumed by assembly_dof and "
            "kinematic_sweep.",
    selector_kinds=["faces"],
    history_rules={},
    produces_features=["kinematic_mate"],
    preserves=["assembly_topology", "body_topology"],
    manufacturing={},
    failure_modes=[
        "fm.no_components",
        "fm.component_not_found",
        "fm.selector_no_match",
        "fm.same_component",
        "fm.zero_axis",
    ],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class MateTag(SkillBase):
    class Args(BaseModel):
        kind: Literal["revolute", "slider", "fixed"]
        between: tuple[str, str] = Field(
            description="(child, parent) component names — between[0] is the "
                        "side that moves when the joint is driven."
        )
        frame: MateFrame
        face_selector_a: SelectorRef = Field(
            description="Anchor face on between[0] (persistence anchor)."
        )
        face_selector_b: SelectorRef = Field(
            description="Anchor face on between[1] (persistence anchor)."
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.assembly._compound import get_component_names

        if body is None:
            raise RuntimeError("mate_tag: fm.no_components — body is None")

        origin = tuple(float(v) for v in args.frame.origin)
        axis = tuple(float(v) for v in args.frame.axis)
        norm = math.sqrt(sum(v * v for v in axis))
        if args.kind in ("revolute", "slider"):
            if norm < 1e-9:
                raise RuntimeError(
                    f"mate_tag: fm.zero_axis — a {args.kind} mate needs a "
                    f"non-zero frame.axis, got {list(axis)}"
                )
            axis = tuple(v / norm for v in axis)

        child, parent = args.between
        if child == parent:
            raise RuntimeError(
                f"mate_tag: fm.same_component — between must name two different "
                f"components, got ('{child}', '{parent}')"
            )

        encoded = encode_kind_frame(args.kind, origin, axis)
        # record_mate raises raw on unknown component ('not found' →
        # fm.component_not_found), empty selector match ('matched 0 faces' →
        # fm.selector_no_match) and missing assembly components
        # ('no assembly components' → fm.no_components). Never masked.
        rec = record_mate(
            body, encoded, child, parent,
            args.face_selector_a, args.face_selector_b,
        )

        mate = {
            "index": rec["index"],
            "kind": args.kind,
            "frame": {"origin": [*origin], "axis": [*axis]},
            "between": [child, parent],
            "sides": rec["sides"],
            "complete": rec["complete"],
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "mate": mate,
                "mates": list_kinematic_mates(body),
                "component_names": get_component_names(body),
            },
        )
