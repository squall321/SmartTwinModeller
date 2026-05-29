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

extras schema:
    {
      "min_radius_mm": float,
      "concave_edges_total": int,
      "violations": [
        {"edge_idx": int, "midpoint": [x,y,z], "current_radius_mm": float},
        ...
      ],
      "edges_filleted": int,     # 0 when auto_fix=False
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
        sub_threshold: list[Any] = []
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
                sub_threshold.append(e)

        result_shape = shape
        ok_count = 0
        if args.auto_fix and sub_threshold:
            # Bulk attempt, then per-edge fallback.
            try:
                maker = BRepFilletAPI_MakeFillet(shape)
                for e in sub_threshold:
                    maker.Add(args.min_radius_mm, e)
                maker.Build()
                if maker.IsDone():
                    result_shape = maker.Shape()
                    ok_count = len(sub_threshold)
                else:
                    raise RuntimeError("bulk min_tool_radius fillet IsDone=False")
            except Exception:
                ok_count = 0
                for e in sub_threshold:
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
            "auto_fix": bool(args.auto_fix),
        }
        return SkillResult(
            body=Part(result_shape) if result_shape is not shape else body,
            history=history,
            extras=extras,
        )
