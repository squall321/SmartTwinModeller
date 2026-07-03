"""feature_tree — Plan-as-feature-tree dependency graph (Track 3-3).

``build_feature_tree(plan, execution_result_or_none)`` derives a parent/child
feature dependency DAG over the plan's steps. Edges always point EARLIER step →
LATER step (a step can only depend on steps that ran before it), so the graph
is acyclic by construction.

Edge derivation, in order of evidence strength (per plan section 3-3):

  evidence='history'        (kind='history') — derived from the EntityHistoryMap
      each skill RETURNED during execution (recorded by the executor in
      ``ExecutionResult.step_histories``). Two sub-rules:
        * entity match — a step whose recorded consumed/inherited EntityRefs
          geometrically match (kind + bbox_center + measure) entities an
          earlier step recorded as ``new_entities``;
        * creator chain — a step whose recorded rules include CONSUMED /
          MODIFIED_INHERIT / SPLIT_BRANCH mutates the body it received, so it
          depends on the nearest preceding CREATE-category step (the step that
          established the base body). This mirrors SolidWorks feature-tree
          semantics: two mutators of the same base are independent unless they
          reference each other.

  evidence='arg_reference'  (kind='arg_reference') — static analysis of step
      args (no execution needed):
        * a step whose selector tree contains {"kind": "tagged", "tag": T} or
          {"kind": "face_named", "name": T} where an earlier step CREATED tag T
          (top-level ``tag`` arg, or a recorded EntityRef carrying that tag);
        * positional overlap — an earlier step's ``position`` arg lies inside
          this step's ``edges_by_position`` selector bbox (or vice versa).

  evidence='unknown'        (kind='sequence') — HONEST degradation. Used when a
      step's skill returned an EMPTY EntityHistoryMap (or the step never ran /
      no execution result was given): we cannot know its true dependency, so a
      conservative sequence edge from the immediately preceding step is added.
      Per the plan, an edge kind is NEVER fabricated — 'sequence' edges say
      "order is all we know".

HONESTY LIMIT (documented, not hidden): most hot-path skills record history
RULES but not per-entity lists, so independence between two mutating steps
(e.g. a hole and a later fillet) is the CAD-conventional heuristic above, not
an entity-level proof. The executor's selector-freeze verification during
rebuild is the runtime guard: a suppressed dependency the tree missed surfaces
as a FreezeMismatch / zero-delta skip in the rebuild report — never silently.
"""
from __future__ import annotations

from typing import Any

from phone_designer.plan.model import Plan, Step, StepStatus

#: evidence strength ranking (higher wins on duplicate edges)
_EVIDENCE_RANK = {"history": 2, "arg_reference": 1, "unknown": 0}

#: edge kind per evidence — 'sequence' is the honest fallback kind
_EVIDENCE_KIND = {
    "history": "history",
    "arg_reference": "arg_reference",
    "unknown": "sequence",
}

#: HistoryRule values (dict form) that mean "this step MUTATED input entities"
_MUTATING_RULES = {"consumed", "modified_inherit", "split_branch"}

#: geometric match tolerance for EntityRef bbox_center / measure comparison —
#: EntityRefs are recorded rounded to 3 decimals, so 1.5e-3 absorbs the
#: rounding of both sides without ever matching a different entity.
_MATCH_TOL = 1.5e-3


# ---------------------------------------------------------------------------
# registry / history access helpers
# ---------------------------------------------------------------------------

def _skill_category(skill_name: str) -> str:
    """Registry category for a skill name; '' when unknown (never raises)."""
    try:
        from phone_designer.skills._registry import registry
        return registry.get(skill_name).category or ""
    except Exception:
        return ""


def _is_creator(step: Step) -> bool:
    """True iff the step's skill IGNORES its input body and creates a new one
    (registry category 'create' / 'create/*'). Static registry metadata —
    safe to use in both executed and static modes."""
    cat = _skill_category(step.skill)
    return cat == "create" or cat.startswith("create")


def _history_dict_for(execution_result: Any, step_id: str) -> dict[str, Any] | None:
    """Recorded EntityHistoryMap (dict form) for a step, or None.

    Primary source: ``ExecutionResult.step_histories`` (recorded by the
    executor per PASS step). Fallback: ``step_results[id].history.to_dict()``
    for callers holding an ExecutionResult built before step_histories existed.
    """
    if execution_result is None:
        return None
    step_histories = getattr(execution_result, "step_histories", None) or {}
    if step_id in step_histories:
        return step_histories[step_id]
    step_results = getattr(execution_result, "step_results", None) or {}
    skill_result = step_results.get(step_id)
    hist = getattr(skill_result, "history", None)
    if hist is None:
        return None
    try:
        return hist.to_dict()
    except Exception:
        return None


