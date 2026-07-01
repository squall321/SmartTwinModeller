"""curvature_comb — atomic, read-only.

The curve-quality tool. Sample radius-of-curvature / curvature ALONG a selected
EDGE, so downstream code (or a human) can spot inflection points, flat spots,
and G2 (curvature-continuity) breaks — the things a Class-A surfacing review
lives and dies by.

Existing curvature skills probe a SURFACE via ``BRepLProp_SLProps``. This one
probes a CURVE via ``BRepLProp_CLProps`` on ``BRepAdaptor_Curve(edge)`` — the
1D analogue: at every station it reads the local curvature κ (= 1/R) and the
3D point on the curve.

Strategy:
    1. Resolve ``edge_selector`` → first matched edge on body.
    2. ``BRepAdaptor_Curve(edge)`` → arc-length station it with
       ``GCPnts_UniformAbscissa`` (uniform-in-arc-length; falls back to uniform
       parameter sampling if the abscissa algo does not converge).
    3. At each station u: ``BRepLProp_CLProps(ac, u, 2, tol)`` →
       ``.Curvature()`` (1/radius) and ``.Value()`` (the 3D point).
    4. radius = 1/κ when κ > eps, else +inf (a straight segment has κ = 0).

A curvature comb for a true circle of radius R reads κ = 1/R at EVERY station
(constant), which is the classic sanity check the test asserts.

extras schema:
    {
      "samples": [
        {"u": float, "point": [x, y, z], "curvature": float, "radius": float},
        ...
      ],
      "min_radius": float,        # tightest curve on the edge (smallest R)
      "max_curvature": float,     # = 1 / min_radius
      "n_samples": int,           # stations actually taken
      "edge_count": int,          # edges the selector matched
    }

Body is returned unchanged — post ``body_present``.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


# Curvature below this (1/mm) is treated as a straight segment → radius = inf.
# 1e-9 /mm ⇔ radius 1e9 mm (1000 km); anything flatter is "flat".
_CURV_EPS = 1e-9

# CLProps resolution tol (mm). Same order as OCCT default linear tol.
_CLPROP_TOL = 1e-7


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _stations(curve, n_samples: int) -> list[float]:
    """Return ``n_samples`` parameter values along the curve.

    Uniform in arc length via ``GCPnts_UniformAbscissa`` where it converges;
    otherwise uniform in parameter between First/Last. Always returns at least
    2 stations (or [] on a degenerate curve).
    """
    from OCP.GCPnts import GCPnts_UniformAbscissa

    n = max(2, int(n_samples))

    # Arc-length uniform stationing.
    try:
        algo = GCPnts_UniformAbscissa(curve, n)
        if algo.IsDone() and algo.NbPoints() >= 2:
            return [algo.Parameter(i) for i in range(1, algo.NbPoints() + 1)]
    except Exception:
        pass

    # Fallback: uniform in parameter.
    try:
        u0 = curve.FirstParameter()
        u1 = curve.LastParameter()
    except Exception:
        return []
    if not (u1 > u0):
        return []
    return [u0 + (u1 - u0) * (i / (n - 1)) for i in range(n)]


@skill(
    name="curvature_comb",
    category="inspect",
    level="atomic",
    summary="Sample radius-of-curvature / curvature along a selected EDGE via "
            "BRepLProp_CLProps (the curve-quality tool: inflections, flat "
            "spots, G2 breaks). Returns per-station {u, point, curvature, "
            "radius} plus min_radius / max_curvature. Read-only — body "
            "unchanged.",
    selector_kinds=[
        "axis_aligned_edges", "edges_on_face", "edges_by_length",
        "edges_by_position", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["curvature_comb"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class CurvatureComb(SkillBase):
    class Args(BaseModel):
        edge_selector: dict
        n_samples: int = Field(default=40, ge=2, le=100000)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.BRepLProp import BRepLProp_CLProps

        from phone_designer.skills._resolvers import resolve_edges

        if body is None:
            raise ValueError("curvature_comb: body is None")
        shape = _occt_shape(body)
        if shape is None:
            raise ValueError("curvature_comb: body.wrapped is None")

        sel = _coerce_selector(args.edge_selector)
        edges = resolve_edges(shape, sel)
        if not edges:
            raise ValueError(
                f"curvature_comb: edge_selector matched 0 edges (kind={sel.kind})"
            )
        edge = edges[0]

        ac = BRepAdaptor_Curve(edge)
        us = _stations(ac, args.n_samples)

        # BRepLProp_CLProps(curve, order=2, resolution) — SetParameter(u) per
        # station reuses one object (matches OCCT CLProps usage; avoids
        # reconstructing the differential apparatus at every station).
        props = BRepLProp_CLProps(ac, 2, _CLPROP_TOL)

        samples: list[dict[str, Any]] = []
        min_radius = math.inf
        max_curvature = 0.0

        for u in us:
            try:
                props.SetParameter(u)
                kappa = abs(float(props.Curvature()))
                p = props.Value()
                pt = [round(p.X(), 9), round(p.Y(), 9), round(p.Z(), 9)]
            except Exception:
                # Skip an unevaluable station rather than aborting the comb.
                continue

            if kappa > _CURV_EPS:
                radius = 1.0 / kappa
            else:
                radius = math.inf

            samples.append({
                "u": round(float(u), 9),
                "point": pt,
                "curvature": round(kappa, 12),
                # inf is JSON-unfriendly; keep the float here — callers that
                # serialize should map inf → null. Rounding leaves inf intact.
                "radius": radius if math.isinf(radius) else round(radius, 9),
            })

            if kappa > max_curvature:
                max_curvature = kappa
            if radius < min_radius:
                min_radius = radius

        extras = {
            "samples": samples,
            "min_radius": min_radius if math.isinf(min_radius) else round(min_radius, 9),
            "max_curvature": round(max_curvature, 12),
            "n_samples": len(samples),
            "edge_count": len(edges),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
