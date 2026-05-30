"""bridge_length_check — atomic, read-only 3D printing DFM inspect.

A bridge in FDM is a horizontal edge (perpendicular to ``build_direction``)
that spans a gap with no material directly beneath it. FDM printers can bridge
a limited span (~10 mm without sag); longer bridges either need support or
break the print.

For each edge:
  1. Check if it is "horizontal" — its tangent direction is perpendicular to
     build_direction within a tolerance.
  2. Cast a ray from the edge midpoint along -build_direction; if the ray hits
     the body within a short distance (< 0.5 mm) the edge sits on top of solid
     material (not a bridge). Otherwise mark as unsupported.
  3. Compute the edge length and flag whenever length_mm > max_bridge_mm.

extras["bridges"] schema:
    [{"edge_idx": int,
      "length_mm": float,
      "unsupported": bool,
      "midpoint": [x, y, z],
      "exceeds_max": bool}, ...]
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _edge_endpoints_and_mid(edge):
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp

    v1 = TopExp.FirstVertex_s(edge)
    v2 = TopExp.LastVertex_s(edge)
    p1 = BRep_Tool.Pnt_s(v1)
    p2 = BRep_Tool.Pnt_s(v2)
    s = (p1.X(), p1.Y(), p1.Z())
    e = (p2.X(), p2.Y(), p2.Z())
    m = ((s[0] + e[0]) / 2, (s[1] + e[1]) / 2, (s[2] + e[2]) / 2)
    return s, e, m


def _edge_length(edge) -> float:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint

    return GCPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(edge))


def _ray_hits_body_below(shape, point, direction, max_dist) -> bool:
    """True if a ray from ``point`` along ``direction`` hits the body within
    ``max_dist`` mm (skipping the seed point itself)."""
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

    try:
        line = gp_Lin(
            gp_Pnt(point[0], point[1], point[2]),
            gp_Dir(direction[0], direction[1], direction[2]),
        )
    except Exception:
        return False
    try:
        inter = BRepIntCurveSurface_Inter()
        inter.Init(shape, line, 1e-6)
    except Exception:
        return False
    while inter.More():
        t = inter.W()
        # Skip the seed point itself (t very near 0)
        if 1e-3 < t <= max_dist:
            return True
        inter.Next()
    return False


@skill(
    name="bridge_length_check",
    category="inspect",
    level="atomic",
    summary="Find horizontal edges that span an unsupported gap (bridges) and "
            "flag any whose length exceeds max_bridge_mm. Bridges longer than "
            "~10 mm are unreliable on FDM without supports. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["bridge_report"],
    preserves=["body_topology"],
    manufacturing={
        "fdm": {"extras": {"max_bridge_mm": 10.0}},
        "sla": {"extras": {"max_bridge_mm": 5.0}},
        "sls": {"extras": {"max_bridge_mm": 50.0, "self_supporting": True}},
    },
    failure_modes=["fm.zero_build_vector"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class BridgeLengthCheck(SkillBase):
    class Args(BaseModel):
        max_bridge_mm: float = Field(default=10.0, gt=0.0, le=500.0)
        build_direction: tuple[float, float, float] = Field(default=(0.0, 0.0, 1.0))
        horizontal_tol_deg: float = Field(default=5.0, ge=0.0, le=45.0,
                                          description="how far from horizontal "
                                                      "an edge tangent may tilt "
                                                      "and still count as a bridge")
        support_gap_mm: float = Field(default=0.5, gt=0.0, le=50.0,
                                       description="if there is solid material this "
                                                   "close beneath the edge midpoint, "
                                                   "the edge is *supported*")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_edges

        bx, by, bz = args.build_direction
        blen = math.sqrt(bx * bx + by * by + bz * bz)
        if blen < 1e-12:
            raise ValueError("bridge_length_check: build_direction must be non-zero")
        bdir = (bx / blen, by / blen, bz / blen)
        ndown = (-bdir[0], -bdir[1], -bdir[2])
        # cos of the max allowed tangent-vs-horizontal angle (= sin tol)
        horizontal_max_dot = math.sin(math.radians(args.horizontal_tol_deg))

        shape = _occt_shape(body)
        edges = _all_edges(shape)

        bridges: list[dict[str, Any]] = []
        for idx, edge in enumerate(edges):
            try:
                s, e, m = _edge_endpoints_and_mid(edge)
            except Exception:
                continue
            try:
                length = _edge_length(edge)
            except Exception:
                length = 0.0
            if length < 1e-9:
                continue
            # tangent vector (straight-line approximation) → must be ~perp to build dir
            tx = (e[0] - s[0]) / length
            ty = (e[1] - s[1]) / length
            tz = (e[2] - s[2]) / length
            dot_with_build = abs(tx * bdir[0] + ty * bdir[1] + tz * bdir[2])
            if dot_with_build > horizontal_max_dot:
                continue  # not horizontal — skip
            # ray-cast from midpoint along -build_direction. If anything is
            # hit within support_gap_mm, the edge is supported.
            supported = _ray_hits_body_below(shape, m, ndown, args.support_gap_mm)
            unsupported = not supported
            exceeds = unsupported and (length > args.max_bridge_mm)
            bridges.append({
                "edge_idx": idx,
                "length_mm": round(length, 4),
                "unsupported": bool(unsupported),
                "midpoint": [round(c, 4) for c in m],
                "exceeds_max": bool(exceeds),
            })

        unsupported_n = sum(1 for b in bridges if b["unsupported"])
        too_long_n = sum(1 for b in bridges if b["exceeds_max"])
        max_len = max((b["length_mm"] for b in bridges if b["unsupported"]),
                      default=0.0)

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "bridges": bridges,
                "bridge_summary": {
                    "build_direction": [round(c, 4) for c in bdir],
                    "max_bridge_mm": args.max_bridge_mm,
                    "bridge_count": len(bridges),
                    "unsupported_count": unsupported_n,
                    "too_long_count": too_long_n,
                    "max_unsupported_length_mm": round(max_len, 4),
                },
            },
        )