def _history_is_empty(hist: dict[str, Any] | None) -> bool:
    if not hist:
        return True
    return not (
        hist.get("rules")
        or hist.get("children")
        or hist.get("new_entities")
        or hist.get("consumed")
        or hist.get("inherited")
    )


# ---------------------------------------------------------------------------
# static arg analysis helpers
# ---------------------------------------------------------------------------

def _walk(node: Any):
    """Yield every dict node nested anywhere inside args."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _walk(v)


def _tags_created(step: Step, hist: dict[str, Any] | None) -> set[str]:
    """Tags a step CREATES: its top-level ``tag`` arg (tag_face pattern) plus
    any tag carried by an EntityRef it recorded (new_entities / inherited)."""
    tags: set[str] = set()
    args = step.args or {}
    top = args.get("tag")
    if isinstance(top, str) and top:
        tags.add(top)
    for key in ("new_entities", "inherited"):
        for ref in (hist or {}).get(key) or []:
            if isinstance(ref, dict):
                t = ref.get("tag")
                if isinstance(t, str) and t:
                    tags.add(t)
    return tags


def _tags_used(step: Step) -> set[str]:
    """Tag names referenced by selectors nested anywhere in the step args."""
    used: set[str] = set()
    for node in _walk(step.args or {}):
        kind = node.get("kind")
        if kind == "tagged" and isinstance(node.get("tag"), str):
            used.add(node["tag"])
        elif kind == "face_named" and isinstance(node.get("name"), str):
            used.add(node["name"])
    return used


def _positions_of(step: Step) -> list[tuple[float, float, float]]:
    """Every 3-component numeric ``position`` value nested in the args."""
    out: list[tuple[float, float, float]] = []
    for node in _walk(step.args or {}):
        p = node.get("position")
        if (
            isinstance(p, (list, tuple)) and len(p) == 3
            and all(isinstance(c, (int, float)) for c in p)
        ):
            out.append((float(p[0]), float(p[1]), float(p[2])))
    # top-level args dict is itself a node — covered by _walk(args)
    return out


def _selector_bboxes(step: Step) -> list[tuple[tuple, tuple]]:
    """(min, max) of every ``edges_by_position`` selector nested in the args."""
    out: list[tuple[tuple, tuple]] = []
    for node in _walk(step.args or {}):
        if node.get("kind") == "edges_by_position":
            bb = node.get("bbox")
            if isinstance(bb, dict):
                mn, mx = bb.get("min"), bb.get("max")
                if (
                    isinstance(mn, (list, tuple)) and len(mn) == 3
                    and isinstance(mx, (list, tuple)) and len(mx) == 3
                ):
                    out.append((tuple(float(c) for c in mn),
                                tuple(float(c) for c in mx)))
    return out


def _point_in_bbox(p: tuple, box: tuple[tuple, tuple]) -> bool:
    mn, mx = box
    return all(mn[i] <= p[i] <= mx[i] for i in range(3))


# ---------------------------------------------------------------------------
# entity matching (history evidence)
# ---------------------------------------------------------------------------

def _refs_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two EntityRef dicts refer to the same entity (kind + center + measure)."""
    if a.get("kind") != b.get("kind"):
        return False
    ca, cb = a.get("bbox_center"), b.get("bbox_center")
    if not (isinstance(ca, (list, tuple)) and isinstance(cb, (list, tuple))
            and len(ca) == 3 and len(cb) == 3):
        return False
    if any(abs(float(ca[i]) - float(cb[i])) > _MATCH_TOL for i in range(3)):
        return False
    ma, mb = a.get("measure"), b.get("measure")
    if isinstance(ma, (int, float)) and isinstance(mb, (int, float)):
        return abs(float(ma) - float(mb)) <= max(_MATCH_TOL, 1e-6 * abs(float(ma)))
    return True


def _input_refs(hist: dict[str, Any] | None) -> list[dict[str, Any]]:
    """EntityRefs a step OPERATED ON (consumed + inherited)."""
    refs: list[dict[str, Any]] = []
    for key in ("consumed", "inherited"):
        for ref in (hist or {}).get(key) or []:
            if isinstance(ref, dict):
                refs.append(ref)
    return refs


