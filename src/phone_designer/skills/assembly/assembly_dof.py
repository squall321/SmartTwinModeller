"""assembly_dof — atomic, read-only DOF BOOKKEEPING for a mated assembly.

NO position solver. Pure accounting over the kinematic mates persisted by
``mate_tag`` (revolute/slider leave 1 DOF, fixed leaves 0; each mate constrains
``6 - freedom`` of the child's 6 rigid-body DOF):

    total_dof = 6 * n_components - Σ constrained          (no ground removed)
    mobility  = 6 * (n_components - 1) - Σ constrained    (ground excluded —
                spatial Grübler/Kutzbach for a tree, exact, no over-constraint
                correction needed because trees have no loops)

Per-component DOF relative to ground = sum of joint freedoms along the unique
tree path from ground (POSITION-DOF semantics: a component behind two serial
revolutes has 2). Components not connected to ground are honest free-floaters:
their island root gets 6 DOF and joint freedoms accumulate inside the island.
NOTE: the per-component path-sums total to mobility only for star-shaped trees
(each joint used by exactly one path); for deeper chains an upstream joint
appears in every downstream component's path-sum — that is not double
counting, it is what "DOF relative to ground" means per component.

TREE-structured mate graphs only. A CLOSED LOOP (e.g. a 4-bar linkage, or two
mates between the same pair) is DETECTED via graph cycle and refused with
``fm.closed_loop`` — the roadmap-mandated honest limit (closed-loop position
solving was explicitly rejected; a spatial Grübler count on a real 4-bar would
also return the paradoxical -2).

extras schema (strict-JSON-safe):
    n_components, component_names, ground, n_mates,
    mates:        [{index, kind, between, freedom_dof, constrained_dof, frame}]
    per_component:[{name, is_ground, connected_to_ground, dof_relative_to_ground,
                    joint_path}]          # joint_path = mate indices from ground
    total_dof, mobility, graph: "tree"
"""
from __future__ import annotations

from collections import deque
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.assembly._compound import list_components
from phone_designer.skills.assembly.mate_tag import (
    KIND_FREEDOM_DOF,
    check_tree_mate_graph,
    list_kinematic_mates,
)


@skill(
    name="assembly_dof",
    category="assembly",
    level="atomic",
    summary="DOF bookkeeping (no solver) for a mated assembly: per-component DOF vs "
            "ground, total DOF and Grübler mobility from the mate_tag records. "
            "Tree mate graphs only — closed loops (4-bar) are detected and refused.",
    selector_kinds=[],
    history_rules={},
    produces_features=["dof_report"],
    preserves=["assembly_topology", "body_topology"],
    manufacturing={},
    failure_modes=[
        "fm.no_components",
        "fm.component_not_found",
        "fm.incomplete_mate",
        "fm.unsupported_mate_kind",
        "fm.closed_loop",
    ],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class AssemblyDof(SkillBase):
    class Args(BaseModel):
        ground: str | None = Field(
            default=None,
            description="Component treated as fixed reference. Default: the "
                        "first component of the compound.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise RuntimeError("assembly_dof: fm.no_components — body is None")
        components = list_components(body)
        if not components:
            raise RuntimeError(
                "assembly_dof: fm.no_components — body has no assembly "
                "components (_pd_component_names missing or compound empty)"
            )
        names = [n for n, _ in components]

        ground = args.ground if args.ground is not None else names[0]
        if ground not in names:
            raise RuntimeError(
                f"assembly_dof: fm.component_not_found — ground '{ground}' is "
                f"not a component (known: {sorted(names)})"
            )

        mates = list_kinematic_mates(body)
        # refuses fm.incomplete_mate / fm.unsupported_mate_kind /
        # fm.component_not_found / fm.closed_loop — see mate_tag.check_tree_mate_graph
        adjacency = check_tree_mate_graph(mates, names, "assembly_dof")

        constrained_total = 0
        mates_out: list[dict[str, Any]] = []
        for m in mates:
            freedom = KIND_FREEDOM_DOF[m["kind"]]
            constrained = 6 - freedom
            constrained_total += constrained
            mates_out.append({
                "index": m["index"],
                "kind": m["kind"],
                "between": list(m["between"]),
                "freedom_dof": freedom,
                "constrained_dof": constrained,
                "frame": m["frame"],
            })

        n = len(names)
        total_dof = 6 * n - constrained_total
        mobility = 6 * (n - 1) - constrained_total

        # BFS islands: ground island rooted at 0 DOF; every other island's
        # root is an honest 6-DOF free floater.
        dof: dict[str, int] = {}
        path: dict[str, list[int]] = {}
        grounded: dict[str, bool] = {}

        def _bfs(root: str, root_dof: int, is_ground_island: bool) -> None:
            dof[root] = root_dof
            path[root] = []
            grounded[root] = is_ground_island
            q = deque([root])
            while q:
                cur = q.popleft()
                for other, mate_idx, freedom in adjacency[cur]:
                    if other in dof:
                        continue
                    dof[other] = dof[cur] + freedom
                    path[other] = path[cur] + [mate_idx]
                    grounded[other] = is_ground_island
                    q.append(other)

        _bfs(ground, 0, True)
        for name in names:
            if name not in dof:
                _bfs(name, 6, False)

        per_component = [
            {
                "name": name,
                "is_ground": name == ground,
                "connected_to_ground": grounded[name],
                "dof_relative_to_ground": dof[name],
                "joint_path": path[name],
            }
            for name in names
        ]

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "n_components": n,
                "component_names": list(names),
                "ground": ground,
                "n_mates": len(mates),
                "mates": mates_out,
                "per_component": per_component,
                "total_dof": total_dof,
                "mobility": mobility,
                "graph": "tree",
            },
        )
