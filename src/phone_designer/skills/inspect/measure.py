"""measure — atomic, read-only.

두 entity 사이의 거리(distance) 또는 각도(angle) 측정. body 는 변경하지 않는다.

entity_a / entity_b 의 ``kind`` 종류:
    - "face_center"    selector + face 의 surface center-of-mass
    - "edge_midpoint"  selector + edge 의 midpoint
    - "vertex"         selector 의 (resolved) entity 의 첫번째 vertex (또는 center)
    - "bbox_center"    selector 가 가리키는 entity 들의 bbox center
    - "point"          직접 좌표 ``point=(x,y,z)``

distance:
    두 위치 사이의 유클리드 거리 (mm).

angle:
    두 entity 가 face 면 normal 사이 각도, edge 면 tangent 사이 각도.
    그 외 조합은 ValueError.

result.extras schema:
    {"value_mm": float | None,   # distance 일 때 mm; angle 일 때 None
     "value_deg": float | None,  # angle 일 때 degrees; distance 일 때 None
     "kind": "distance" | "angle_deg",
     "a_pos": [x,y,z], "b_pos": [x,y,z]}
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


def _resolve_entity_position(
    shape, entity: dict[str, Any],
) -> tuple[tuple[float, float, float], str, Any]:
    """Resolve entity dict → (position, kind_tag, raw_occt_entity).

    raw_occt_entity 는 angle 계산 시 face/edge 의 normal/tangent 가 필요할 때 사용.
    """
    from phone_designer.skills._resolvers import (
        _all_edges,
        _all_faces,
        _edge_endpoints,
        _face_center,
        _face_normal_at_center,
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
            raise ValueError(f"selector matched 0 faces (entity kind=face_center, sel={sel.kind})")
        f = faces[0]
        return _face_center(f), "face", f

    if kind == "edge_midpoint":
        edges = resolve_edges(shape, sel)
        if not edges:
            raise ValueError(f"selector matched 0 edges (entity kind=edge_midpoint, sel={sel.kind})")
        e = edges[0]
        p1, p2 = _edge_endpoints(e)
        return (
            ((p1.X() + p2.X()) / 2, (p1.Y() + p2.Y()) / 2, (p1.Z() + p2.Z()) / 2),
            "edge",
            e,
        )

    if kind == "vertex":
        # selector 는 face 또는 edge 매칭. 첫 entity 의 첫 vertex.
        try:
            edges = resolve_edges(shape, sel)
        except Exception:
            edges = []
        if edges:
            p1, _ = _edge_endpoints(edges[0])
            return (p1.X(), p1.Y(), p1.Z()), "vertex", None
        faces = resolve_faces(shape, sel)
        if not faces:
            raise ValueError("selector matched 0 entities for vertex extraction")
        c = _face_center(faces[0])
        return c, "vertex", None

    if kind == "bbox_center":
        # selector 가 face 또는 edge 인지 모르므로 둘 다 시도해서 결합.
        positions: list[tuple[float, float, float]] = []
        try:
            faces = resolve_faces(shape, sel)
            for f in faces:
                positions.append(_face_center(f))
        except Exception:
            pass
        try:
            edges = resolve_edges(shape, sel)
            for e in edges:
                p1, p2 = _edge_endpoints(e)
                positions.append(((p1.X() + p2.X()) / 2,
                                   (p1.Y() + p2.Y()) / 2,
                                   (p1.Z() + p2.Z()) / 2))
        except Exception:
            pass
        if not positions:
            raise ValueError("selector matched 0 entities for bbox_center")
        # bbox of resolved positions
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        zs = [p[2] for p in positions]
        c = ((min(xs) + max(xs)) / 2,
             (min(ys) + max(ys)) / 2,
             (min(zs) + max(zs)) / 2)
        return c, "bbox_center", None

    raise ValueError(f"unknown entity kind: {kind}")


def _edge_tangent_at_mid(edge) -> tuple[float, float, float]:
    """Edge 의 mid-parameter 점에서의 unit tangent."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    curve = BRepAdaptor_Curve(edge)
    u_first = curve.FirstParameter()
    u_last = curve.LastParameter()
    u_mid = 0.5 * (u_first + u_last)
    # D1 returns position + first derivative.
    from OCP.gp import gp_Pnt, gp_Vec
    p = gp_Pnt()
    v = gp_Vec()
    curve.D1(u_mid, p, v)
    mag = math.sqrt(v.X() * v.X() + v.Y() * v.Y() + v.Z() * v.Z())
    if mag < 1e-12:
        return (1.0, 0.0, 0.0)
    return (v.X() / mag, v.Y() / mag, v.Z() / mag)


@skill(
    name="measure",
    category="inspect",
    level="atomic",
    summary="Measure distance (mm) or angle (deg) between two entities "
            "(face_center / edge_midpoint / vertex / bbox_center / point). "
            "Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "axis_aligned_edges", "edges_on_face", "edges_by_length",
        "edges_by_position", "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["measurement"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class Measure(SkillBase):
    class Args(BaseModel):
        entity_a: dict
        entity_b: dict
        measure_kind: Literal["distance", "angle"] = "distance"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        shape = _occt_shape(body)

        pos_a, ekind_a, raw_a = _resolve_entity_position(shape, args.entity_a)
        pos_b, ekind_b, raw_b = _resolve_entity_position(shape, args.entity_b)

        extras: dict[str, Any] = {
            "a_pos": [round(c, 4) for c in pos_a],
            "b_pos": [round(c, 4) for c in pos_b],
        }

        if args.measure_kind == "distance":
            dx = pos_a[0] - pos_b[0]
            dy = pos_a[1] - pos_b[1]
            dz = pos_a[2] - pos_b[2]
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            extras["value_mm"] = round(d, 6)
            extras["value_deg"] = None
            extras["kind"] = "distance"
        else:  # angle
            # 두 face 또는 두 edge 가 필요
            if ekind_a == "face" and ekind_b == "face":
                from phone_designer.skills._resolvers import _face_normal_at_center
                n_a = _face_normal_at_center(raw_a)
                n_b = _face_normal_at_center(raw_b)
                # 곡면이면 (0,0,0)
                if n_a == (0.0, 0.0, 0.0) or n_b == (0.0, 0.0, 0.0):
                    raise ValueError(
                        "angle measurement requires planar faces (curved face normal undefined at center)"
                    )
                v_a, v_b = n_a, n_b
            elif ekind_a == "edge" and ekind_b == "edge":
                v_a = _edge_tangent_at_mid(raw_a)
                v_b = _edge_tangent_at_mid(raw_b)
            else:
                raise ValueError(
                    f"angle measurement requires two faces or two edges, "
                    f"got entity_a={ekind_a}, entity_b={ekind_b}"
                )

            dot = v_a[0] * v_b[0] + v_a[1] * v_b[1] + v_a[2] * v_b[2]
            # clamp
            dot = max(-1.0, min(1.0, dot))
            ang = math.degrees(math.acos(dot))
            extras["value_deg"] = round(ang, 6)
            extras["value_mm"] = None
            extras["kind"] = "angle_deg"

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
