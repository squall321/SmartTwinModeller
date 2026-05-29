"""edge_to_edge_distance — atomic, read-only.

Compute the minimum Euclidean distance between two selected edges on the
body, using OCCT's BRepExtrema_DistShape distance algorithm. Returns the
distance plus the two closest points (one per edge).

If either selector resolves to multiple edges, the minimum across all
edge-pairs is reported and the chosen pair is included.

extras schema:
    {
      "distance_mm": float,
      "closest_point_a": [x,y,z],
      "closest_point_b": [x,y,z],
      "edge_count_a": int,
      "edge_count_b": int,
      "pair_index_a": int,            # which edge in selector_a matched
      "pair_index_b": int,            # which edge in selector_b matched
      "edge_a_length_mm": float,
      "edge_b_length_mm": float
    }

body unchanged (post ``body_present``).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _edge_min_distance(edge_a, edge_b) -> tuple[float, tuple[float, float, float],
                                                  tuple[float, float, float]]:
    """Use BRepExtrema_DistShapeShape to find min distance + closest points."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    algo = BRepExtrema_DistShapeShape(edge_a, edge_b)
    algo.Perform()
    if not algo.IsDone() or algo.NbSolution() < 1:
        # Fallback: sample-based estimate
        return _fallback_edge_distance(edge_a, edge_b)

    d = float(algo.Value())
    p1 = algo.PointOnShape1(1)
    p2 = algo.PointOnShape2(1)
    return d, (p1.X(), p1.Y(), p1.Z()), (p2.X(), p2.Y(), p2.Z())


def _fallback_edge_distance(edge_a, edge_b) -> tuple[float, tuple[float, float, float],
                                                       tuple[float, float, float]]:
    """Sample-based fallback if BRepExtrema fails."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    def samples(edge, n=32):
        c = BRepAdaptor_Curve(edge)
        u0, u1 = c.FirstParameter(), c.LastParameter()
        pts = []
        for i in range(n):
            u = u0 + (u1 - u0) * (i / (n - 1)) if n > 1 else u0
            p = c.Value(u)
            pts.append((p.X(), p.Y(), p.Z()))
        return pts

    pa = samples(edge_a)
    pb = samples(edge_b)
    best = (float("inf"), pa[0], pb[0])
    for ap in pa:
        for bp in pb:
            dx = ap[0] - bp[0]
            dy = ap[1] - bp[1]
            dz = ap[2] - bp[2]
            d2 = dx * dx + dy * dy + dz * dz
            if d2 < best[0]:
                best = (d2, ap, bp)
    return math.sqrt(best[0]), best[1], best[2]


@skill(
    name="edge_to_edge_distance",
    category="inspect",
    level="atomic",
    summary="Minimum distance between two selected edges via "
            "BRepExtrema_DistShapeShape. If either selector matches multiple "
            "edges, the global minimum pair is reported. Read-only — body unchanged.",
    selector_kinds=[
        "axis_aligned_edges", "edges_on_face", "edges_by_length",
        "edges_by_position", "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["edge_distance"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class EdgeToEdgeDistance(SkillBase):
    class Args(BaseModel):
        edge_selector_a: dict
        edge_selector_b: dict

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _edge_length, resolve_edges

        shape = _occt_shape(body)
        sel_a = _coerce_selector(args.edge_selector_a)
        sel_b = _coerce_selector(args.edge_selector_b)

        edges_a = resolve_edges(shape, sel_a)
        edges_b = resolve_edges(shape, sel_b)
        if not edges_a:
            raise ValueError(
                f"edge_to_edge_distance: edge_selector_a matched 0 edges "
                f"(kind={sel_a.kind})"
            )
        if not edges_b:
            raise ValueError(
                f"edge_to_edge_distance: edge_selector_b matched 0 edges "
                f"(kind={sel_b.kind})"
            )

        best_d = float("inf")
        best_pa: tuple[float, float, float] = (0.0, 0.0, 0.0)
        best_pb: tuple[float, float, float] = (0.0, 0.0, 0.0)
        best_i = -1
        best_j = -1

        for i, ea in enumerate(edges_a):
            for j, eb in enumerate(edges_b):
                try:
                    d, pa, pb = _edge_min_distance(ea, eb)
                except Exception:
                    continue
                if d < best_d:
                    best_d = d
                    best_pa = pa
                    best_pb = pb
                    best_i = i
                    best_j = j

        if best_i < 0:
            raise ValueError(
                "edge_to_edge_distance: failed to compute distance for any pair"
            )

        try:
            len_a = _edge_length(edges_a[best_i])
        except Exception:
            len_a = 0.0
        try:
            len_b = _edge_length(edges_b[best_j])
        except Exception:
            len_b = 0.0

        extras = {
            "distance_mm": round(best_d, 6),
            "closest_point_a": [round(c, 6) for c in best_pa],
            "closest_point_b": [round(c, 6) for c in best_pb],
            "edge_count_a": len(edges_a),
            "edge_count_b": len(edges_b),
            "pair_index_a": int(best_i),
            "pair_index_b": int(best_j),
            "edge_a_length_mm": round(float(len_a), 6),
            "edge_b_length_mm": round(float(len_b), 6),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
