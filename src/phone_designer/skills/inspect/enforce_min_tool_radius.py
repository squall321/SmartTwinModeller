"""enforce_min_tool_radius — atomic. CNC tool-radius DFM enforcement.

A 3-axis CNC cutter cannot machine an internal (concave) corner sharper than
the tool's own radius. Sharp internal corners therefore either need to be
filleted to >= min_tool_radius_mm or the part is unmachinable as drawn.

This skill walks every concave internal edge and:
  1. Estimates its current fillet radius from adjacent face geometry.
     - SHARP (≈0) concave edge → below threshold.
     - Toroidal / cylindrical adjacent face → MinorRadius / Radius.
  2. If radius < min_radius_mm:
        - auto_fix=True  → BRepFilletAPI_MakeFillet(min_radius_mm, edge)
        - auto_fix=False → record a violation, leave geometry untouched.

Convex edges are not subject to the rule and are ignored.

CRASH GUARD (auto_fix=True): OCCT's BRepFilletAPI_MakeFillet HARD-CRASHES the
process (0xC0000005 ACCESS_VIOLATION, not a catchable exception) when the edge
being filleted terminates at a vertex where a CLOSED edge (e.g. a cross-hole
opening circle) OSCULATES it — tangent-parallel contact with the circle's seam
at the tangency vertex, leaving a zero-angle cusp on the shared face. Real
case: a drain hole drilled through a wall whose opening circle touches the
wall/floor concave corner line. Such edges are pre-validated and SKIPPED from
the fillet build with a reported reason (extras['edges_skipped_unsafe']) —
they still count as violations; they are just not auto-fixable here. The guard
is a targeted predicate for this known-fatal configuration, not a general
"will OCCT crash" oracle.

extras schema:
    {
      "min_radius_mm": float,
      "concave_edges_total": int,
      "violations": [
        {"edge_idx": int, "midpoint": [x,y,z], "current_radius_mm": float},
        ...
      ],
      "edges_filleted": int,     # 0 when auto_fix=False
      "edges_skipped_unsafe": [  # crash-prone edges excluded from the build
        {"edge_idx": int, "midpoint": [x,y,z], "reason": str}, ...
      ],
      "auto_fix": bool,
    }

post_condition face_count_changed(allow_no_change=True) — when every concave
edge already meets the threshold (or auto_fix=False) the body legitimately
does not change.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _edge_existing_fillet_radius(shape, edge) -> float:
    """Estimate the local fillet radius at a concave edge.

    Heuristic:
      - If one adjacent face is a torus → its MinorRadius is the fillet radius.
      - If one adjacent face is a cylinder whose axis is parallel to the edge
        tangent → its Radius is the fillet radius.
      - Otherwise the edge is treated as SHARP and reported as radius 0.0.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus

    from phone_designer.skills.modify_finish.sanding_pass import (
        _adjacent_faces,
        _edge_tangent_mid,
    )

    faces = _adjacent_faces(shape, edge)
    radii: list[float] = []
    t = _edge_tangent_mid(edge)

    for f in faces:
        try:
            surf = BRepAdaptor_Surface(f)
            st = surf.GetType()
            if st == GeomAbs_Torus:
                tor = surf.Torus()
                radii.append(float(tor.MinorRadius()))
            elif st == GeomAbs_Cylinder and t is not None:
                cyl = surf.Cylinder()
                ax = cyl.Axis().Direction()
                dot = abs(ax.X() * t[0] + ax.Y() * t[1] + ax.Z() * t[2])
                if dot > 0.9:
                    radii.append(float(cyl.Radius()))
        except Exception:
            continue

    if not radii:
        return 0.0
    return min(radii)


