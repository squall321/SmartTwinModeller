"""basic_dimension_attach — atomic, read-only.

Attach a basic (theoretically exact) dimension between two entities. Used
for GD&T basic dimensions (boxed in drawings) — the theoretical target
value position/angle/diameter that downstream true-position tolerances
reference.

If ``nominal_value`` is None, the dimension is *measured* on the body and
that value is stored as the nominal. Otherwise the supplied value is
recorded as-is (the engineering intent), but the measured value is also
computed for reference.

Entity selector schema (matches inspect.measure):
    {"kind": "face_center" | "edge_midpoint" | "vertex" | "bbox_center"
             | "point",
     "selector": {...}                       # not for "point"
     "point": (x, y, z)                      # for "point"
    }

Storage:
    body._pd_basic_dims = [
        {"a": <entity_dict>,
         "b": <entity_dict>,
         "kind": "distance" | "angle" | "diameter" | "radius" | "arc_length",
         "value": float,         # nominal (basic) value
         "measured": float,      # actual on body (may equal value)
         "basic": bool},
        ...
    ]

extras["dimension"] = the just-appended entry plus its list index.

body unchanged (post ``body_present``).
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(s: Any) -> SelectorBase | None:
    if s is None:
        return None
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _resolve_entity(
    shape, entity: dict[str, Any],
) -> tuple[tuple[float, float, float], str, Any]:
    """Return (position, kind_tag, raw_occt_entity_or_None)."""
    from phone_designer.skills._resolvers import (
        _edge_endpoints,
        _face_center,
        resolve_edges,
        resolve_faces,
    )

    kind = entity.get("kind")
    if kind == "point":
        pt = entity.get("point")
        if pt is None or len(pt) != 3:
            raise ValueError("entity kind=point requires point=(x,y,z)")
        return (float(pt[0]), float(pt[1]), float(pt[2])), "point", None

    sel = _coerce_selector(entity.get("selector"))
    if sel is None:
        raise ValueError(f"entity kind={kind} requires selector")

    if kind == "face_center":
        faces = resolve_faces(shape, sel)
        if not faces:
            raise ValueError(
                f"selector matched 0 faces (entity face_center sel={sel.kind})"
            )
        f = faces[0]
        return _face_center(f), "face", f

    if kind == "edge_midpoint":
        edges = resolve_edges(shape, sel)
        if not edges:
            raise ValueError(
                f"selector matched 0 edges (entity edge_midpoint sel={sel.kind})"
            )
        e = edges[0]
        p1, p2 = _edge_endpoints(e)
        return (
            ((p1.X() + p2.X()) / 2, (p1.Y() + p2.Y()) / 2, (p1.Z() + p2.Z()) / 2),
            "edge",
            e,
        )

    if kind in ("vertex", "bbox_center"):
        # bbox_center over all matches.
        positions: list[tuple[float, float, float]] = []
        try:
            for f in resolve_faces(shape, sel):
                positions.append(_face_center(f))
        except Exception:
            pass
        try:
            for e in resolve_edges(shape, sel):
                p1, p2 = _edge_endpoints(e)
                positions.append(((p1.X() + p2.X()) / 2,
                                   (p1.Y() + p2.Y()) / 2,
                                   (p1.Z() + p2.Z()) / 2))
        except Exception:
            pass
        if not positions:
            raise ValueError("selector matched 0 entities")
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        zs = [p[2] for p in positions]
        return (
            ((min(xs) + max(xs)) / 2,
             (min(ys) + max(ys)) / 2,
             (min(zs) + max(zs)) / 2),
            kind,
            None,
        )

    raise ValueError(f"unknown entity kind: {kind}")


def _cylinder_radius(face) -> float | None:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Sphere, GeomAbs_Torus

    try:
        surf = BRepAdaptor_Surface(face)
        gt = surf.GetType()
        if gt == GeomAbs_Cylinder:
            return float(surf.Cylinder().Radius())
        if gt == GeomAbs_Sphere:
            return float(surf.Sphere().Radius())
        if gt == GeomAbs_Torus:
            return float(surf.Torus().MinorRadius())
    except Exception:
        return None
    return None


def _edge_arc_length(edge) -> float:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint

    try:
        return float(GCPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(edge)))
    except Exception:
        return 0.0


def _measure_dimension(
    shape, a_entity: dict, b_entity: dict, kind: str,
) -> float:
    """Compute the actual on-body value for the requested dimension kind."""
    pos_a, _, raw_a = _resolve_entity(shape, a_entity)
    pos_b, _, raw_b = _resolve_entity(shape, b_entity)

    if kind == "distance":
        dx = pos_a[0] - pos_b[0]
        dy = pos_a[1] - pos_b[1]
        dz = pos_a[2] - pos_b[2]
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    if kind == "angle":
        # Use face normals or edge tangents.
        from phone_designer.skills._resolvers import _face_normal_at_center

        def _vec_of(raw):
            if raw is None:
                return None
            # Face first.
            try:
                n = _face_normal_at_center(raw)
                if n != (0.0, 0.0, 0.0):
                    return n
            except Exception:
                pass
            # Edge tangent fallback.
            try:
                from OCP.BRepAdaptor import BRepAdaptor_Curve
                from OCP.gp import gp_Pnt, gp_Vec

                curve = BRepAdaptor_Curve(raw)
                u = 0.5 * (curve.FirstParameter() + curve.LastParameter())
                p = gp_Pnt()
                v = gp_Vec()
                curve.D1(u, p, v)
                mag = math.sqrt(v.X() ** 2 + v.Y() ** 2 + v.Z() ** 2)
                if mag > 1e-12:
                    return (v.X() / mag, v.Y() / mag, v.Z() / mag)
            except Exception:
                pass
            return None

        va = _vec_of(raw_a)
        vb = _vec_of(raw_b)
        if va is None or vb is None:
            raise ValueError(
                "angle dimension requires two faces or two edges as entities"
            )
        dot = max(-1.0, min(1.0, va[0] * vb[0] + va[1] * vb[1] + va[2] * vb[2]))
        return math.degrees(math.acos(dot))

    if kind in ("diameter", "radius"):
        # Pull radius from raw_a (face) preferentially, otherwise raw_b.
        r = _cylinder_radius(raw_a) if raw_a is not None else None
        if r is None and raw_b is not None:
            r = _cylinder_radius(raw_b)
        if r is None:
            raise ValueError(
                f"{kind} dimension requires a cylindrical / spherical / "
                f"toroidal face as one of the entities"
            )
        return 2.0 * r if kind == "diameter" else r

    if kind == "arc_length":
        if raw_a is not None and hasattr(raw_a, "Orientation"):
            try:
                # treat raw_a as edge
                return _edge_arc_length(raw_a)
            except Exception:
                pass
        if raw_b is not None:
            try:
                return _edge_arc_length(raw_b)
            except Exception:
                pass
        raise ValueError("arc_length dimension requires an edge entity")

    raise ValueError(f"unsupported dimension_kind: {kind}")


@skill(
    name="basic_dimension_attach",
    category="inspect",
    level="atomic",
    summary="Attach a basic (theoretically-exact) dimension between two "
            "entities (distance/angle/diameter/radius/arc_length). If no "
            "nominal supplied, measure on-body. Stored on body._pd_basic_dims. "
            "Read-only.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "axis_aligned_edges", "edges_on_face", "edges_by_length",
        "edges_by_position", "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["basic_dimension"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class BasicDimensionAttach(SkillBase):
    class Args(BaseModel):
        entity_a_selector: dict
        entity_b_selector: dict
        dimension_kind: Literal[
            "distance", "angle", "diameter", "radius", "arc_length"
        ]
        nominal_value: float | None = None
        basic: bool = True

    def _apply(self, body: Any, args: Args) -> SkillResult:
        shape = _occt_shape(body)

        measured = _measure_dimension(
            shape, args.entity_a_selector, args.entity_b_selector,
            args.dimension_kind,
        )
        nominal = (
            float(args.nominal_value)
            if args.nominal_value is not None
            else float(measured)
        )

        entry = {
            "a": args.entity_a_selector,
            "b": args.entity_b_selector,
            "kind": args.dimension_kind,
            "value": round(nominal, 6),
            "measured": round(measured, 6),
            "basic": bool(args.basic),
        }

        try:
            existing = list(getattr(body, "_pd_basic_dims", []) or [])
        except Exception:
            existing = []
        existing.append(entry)
        try:
            body._pd_basic_dims = existing
        except Exception:
            pass

        extras = {
            "dimension": entry,
            "index": len(existing) - 1,
            "total": len(existing),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