def _producer_refs(hist: dict[str, Any] | None) -> list[dict[str, Any]]:
    """EntityRefs a step PRODUCED (new_entities)."""
    return [r for r in ((hist or {}).get("new_entities") or [])
            if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# build_feature_tree
# ---------------------------------------------------------------------------

def build_feature_tree(
    plan: Plan,
    execution_result: Any = None,
) -> dict[str, dict[str, Any]]:
    """Dependency graph over ``plan.steps``.

    Args:
        plan: the Plan (executed or not).
        execution_result: the ``ExecutionResult`` of running this plan, or
            None. With a result, 'history' evidence is available; without one
            the tree degrades HONESTLY to arg_reference + sequence/'unknown'
            edges (never fabricated 'history' claims).

    Returns (strict-JSON-safe)::

        {step_id: {
            "index": int, "skill": str,
            "parents":  [{"step_id", "evidence", "kind"}, ...],   # by index
            "children": [{"step_id", "evidence", "kind"}, ...],
            "evidence": "history"|"arg_reference"|"unknown"|None,  # None=root
            "history_recorded": bool | None,   # None = no execution result;
                                               # False = ran but EMPTY history
                                               #         (or never ran)
        }}
    """
    steps = list(plan.steps)
    ids = [s.id for s in steps]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(
            f"fm.feature_tree_duplicate_step_ids: plan '{plan.plan_name}' has "
            f"duplicate step ids {dupes} — a feature tree keyed by step_id "
            f"cannot represent it")

    executed = execution_result is not None
    hists = [
        _history_dict_for(execution_result, s.id) if executed else None
        for s in steps
    ]

    created_tags = [_tags_created(s, hists[i]) for i, s in enumerate(steps)]
    used_tags = [_tags_used(s) for s in steps]
    positions = [_positions_of(s) for s in steps]
    sel_bboxes = [_selector_bboxes(s) for s in steps]
    producers = [_producer_refs(hists[i]) for i in range(len(steps))]

    # parent edges: child index -> {parent index: evidence}
    parents: list[dict[int, str]] = [dict() for _ in steps]

    def add_edge(child: int, parent: int, evidence: str) -> None:
        if parent == child or parent < 0:
            return
        prev = parents[child].get(parent)
        if prev is None or _EVIDENCE_RANK[evidence] > _EVIDENCE_RANK[prev]:
            parents[child][parent] = evidence

    for i, step in enumerate(steps):
        # ---- (b) arg_reference: tag creation -> tag use -------------------
        for tag in used_tags[i]:
            for j in range(i - 1, -1, -1):
                if tag in created_tags[j]:
                    add_edge(i, j, "arg_reference")
                    break
        # ---- (b) arg_reference: positional overlap ------------------------
        for j in range(i):
            if any(_point_in_bbox(p, bb)
                   for bb in sel_bboxes[i] for p in positions[j]):
                add_edge(i, j, "arg_reference")
            if any(_point_in_bbox(p, bb)
                   for bb in sel_bboxes[j] for p in positions[i]):
                add_edge(i, j, "arg_reference")

        # ---- (a)/(c) history / honest fallback ----------------------------
        if executed:
            hist = hists[i]
            if _history_is_empty(hist):
                # (c) EMPTY history — order is all we know. Never fabricate.
                add_edge(i, i - 1, "unknown")
                continue
            # entity-level match: my consumed/inherited refs vs earlier
            # steps' recorded new_entities (latest producer wins).
            unmatched = 0
            for ref in _input_refs(hist):
                found = None
                for j in range(i - 1, -1, -1):
                    if any(_refs_match(ref, prod) for prod in producers[j]):
                        found = j
                        break
                if found is not None:
                    add_edge(i, found, "history")
                else:
                    unmatched += 1
            if unmatched:
                # refs produced by an earlier step that recorded no entity
                # list — conservative sequence edge (honest degradation).
                add_edge(i, i - 1, "unknown")
            # creator chain: a mutating step depends on the base-body creator.
            rules = {
                str(getattr(v, "value", v))
                for v in ((hist.get("rules") or {}).values())
            }
            mutates = bool(_MUTATING_RULES & rules)
            if _is_creator(step) and not mutates:
                pass  # create skills ignore the input body — root
            else:
                creator = None
                for j in range(i - 1, -1, -1):
                    if _is_creator(steps[j]):
                        creator = j
                        break
                if creator is not None:
                    add_edge(i, creator, "history")
                elif not parents[i]:
                    add_edge(i, i - 1, "unknown")
        else:
            # static mode — no execution: create steps are roots (registry
            # metadata), everything else gets the honest sequence edge.
            if not _is_creator(step):
                add_edge(i, i - 1, "unknown")

    # ---- assemble JSON-safe node dicts -------------------------------------
    tree: dict[str, dict[str, Any]] = {}
    children: list[list[dict[str, Any]]] = [[] for _ in steps]
    for i, step in enumerate(steps):
        for j in sorted(parents[i]):
            children[j].append({
                "step_id": step.id,
                "evidence": parents[i][j],
                "kind": _EVIDENCE_KIND[parents[i][j]],
            })
    for i, step in enumerate(steps):
        parent_edges = [
            {"step_id": steps[j].id, "evidence": ev, "kind": _EVIDENCE_KIND[ev]}
            for j, ev in sorted(parents[i].items())
        ]
        node_evidence = None
        if parent_edges:
            node_evidence = max(
                (e["evidence"] for e in parent_edges),
                key=lambda ev: _EVIDENCE_RANK[ev],
            )
        tree[step.id] = {
            "index": i,
            "skill": step.skill,
            "parents": parent_edges,
            "children": children[i],
            "evidence": node_evidence,
            "history_recorded": (
                None if not executed else not _history_is_empty(hists[i])
            ),
        }
    return tree


# ---------------------------------------------------------------------------
# suppression closure
# ---------------------------------------------------------------------------

def suppress_closure(
    tree: dict[str, dict[str, Any]],
    step_id: str,
) -> list[dict[str, Any]]:
    """The step plus everything that (transitively) depends on it.

    Returns plan-ordered ``[{"step_id", "via", "evidence"}, ...]`` where
    ``via`` is the in-closure parent whose edge pulled the member in (None for
    the suppression root) and ``evidence`` is that edge's evidence.
    """
    if step_id not in tree:
        raise KeyError(step_id)
    pulled: dict[str, tuple[str | None, str | None]] = {step_id: (None, None)}
    frontier = [step_id]
    while frontier:
        current = frontier.pop()
        for edge in tree[current]["children"]:
            child = edge["step_id"]
            if child not in pulled:
                pulled[child] = (current, edge["evidence"])
                frontier.append(child)
    ordered = sorted(pulled, key=lambda sid: tree[sid]["index"])
    return [
        {"step_id": sid, "via": pulled[sid][0], "evidence": pulled[sid][1]}
        for sid in ordered
    ]


# ---------------------------------------------------------------------------
# incremental rebuild helper
# ---------------------------------------------------------------------------

def run_plan_suffix(
    plan: Plan,
    start_index: int,
    *,
    initial_body: Any = None,
    mode: Any = None,
):
    """Execute ONLY ``plan.steps[start_index:]``, seeded with ``initial_body``
    (the recorded intermediate body after the original run's step
    ``start_index - 1``). This is the GENUINE incremental primitive: steps
    before ``start_index`` are not re-executed here.

    Statuses/freezes land on the SAME Step objects held by ``plan`` (the
    sub-plan shares them), so callers can read results off the full plan.

    Returns the suffix's ``ExecutionResult`` (its ``.plan`` is the sub-plan).
    An empty suffix returns a PASS result whose final_body is ``initial_body``.
    """
    from phone_designer.plan.executor import (
        ExecutionMode,
        ExecutionResult,
        PlanExecutor,
    )

    if mode is None:
        mode = ExecutionMode.STRICT
    if start_index < 0 or start_index > len(plan.steps):
        raise ValueError(
            f"fm.plan_suffix_bad_index: start_index={start_index} outside "
            f"[0, {len(plan.steps)}]")
    sub = Plan(
        plan_name=f"{plan.plan_name}[{start_index}:]",
        steps=plan.steps[start_index:],
        continue_on_step_failure=plan.continue_on_step_failure,
        strict_cuts=plan.strict_cuts,
    )
    if not sub.steps:
        result = ExecutionResult(plan=sub)
        result.final_body = initial_body
        return result
    return PlanExecutor(sub, mode=mode).run(initial_body=initial_body)


def snapshot_body_before(
    execution_result: Any,
    index: int,
) -> tuple[Any, bool]:
    """The recorded intermediate body entering step ``index`` of the EXECUTED
    plan (``execution_result.plan``), i.e. the body AFTER step ``index - 1``.

    Statuses are read off ``execution_result.plan.steps`` — the Step objects
    the executor actually ran (a derived/edited plan's fresh copies stay
    PENDING and must not be consulted).

    Returns ``(body, available)``. ``available`` is False (body None) when any
    step before ``index`` did not PASS or left no recorded result — callers
    must then fall back to a full re-run AND SAY SO (no fake incremental).
    ``index == 0`` returns ``(None, True)``: the honest snapshot before the
    first step is "no body yet".
    """
    if index <= 0:
        return None, True
    if execution_result is None:
        return None, False
    executed_plan = getattr(execution_result, "plan", None)
    if executed_plan is None or index > len(executed_plan.steps):
        return None, False
    step_results = getattr(execution_result, "step_results", None) or {}
    for step in executed_plan.steps[:index]:
        if step.status != StepStatus.PASS or step.id not in step_results:
            return None, False
    prev_id = executed_plan.steps[index - 1].id
    return step_results[prev_id].body, True
