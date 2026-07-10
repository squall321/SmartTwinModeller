"""kinematic_sweep — atomic, read-only serial drive of a TREE mate graph.

Drives ONE revolute (degrees) or slider (mm) mate recorded by ``mate_tag``
through ``[start, end]`` in ``n_samples`` evenly spaced poses. The assembly's
CURRENT geometry is the value=0 reference pose. At each sample the whole
subtree on the child side of the driven mate (``between[0]``'s side after
removing that edge from the tree) is posed rigidly by the joint transform —
serial drive, no solver — and checked for interference against every static
component (reuses ``interference_check``'s BRepAlgoAPI_Common machinery and
``check_clearance_full``'s BRepExtrema distance).

Contact semantics (honest): contact := pairwise common volume > 1e-3 mm³ (the
same 1 µL tolerance as interference_check). An exact face-on-face touch has
zero common volume and is NOT counted as contact — pinned by the slider test.

FIRST CONTACT is reported at sample resolution ``|end-start|/(n_samples-1)``:
the found value brackets the true contact in ``(previous_clear_value, value]``.
No sub-sample refinement is performed (the resolution is reported, never
faked away).

Cross pairs only: moving-vs-static pairs are the only ones whose relative pose
changes during a single-joint drive (moving-vs-moving and static-vs-static are
rigid), so only those are checked.

extras schema (strict-JSON-safe):
    driven_mate {index, kind, between, frame}, units ("deg"|"mm"),
    moving_components, static_components, resolution, contact_tol_mm3,
    samples: [{value, contact, overlap_volume_mm3, min_clearance_mm,
               worst_pair}],
    first_contact: {value, index, previous_clear_value, resolution} | None

Refusals (raw, each pinned): fm.no_components, fm.no_mates, fm.mate_not_found,
fm.mate_not_drivable, fm.zero_axis, fm.empty_range, fm.closed_loop (cycle —
tree graphs only, roadmap ruling), fm.incomplete_mate,
fm.unsupported_mate_kind, fm.component_not_found, fm.mate_anchor_stale (the
driven mate's persistence anchors no longer resolve cleanly — e.g. a component
was moved > 5 mm after mate_tag, so the recorded WORLD frame is stale).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.assembly._compound import (
    apply_transform_shape,
    list_components,
)
from phone_designer.skills.assembly._mate_persistence import resolve_mate
from phone_designer.skills.assembly.check_clearance_full import (
    _min_distance_between_shapes,
)
from phone_designer.skills.assembly.interference_check import _common_volume
from phone_designer.skills.assembly.mate_tag import (
    check_tree_mate_graph,
    list_kinematic_mates,
)

_CONTACT_TOL_MM3 = 1e-3   # 1 µL — same gate as interference_check


def _joint_trsf(kind: str, origin, axis, value: float):
    """gp_Trsf for one joint pose. revolute: value in DEGREES about the world
    axis line (origin, axis); slider: value in mm along the unit axis."""
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    trsf = gp_Trsf()
    if kind == "revolute":
        ax1 = gp_Ax1(
            gp_Pnt(origin[0], origin[1], origin[2]),
            gp_Dir(axis[0], axis[1], axis[2]),
        )
        trsf.SetRotation(ax1, math.radians(value))
    elif kind == "slider":
        trsf.SetTranslation(
            gp_Vec(axis[0] * value, axis[1] * value, axis[2] * value)
        )
    else:  # guarded earlier — defensive
        raise RuntimeError(f"kinematic_sweep: fm.mate_not_drivable — kind {kind!r}")
    return trsf


@skill(
    name="kinematic_sweep",
    category="assembly",
    level="atomic",
    summary="Drive one revolute (deg) or slider (mm) mate through a sampled range, "
            "pose the downstream subtree rigidly, and report the first-contact "
            "value plus a clearance/overlap-vs-value table (tree mate graphs only).",
    selector_kinds=[],
    history_rules={},
    produces_features=["kinematic_sweep_report"],
    preserves=["assembly_topology", "body_topology"],
    manufacturing={},
    failure_modes=[
        "fm.no_components",
        "fm.no_mates",
        "fm.mate_not_found",
        "fm.mate_not_drivable",
        "fm.zero_axis",
        "fm.empty_range",
        "fm.closed_loop",
        "fm.incomplete_mate",
        "fm.unsupported_mate_kind",
        "fm.component_not_found",
        "fm.mate_anchor_stale",
    ],
    cost_hint=0.6,
    post_conditions=[PostCondition(kind="body_present")],
)
class KinematicSweep(SkillBase):
    class Args(BaseModel):
        mate_index: int = Field(ge=0, description="Index of the mate_tag mate to drive.")
        start: float = Field(description="Sweep start (deg for revolute, mm for slider).")
        end: float = Field(description="Sweep end (deg for revolute, mm for slider).")
        n_samples: int = Field(
            default=16, ge=2, le=721,
            description="Evenly spaced samples over [start, end], endpoints included.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise RuntimeError("kinematic_sweep: fm.no_components — body is None")
        components = list_components(body)
        if len(components) < 2:
            raise RuntimeError(
                "kinematic_sweep: fm.no_components — needs an assembly compound "
                f"with >= 2 components (got {len(components)})"
            )
        names = [n for n, _ in components]
        shapes = {n: s for n, s in components}

        if abs(args.end - args.start) < 1e-12:
            raise RuntimeError(
                f"kinematic_sweep: fm.empty_range — start ({args.start}) and "
                f"end ({args.end}) must differ"
            )

        mates = list_kinematic_mates(body)
        if not mates:
            raise RuntimeError(
                "kinematic_sweep: fm.no_mates — no mate_tag records on this "
                "assembly (record one first)"
            )
        # refuses fm.incomplete_mate / fm.unsupported_mate_kind /
        # fm.component_not_found / fm.closed_loop
        adjacency = check_tree_mate_graph(mates, names, "kinematic_sweep")

        by_index = {m["index"]: m for m in mates}
        if args.mate_index not in by_index:
            raise RuntimeError(
                f"kinematic_sweep: fm.mate_not_found — no mate with index "
                f"{args.mate_index} (known: {sorted(by_index)})"
            )
        driven = by_index[args.mate_index]
        if driven["kind"] == "fixed":
            raise RuntimeError(
                f"kinematic_sweep: fm.mate_not_drivable — mate "
                f"#{args.mate_index} is 'fixed' (0 DOF); drive a revolute or "
                f"slider mate"
            )
        origin = driven["frame"]["origin"]
        axis = driven["frame"]["axis"]
        norm = math.sqrt(sum(v * v for v in axis))
        if norm < 1e-9:
            raise RuntimeError(
                f"kinematic_sweep: fm.zero_axis — mate #{args.mate_index} has a "
                f"zero frame axis {axis}"
            )
        axis = [v / norm for v in axis]

        # Persistence-anchor freshness guard: the recorded frame is WORLD-frozen
        # at mate_tag time; if the anchor faces no longer resolve cleanly the
        # frame cannot be trusted (component moved > 5 mm / collapsed refs).
        res = resolve_mate(body, args.mate_index)
        if not res["ok"]:
            statuses = {s: res["sides"][s]["status"] for s in ("a", "b")}
            raise RuntimeError(
                f"kinematic_sweep: fm.mate_anchor_stale — mate "
                f"#{args.mate_index} anchors did not resolve cleanly "
                f"({statuses}); the recorded world frame is stale. Re-record "
                f"the mate with mate_tag at the current pose."
            )

        # Split the tree at the driven edge: child side moves rigidly.
        child, parent = driven["between"]
        moving: set[str] = set()
        stack = [child]
        while stack:
            cur = stack.pop()
            if cur in moving:
                continue
            moving.add(cur)
            for other, mate_idx, _freedom in adjacency[cur]:
                if mate_idx == args.mate_index:
                    continue
                if other not in moving:
                    stack.append(other)
        if parent in moving:  # tree check makes this unreachable — defensive
            raise RuntimeError(
                "kinematic_sweep: internal error — parent side reachable from "
                "child side without the driven mate (graph is not a tree)"
            )
        moving_names = [n for n in names if n in moving]
        static_names = [n for n in names if n not in moving]

        step = (args.end - args.start) / (args.n_samples - 1)
        resolution = abs(step)
        units = "deg" if driven["kind"] == "revolute" else "mm"

        samples: list[dict[str, Any]] = []
        first_contact: dict[str, Any] | None = None
        for i in range(args.n_samples):
            value = args.start + step * i
            trsf = _joint_trsf(driven["kind"], origin, axis, value)
            moved = {n: apply_transform_shape(shapes[n], trsf) for n in moving_names}

            total_overlap = 0.0
            worst_pair: list[str] | None = None
            worst_vol = 0.0
            for m in moving_names:
                for s in static_names:
                    vol = _common_volume(moved[m], shapes[s])
                    total_overlap += vol
                    if vol > worst_vol:
                        worst_vol = vol
                        worst_pair = [m, s]
            contact = total_overlap > _CONTACT_TOL_MM3

            if contact:
                min_clearance: float | None = 0.0
            else:
                dmin = float("inf")
                for m in moving_names:
                    for s in static_names:
                        d, _pa, _pb = _min_distance_between_shapes(
                            moved[m], shapes[s]
                        )
                        dmin = min(dmin, d)
                # inf (extrema failure) is not strict-JSON-safe → honest None
                min_clearance = round(dmin, 6) if math.isfinite(dmin) else None

            samples.append({
                "value": round(value, 9),
                "contact": contact,
                "overlap_volume_mm3": round(total_overlap, 6),
                "min_clearance_mm": min_clearance,
                "worst_pair": worst_pair if worst_vol > _CONTACT_TOL_MM3 else None,
            })
            if contact and first_contact is None:
                first_contact = {
                    "value": round(value, 9),
                    "index": i,
                    "previous_clear_value": (
                        round(args.start + step * (i - 1), 9) if i > 0 else None
                    ),
                    "resolution": round(resolution, 9),
                }

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "driven_mate": {
                    "index": driven["index"],
                    "kind": driven["kind"],
                    "between": list(driven["between"]),
                    "frame": {"origin": list(origin), "axis": list(axis)},
                },
                "units": units,
                "moving_components": moving_names,
                "static_components": static_names,
                "resolution": round(resolution, 9),
                "contact_tol_mm3": _CONTACT_TOL_MM3,
                "samples": samples,
                "first_contact": first_contact,
            },
        )