def _edge_tangent_at_vertex(edge, vpnt):
    """Unit tangent of `edge` evaluated at the parameter end closest to the
    vertex point `vpnt`. None when the curve cannot be evaluated."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.gp import gp_Pnt, gp_Vec
    try:
        c = BRepAdaptor_Curve(edge)
        pf, pl = c.FirstParameter(), c.LastParameter()
        u = pf if c.Value(pf).Distance(vpnt) <= c.Value(pl).Distance(vpnt) else pl
        pt, vec = gp_Pnt(), gp_Vec()
        c.D1(u, pt, vec)
        m = vec.Magnitude()
        if m < 1e-12:
            return None
        return (vec.X() / m, vec.Y() / m, vec.Z() / m)
    except Exception:
        return None


def _osculating_tangent_junction(edge, v2e_map,
                                 angle_tol_deg: float = 5.0) -> str | None:
    """MakeFillet crash-guard predicate. Returns a reason string when `edge`
    ends at a vertex where ANOTHER edge osculates it — tangent-parallel AND
    touching that vertex with BOTH of its ends (a closed edge, e.g. a
    cross-hole opening circle whose seam sits exactly at the tangency point).
    Filleting such an edge hard-crashes OCCT (0xC0000005) in ChFi3d corner
    processing — the shared face pinches to a zero-angle cusp there. Deliberately
    NARROW: an ordinary G1 chain neighbour (pocket line->arc, or a collinear
    continuation) touches the vertex with ONE end only and stays fillable."""
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS

    cos_tol = math.cos(math.radians(angle_tol_deg))
    try:
        v_first = TopExp.FirstVertex_s(edge)
        v_last = TopExp.LastVertex_s(edge)
    except Exception:
        return None  # cannot evaluate -> do not over-skip
    verts = [v_first]
    if v_last is not None and not v_last.IsSame(v_first):
        verts.append(v_last)

    for v in verts:
        if v is None or not v2e_map.Contains(v):
            continue
        vpnt = BRep_Tool.Pnt_s(v)
        t_e = _edge_tangent_at_vertex(edge, vpnt)
        if t_e is None:
            continue
        seen = []
        for item in v2e_map.FindFromKey(v):
            other = TopoDS.Edge_s(item)
            if other.IsSame(edge) or any(other.IsSame(s) for s in seen):
                continue
            seen.append(other)
            try:
                of, ol = TopExp.FirstVertex_s(other), TopExp.LastVertex_s(other)
            except Exception:
                continue
            # BOTH ends of the other edge on this vertex = closed/osculating.
            if of is None or ol is None:
                continue
            if not (of.IsSame(v) and ol.IsSame(v)):
                continue
            t_o = _edge_tangent_at_vertex(other, vpnt)
            if t_o is None:
                continue
            dot = abs(t_e[0] * t_o[0] + t_e[1] * t_o[1] + t_e[2] * t_o[2])
            if dot >= cos_tol:
                return ("osculating tangent junction at vertex "
                        f"({vpnt.X():.3f},{vpnt.Y():.3f},{vpnt.Z():.3f}): a "
                        "closed edge (hole-opening circle) touches this edge "
                        "tangentially — BRepFilletAPI_MakeFillet hard-crashes "
                        "(ACCESS_VIOLATION) on this cusp configuration")
    return None


def _fillet_crash_guard(edge, v2e_map, angle_tol_deg: float = 5.0,
                        max_chain: int = 32) -> str | None:
    """Full MakeFillet crash-guard: the direct osculating predicate PLUS its
    transitive closure along tangent-continuous chains.

    MakeFillet's spine PROPAGATES over G1 vertex junctions (a collinear stub
    or a tangent arc continues the fillet), so an edge that is merely
    tangent-connected to an osculating edge reaches the same fatal cusp and
    crashes identically (verified: a corner-line stub left over from a prior
    fillet round, collinear with the skipped osculating segment). BFS the
    tangent chain from `edge`; any member with a direct osculation hit poisons
    the whole chain. Returns a reason string, or None when safe."""
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS

    direct = _osculating_tangent_junction(edge, v2e_map, angle_tol_deg)
    if direct is not None:
        return direct

    cos_tol = math.cos(math.radians(angle_tol_deg))
    visited = [edge]
    queue = [edge]
    while queue and len(visited) <= max_chain:
        cur = queue.pop(0)
        try:
            vf, vl = TopExp.FirstVertex_s(cur), TopExp.LastVertex_s(cur)
        except Exception:
            continue
        verts = [v for v in (vf, vl) if v is not None]
        if len(verts) == 2 and verts[0].IsSame(verts[1]):
            verts = verts[:1]
        for v in verts:
            if not v2e_map.Contains(v):
                continue
            vpnt = BRep_Tool.Pnt_s(v)
            t_c = _edge_tangent_at_vertex(cur, vpnt)
            if t_c is None:
                continue
            for item in v2e_map.FindFromKey(v):
                nxt = TopoDS.Edge_s(item)
                if any(nxt.IsSame(s) for s in visited):
                    continue
                t_n = _edge_tangent_at_vertex(nxt, vpnt)
                if t_n is None:
                    continue
                dot = abs(t_c[0] * t_n[0] + t_c[1] * t_n[1] + t_c[2] * t_n[2])
                if dot < cos_tol:
                    continue  # not a G1 continuation -> spine stops here
                hit = _osculating_tangent_junction(nxt, v2e_map, angle_tol_deg)
                if hit is not None:
                    return ("tangent chain reaches a crash-prone edge (fillet "
                            "spine propagates over G1 junctions): " + hit)
                visited.append(nxt)
                queue.append(nxt)
    return None


@skill(
    name="enforce_min_tool_radius",
    category="inspect",
    level="atomic",
    summary="CNC DFM check — every concave internal edge must have a fillet "
            "radius >= min_radius_mm (the cutter cannot reach a sharper "
            "corner). With auto_fix=True, sub-threshold edges are auto-"
            "filleted; otherwise they are reported as violations.",
    selector_kinds=[],
    history_rules={
        "sub_threshold_edges": HistoryRule.CONSUMED,
        "fillet_faces":        HistoryRule.GENERATED_NEW,
        "ok_edges":            HistoryRule.MODIFIED_INHERIT,
    },
    produces_features=["min_tool_radius_report"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis": {"min_fillet_r_mm": "args.min_radius_mm",
                      "extras": {"tool_radius_rule": "internal_corner"}},
    },
    failure_modes=["fm.fillet_self_intersection"],
    cost_hint=0.35,
    # If every concave edge already passes, or auto_fix=False, the body does
    # not change. allow_no_change preserves that case.
    post_conditions=[PostCondition(kind="face_count_changed", allow_no_change=True)],
)
class EnforceMinToolRadius(SkillBase):
    class Args(BaseModel):
        min_radius_mm: float = Field(
            gt=0.0, le=25.0,
            description="Minimum internal-corner fillet radius (= cutter radius).",
        )
        auto_fix: bool = Field(
            default=True,
            description="True → auto-fillet sub-threshold edges. "
                        "False → report violations only.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        from phone_designer.skills._resolvers import _all_edges
        from phone_designer.skills.modify_finish.cleanability_radius_enforce import (
            _is_concave_edge,
        )
        from phone_designer.skills.modify_finish.sanding_pass import _edge_midpoint

        shape = _occt_shape(body)
        all_edges = _all_edges(shape)

        concave_pairs: list[tuple[int, Any]] = []
        for idx, e in enumerate(all_edges):
            try:
                if _is_concave_edge(shape, e):
                    concave_pairs.append((idx, e))
            except Exception:
                continue

        violations: list[dict[str, Any]] = []
        sub_threshold: list[tuple[int, Any]] = []
        for idx, e in concave_pairs:
            r = _edge_existing_fillet_radius(shape, e)
            if r + 1e-6 < args.min_radius_mm:
                try:
                    p = _edge_midpoint(e)
                    mp = [round(p.X(), 4), round(p.Y(), 4), round(p.Z(), 4)]
                except Exception:
                    mp = [0.0, 0.0, 0.0]
                violations.append({
                    "edge_idx": idx,
                    "midpoint": mp,
                    "current_radius_mm": round(r, 4),
                })
                sub_threshold.append((idx, e))

        result_shape = shape
        ok_count = 0
        skipped_unsafe: list[dict[str, Any]] = []
        if args.auto_fix and sub_threshold:
            # CRASH GUARD — exclude edges in the known-fatal osculating-tangent
            # configuration (see module docstring): MakeFillet on them kills
            # the whole process (0xC0000005), which no try/except can catch.
            # Skipped edges are REPORTED, never silently dropped.
            from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX
            from OCP.TopExp import TopExp
            from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
            v2e = TopTools_IndexedDataMapOfShapeListOfShape()
            TopExp.MapShapesAndAncestors_s(shape, TopAbs_VERTEX, TopAbs_EDGE, v2e)
            fillable: list[Any] = []
            for idx, e in sub_threshold:
                reason = _fillet_crash_guard(e, v2e)
                if reason is not None:
                    viol = next((vv for vv in violations
                                 if vv["edge_idx"] == idx), None)
                    skipped_unsafe.append({
                        "edge_idx": idx,
                        "midpoint": viol["midpoint"] if viol else [0.0, 0.0, 0.0],
                        "reason": reason,
                    })
                else:
                    fillable.append(e)

            # Bulk attempt, then per-edge fallback.
            if fillable:
                try:
                    maker = BRepFilletAPI_MakeFillet(shape)
                    for e in fillable:
                        maker.Add(args.min_radius_mm, e)
                    maker.Build()
                    if maker.IsDone():
                        result_shape = maker.Shape()
                        ok_count = len(fillable)
                    else:
                        raise RuntimeError("bulk min_tool_radius fillet IsDone=False")
                except Exception:
                    ok_count = 0
                    for e in fillable:
                        try:
                            one = BRepFilletAPI_MakeFillet(result_shape)
                            one.Add(args.min_radius_mm, e)
                            one.Build()
                            if one.IsDone():
                                result_shape = one.Shape()
                                ok_count += 1
                        except Exception:
                            continue

        history = EntityHistoryMap(
            rules={
                "sub_threshold_edges": HistoryRule.CONSUMED,
                "fillet_faces":        HistoryRule.GENERATED_NEW,
                "ok_edges":            HistoryRule.MODIFIED_INHERIT,
            },
        )
        extras = {
            "min_radius_mm": args.min_radius_mm,
            "concave_edges_total": len(concave_pairs),
            "violations": violations,
            "edges_filleted": ok_count,
            "edges_skipped_unsafe": skipped_unsafe,
            "auto_fix": bool(args.auto_fix),
        }
        return SkillResult(
            body=Part(result_shape) if result_shape is not shape else body,
            history=history,
            extras=extras,
        )
