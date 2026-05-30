"""edge_polyline_extract — atomic, read-only.

For each edge matched by the selector, sample ``segments_per_edge + 1`` points
uniformly along the curve's arc length using OCCT
``GCPnts_UniformAbscissa`` (with a tabulated fallback). The resulting
polylines are used for wire-frame viz, hover-tooltip overlays, and any code
that wants to walk a feature edge in tiny linear steps.

extras["polylines"] schema:
    [
      [(x, y, z), ...],   # one list per resolved edge, in selector order
      ...
    ]

Body is returned unchanged — post ``body_present``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family: str, name: str):
    import pathlib

    import yaml
    here = pathlib.Path(__file__).resolve()
    for root in here.parents:
        cand = root / "catalogs" / family / f"{name}.yaml"
        if cand.exists():
            return yaml.safe_load(cand.read_text())
    raise FileNotFoundError(f"catalog not found: {family}/{name}.yaml")


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _sample_edge(edge, segments_per_edge: int) -> list[tuple[float, float, float]]:
    """Return ``segments_per_edge + 1`` points along the edge curve.

    Uniform in arc length where ``GCPnts_UniformAbscissa`` converges; otherwise
    falls back to uniform parameter sampling.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_UniformAbscissa

    n_pts = max(2, int(segments_per_edge) + 1)
    points: list[tuple[float, float, float]] = []

    try:
        curve = BRepAdaptor_Curve(edge)
    except Exception:
        return points

    # Arc-length uniform.
    try:
        ua = GCPnts_UniformAbscissa(curve, n_pts)
        if ua.IsDone() and ua.NbPoints() >= 2:
            for i in range(1, ua.NbPoints() + 1):
                t = ua.Parameter(i)
                p = curve.Value(t)
                points.append((
                    round(p.X(), 6),
                    round(p.Y(), 6),
                    round(p.Z(), 6),
                ))
            return points
    except Exception:
        pass

    # Fallback: uniform in parameter.
    try:
        t0 = curve.FirstParameter()
        t1 = curve.LastParameter()
        for i in range(n_pts):
            t = t0 + (t1 - t0) * (i / (n_pts - 1))
            p = curve.Value(t)
            points.append((
                round(p.X(), 6),
                round(p.Y(), 6),
                round(p.Z(), 6),
            ))
    except Exception:
        return []
    return points


@skill(
    name="edge_polyline_extract",
    category="inspect",
    level="atomic",
    summary="Sample N+1 points uniformly along each edge matched by the "
            "selector and return the resulting polylines for wire-frame viz "
            "or geometric walking. Read-only — body unchanged.",
    selector_kinds=[
        "axis_aligned_edges", "edges_by_position", "edges_by_length",
        "edges_on_face", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["edge_polyline"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class EdgePolylineExtract(SkillBase):
    class Args(BaseModel):
        edge_selector: dict
        segments_per_edge: int = Field(default=20, ge=1, le=10000)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_edges

        if body is None:
            raise ValueError("edge_polyline_extract: body is None")

        shape = _occt_shape(body)
        if shape is None:
            raise ValueError("edge_polyline_extract: body.wrapped is None")

        sel = _coerce_selector(args.edge_selector)
        edges = resolve_edges(shape, sel)

        polylines: list[list[tuple[float, float, float]]] = []
        for e in edges:
            try:
                pts = _sample_edge(e, args.segments_per_edge)
            except Exception:
                pts = []
            polylines.append(pts)

        extras = {
            "polylines": polylines,
            "edge_count": len(edges),
            "segments_per_edge": int(args.segments_per_edge),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
