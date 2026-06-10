"""topology_health — atomic, read-only.

First BREP-level validity gate. Where the geometric inspectors (gdt_*,
classify_*) assume a sane shape, this skill asks OCCT directly whether the
topology itself is trustworthy — BEFORE expensive downstream analysis runs
on a corrupt import.

Checks performed
----------------
1. ``BRepCheck_Analyzer`` verdict (``is_valid``). When invalid, the analyzer
   Result of the shape + every unique subshape is walked and each
   ``BRepCheck_Status`` (excluding ``BRepCheck_NoError``) is translated to
   its enum name and counted. If the per-subshape enumeration throws in this
   binding, ``statuses`` honestly reports
   ``{"invalid": True, "detail": "enumeration unavailable"}`` instead of a
   fabricated breakdown.
2. Free-boundary census:
     - ``ShapeAnalysis_FreeBounds`` → closed + open free-boundary wire
       counts (a missing box face shows up as ONE closed 4-edge loop, so
       both counts are reported).
     - Raw edge→face incidence via ``TopExp::MapShapesAndAncestors``
       (non-unique): an edge with exactly 1 distinct parent face is a
       boundary (free) edge; more than 2 distinct parent faces is a
       non-manifold edge. Seam edges — an edge appearing twice in the SAME
       face (cylinder/cone/torus closure) — are detected both by the
       duplicate-ancestor signature and by ``BRep_Tool::IsClosed(edge, face)``
       and are excluded from the free-edge count. Degenerated edges (sphere
       poles) are likewise excluded: they have a single parent face but are
       a legitimate BREP construct, not an open boundary.
3. Sliver census: faces whose ``BRepGProp::SurfaceProperties`` area falls
   below ``sliver_face_area_mm2`` and edges whose
   ``BRepGProp::LinearProperties`` length falls below
   ``sliver_edge_length_mm`` (degenerated edges excluded — they always
   measure 0).
4. Optional self-intersection (``check_self_intersection=True``, off by
   default — ``BOPAlgo_CheckerSI`` is the single most expensive probe here):
   the shape is fed as the single argument and the interfering shape-pair
   count is read from ``DS().Interferences()``. Note OCCT only raises
   ``HasErrors()`` on algorithm failure, NOT on found interferences — the
   pair count is the verdict.

Face-count guard
----------------
Same budget discipline as ``classify_pockets``: bodies with more than
``max_face_count`` faces (default ``DEFAULT_MAX_FACE_COUNT`` from
``_face_count_guard``, env-overridable via ``PHONE_DESIGNER_MAX_FACE_COUNT``)
return a guarded PARTIAL report (``guarded=True``, analysis fields ``None``)
instead of hanging in the O(N) per-face/per-edge probes.

extras schema:
    {"topology_health": {
        "is_valid": bool | None,           # None only when guarded
        "statuses": dict,                  # status name → count; {} when valid
        "free_edge_count": int | None,
        "open_wire_count": int | None,
        "closed_wire_count": int | None,
        "non_manifold_edge_count": int | None,
        "sliver_faces": [{"face_index": int, "area_mm2": float}, ...] | None,
        "sliver_edges": [{"edge_index": int, "length_mm": float}, ...] | None,
        "self_intersecting": bool | None,  # None when check off / failed
        "self_intersection_pair_count": int | None,
        "guarded": bool,
        "face_count": int,
     }}

body 는 변경하지 않는다 (post ``body_present``).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._face_count_guard import (
    DEFAULT_MAX_FACE_COUNT as _DEFAULT_MAX_FACE_COUNT,
)
from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


# ──────────────────────────────────────────────────────────────────────────────
# (a) BRepCheck_Analyzer + per-subshape status enumeration


def _walk_unique_subshapes(shape) -> list:
    """Shape + every unique subshape (TopoDS_Iterator walk, IsSame-deduped)."""
    from OCP.TopoDS import TopoDS_Iterator
    from OCP.TopTools import TopTools_MapOfShape

    seen = TopTools_MapOfShape()
    out: list = []
    stack = [shape]
    while stack:
        s = stack.pop()
        if seen.Contains(s):
            continue
        seen.Add(s)
        out.append(s)
        it = TopoDS_Iterator(s)
        while it.More():
            stack.append(it.Value())
            it.Next()
    return out


def _enumerate_statuses(analyzer, shape) -> dict[str, Any]:
    """Walk analyzer results for shape + subshapes → {status_name: count}.

    ``BRepCheck_NoError`` entries are dropped. If the binding throws anywhere
    (null Result handle, unexpected container), report
    {"invalid": True, "detail": "enumeration unavailable"} — honest partial
    information beats a fabricated breakdown.
    """
    try:
        counts: dict[str, int] = {}
        for sub in _walk_unique_subshapes(shape):
            res = analyzer.Result(sub)
            if res is None:
                continue
            for st in res.Status():
                name = getattr(st, "name", str(st))
                if name == "BRepCheck_NoError":
                    continue
                counts[name] = counts.get(name, 0) + 1
        return counts
    except Exception:
        return {"invalid": True, "detail": "enumeration unavailable"}


# ──────────────────────────────────────────────────────────────────────────────
# (b) Free / non-manifold edge census


def _count_wires(compound) -> int:
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer

    n = 0
    it = TopExp_Explorer(compound, TopAbs_WIRE)
    while it.More():
        n += 1
        it.Next()
    return n


def _free_bound_wire_counts(shape) -> tuple[int, int]:
    """(closed_wire_count, open_wire_count) of the free boundary."""
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds

    fb = ShapeAnalysis_FreeBounds(shape, False, True)  # same as detect_shell_holes
    return _count_wires(fb.GetClosedWires()), _count_wires(fb.GetOpenWires())


def _edge_incidence_census(shape) -> tuple[int, int]:
    """(free_edge_count, non_manifold_edge_count) from edge→face ancestors.

    Uses the NON-unique ancestor map so a seam edge carries its face twice —
    the duplicate collapses when we count DISTINCT parent faces, and the
    duplication itself marks the edge as a seam (never a free boundary).
    Belt-and-braces: a single-parent edge is also probed with
    ``BRep_Tool::IsClosed(edge, face)`` before being declared free, and
    degenerated edges are skipped outright.
    """
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    edge_faces = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, edge_faces)

    free = 0
    non_manifold = 0
    for i in range(1, edge_faces.Extent() + 1):
        edge = TopoDS.Edge_s(edge_faces.FindKey(i))
        owners = list(edge_faces.FindFromIndex(i))
        if not owners:
            continue  # dangling wireframe edge — not a face boundary

        try:
            if BRep_Tool.Degenerated_s(edge):
                continue  # sphere-pole style construct, not an open boundary
        except Exception:
            pass

        # Distinct parent faces (seam → same face listed twice → collapses).
        distinct: list = []
        for f in owners:
            if not any(f.IsSame(d) for d in distinct):
                distinct.append(f)

        is_seam = len(owners) > len(distinct)
        if not is_seam and len(distinct) == 1:
            try:
                is_seam = bool(
                    BRep_Tool.IsClosed_s(edge, TopoDS.Face_s(distinct[0]))
                )
            except Exception:
                is_seam = False

        if len(distinct) == 1 and not is_seam:
            free += 1
        elif len(distinct) > 2:
            non_manifold += 1
    return free, non_manifold


# ──────────────────────────────────────────────────────────────────────────────
# (c) Sliver census


def _sliver_census(
    faces: list, edges: list, face_area_thr: float, edge_length_thr: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from OCP.BRep import BRep_Tool
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    sliver_faces: list[dict[str, Any]] = []
    for i, f in enumerate(faces):
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, props)
        area = abs(float(props.Mass()))  # invalid faces can integrate negative
        if area < face_area_thr:
            sliver_faces.append({"face_index": i, "area_mm2": round(area, 9)})

    sliver_edges: list[dict[str, Any]] = []
    for i, e in enumerate(edges):
        try:
            if BRep_Tool.Degenerated_s(e):
                continue  # always measures 0 — legitimate, not a sliver
        except Exception:
            pass
        props = GProp_GProps()
        BRepGProp.LinearProperties_s(e, props)
        length = abs(float(props.Mass()))
        if length < edge_length_thr:
            sliver_edges.append({"edge_index": i, "length_mm": round(length, 9)})
    return sliver_faces, sliver_edges


# ──────────────────────────────────────────────────────────────────────────────
# (d) Optional self-intersection


def _self_intersection_check(shape) -> tuple[bool | None, int | None]:
    """(self_intersecting, interfering_pair_count) via BOPAlgo_CheckerSI.

    OCCT only sets HasErrors() on ALGORITHM failure — found interferences
    live in DS().Interferences() (a BOPDS_MapOfPair). pairs > 0 is the
    verdict. Returns (None, None) when the checker itself fails.
    """
    try:
        from OCP.BOPAlgo import BOPAlgo_CheckerSI
        from OCP.TopTools import TopTools_ListOfShape

        checker = BOPAlgo_CheckerSI()
        arguments = TopTools_ListOfShape()
        arguments.Append(shape)
        checker.SetArguments(arguments)
        checker.SetNonDestructive(True)  # read-only skill — never mutate input
        checker.Perform()
        pairs = int(checker.DS().Interferences().Extent())
        return pairs > 0, pairs
    except Exception:
        return None, None


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="topology_health",
    category="inspect",
    level="atomic",
    summary="BREP-level validity gate: BRepCheck_Analyzer verdict with "
            "per-subshape status breakdown, free/open-boundary and "
            "non-manifold edge census (seam + degenerated edges excluded), "
            "sliver face/edge detection, and optional BOPAlgo_CheckerSI "
            "self-intersection probe. Over-budget face counts return a "
            "guarded partial report instead of hanging. Read-only — body "
            "unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["topology_health_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class TopologyHealth(SkillBase):
    class Args(BaseModel):
        check_self_intersection: bool = Field(
            default=False,
            description="Run BOPAlgo_CheckerSI on the shape. Expensive "
                        "(full pairwise interference walk) — off by default. "
                        "When off, self_intersecting is reported as None.",
        )
        sliver_face_area_mm2: float = Field(
            default=1e-4, ge=0.0,
            description="Faces with surface area below this are reported as "
                        "sliver faces (degenerate import artefacts that break "
                        "offset/fillet ops downstream).",
        )
        sliver_edge_length_mm: float = Field(
            default=1e-3, ge=0.0,
            description="Edges with curve length below this are reported as "
                        "sliver edges. Degenerated edges (sphere poles) are "
                        "excluded — they legitimately measure 0.",
        )
        max_face_count: int | None = Field(
            default=_DEFAULT_MAX_FACE_COUNT,
            description="If the body has more than this many faces, skip the "
                        "per-face/per-edge probes and return a guarded "
                        "partial report (guarded=True, analysis fields "
                        "None). Set to None to disable the guard. Default "
                        f"{_DEFAULT_MAX_FACE_COUNT} (see _face_count_guard; "
                        "override via PHONE_DESIGNER_MAX_FACE_COUNT).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepCheck import BRepCheck_Analyzer

        from phone_designer.skills._resolvers import _all_edges, _all_faces

        shape = _occt_shape(body)
        faces = _all_faces(shape)

        # ── face-count guard — same discipline as classify_pockets ─────────
        if args.max_face_count is not None and len(faces) > args.max_face_count:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"topology_health": {
                    "is_valid": None,
                    "statuses": {},
                    "free_edge_count": None,
                    "open_wire_count": None,
                    "closed_wire_count": None,
                    "non_manifold_edge_count": None,
                    "sliver_faces": None,
                    "sliver_edges": None,
                    "self_intersecting": None,
                    "self_intersection_pair_count": None,
                    "guarded": True,
                    "face_count": len(faces),
                    "limit": args.max_face_count,
                    "reason": "too_big",
                    "advice": "decimate the input mesh (mesh_decimate skill) "
                              "or simplify_to_canonical the BREP before "
                              "calling topology_health.",
                }},
            )

        edges = _all_edges(shape)

        # (a) analyzer verdict + status breakdown
        analyzer = BRepCheck_Analyzer(shape)
        is_valid = bool(analyzer.IsValid())
        statuses: dict[str, Any] = {}
        if not is_valid:
            statuses = _enumerate_statuses(analyzer, shape)

        # (b) free boundaries + edge incidence
        closed_wires, open_wires = _free_bound_wire_counts(shape)
        free_edges, non_manifold_edges = _edge_incidence_census(shape)

        # (c) sliver census
        sliver_faces, sliver_edges = _sliver_census(
            faces, edges,
            float(args.sliver_face_area_mm2),
            float(args.sliver_edge_length_mm),
        )

        # (d) optional self-intersection
        self_intersecting: bool | None = None
        si_pairs: int | None = None
        if args.check_self_intersection:
            self_intersecting, si_pairs = _self_intersection_check(shape)

        extras = {
            "topology_health": {
                "is_valid": is_valid,
                "statuses": statuses,
                "free_edge_count": free_edges,
                "open_wire_count": open_wires,
                "closed_wire_count": closed_wires,
                "non_manifold_edge_count": non_manifold_edges,
                "sliver_faces": sliver_faces,
                "sliver_edges": sliver_edges,
                "self_intersecting": self_intersecting,
                "self_intersection_pair_count": si_pairs,
                "guarded": False,
                "face_count": len(faces),
            }
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
