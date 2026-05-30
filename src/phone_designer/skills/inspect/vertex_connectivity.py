"""vertex_connectivity — atomic, read-only.

For each unique vertex of the body, record its 3D position, degree (number
of incident edges), and the indices of those edges in the body's enumerated
edge list (same order as ``_all_edges``).

extras["vertices"] = [
    {"idx": i,
     "position": [x, y, z],
     "degree": N,
     "incident_edges": [edge_idx, ...]},
    ...
]

Body unchanged — post ``body_present``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _all_unique_vertices(shape) -> list:
    """Unique TopoDS_Vertex list (dedup via TopTools_MapOfShape)."""
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_MapOfShape

    seen = TopTools_MapOfShape()
    out = []
    it = TopExp_Explorer(shape, TopAbs_VERTEX)
    while it.More():
        v = it.Current()
        if not seen.Contains(v):
            seen.Add(v)
            out.append(TopoDS.Vertex_s(v))
        it.Next()
    return out


@skill(
    name="vertex_connectivity",
    category="inspect",
    level="atomic",
    summary="For each unique vertex, list its position, degree, and incident "
            "edge indices (same order as the body's enumerated edge list). "
            "Result on extras['vertices']; body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["vertex_connectivity"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class VertexConnectivity(SkillBase):
    class Args(BaseModel):
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRep import BRep_Tool
        from OCP.TopAbs import TopAbs_VERTEX
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        from phone_designer.skills._resolvers import _all_edges

        shape = _occt_shape(body)
        edges = _all_edges(shape)
        vertices = _all_unique_vertices(shape)

        # Pre-compute edge → list of sub-vertices (TopExp on each edge).
        edge_sub_vertices: list[list] = []
        for e in edges:
            sub: list = []
            sit = TopExp_Explorer(e, TopAbs_VERTEX)
            while sit.More():
                sub.append(TopoDS.Vertex_s(sit.Current()))
                sit.Next()
            edge_sub_vertices.append(sub)

        entries: list[dict[str, Any]] = []
        for vi, v in enumerate(vertices):
            try:
                p = BRep_Tool.Pnt_s(v)
                pos = (p.X(), p.Y(), p.Z())
            except Exception:
                pos = (0.0, 0.0, 0.0)

            incident: list[int] = []
            for ei, sub_vs in enumerate(edge_sub_vertices):
                for sv in sub_vs:
                    if sv.IsSame(v):
                        incident.append(ei)
                        break

            entries.append({
                "idx": vi,
                "position": [round(pos[0], 6), round(pos[1], 6), round(pos[2], 6)],
                "degree": len(incident),
                "incident_edges": incident,
            })

        extras = {"vertices": entries}
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
