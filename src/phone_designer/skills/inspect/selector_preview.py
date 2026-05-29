"""selector_preview — atomic, read-only.

Selector 를 dry-run 해서 어떤 face/edge 가 매칭되는지 진단. LLM 가 plan 의
selector 를 만들면서 "이 selector 가 정말 0 개 매칭이냐 / 너무 많이 잡냐"
를 확인할 때 쓴다.

zero match / NotImplementedError / 잘못된 selector kind 같은 에러 경우에도
**예외를 던지지 않고** extras 에 진단 정보로 담는다 — LLM 가 메시지를 받고
다음 시도를 짜는 게 목적이므로.

result.extras schema:
    {"selector": serialized_selector_dict,
     "kind": "faces" | "edges",
     "match_count": int,
     "matched_entities": [
         {"index": i, "center": [x,y,z],
          "area"|"length": float, "surface_type"|"curve_type": str},
         ...
     ],
     "error": str | None,        # exception 발생 시 메시지
     "truncated": bool}          # max_entities 로 잘렸는지
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


# OCCT GeomAbs surface / curve type → string
def _surface_type_name(face) -> str:
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import (
            GeomAbs_BezierSurface, GeomAbs_BSplineSurface, GeomAbs_Cone,
            GeomAbs_Cylinder, GeomAbs_OffsetSurface, GeomAbs_OtherSurface,
            GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_SurfaceOfExtrusion,
            GeomAbs_SurfaceOfRevolution, GeomAbs_Torus,
        )
        mp = {
            GeomAbs_Plane: "plane",
            GeomAbs_Cylinder: "cylinder",
            GeomAbs_Cone: "cone",
            GeomAbs_Sphere: "sphere",
            GeomAbs_Torus: "torus",
            GeomAbs_BSplineSurface: "bspline",
            GeomAbs_BezierSurface: "bezier",
            GeomAbs_OffsetSurface: "offset",
            GeomAbs_SurfaceOfExtrusion: "extrusion",
            GeomAbs_SurfaceOfRevolution: "revolution",
            GeomAbs_OtherSurface: "other",
        }
        surf = BRepAdaptor_Surface(face)
        return mp.get(int(surf.GetType()), "unknown")
    except Exception:
        return "unknown"


def _curve_type_name(edge) -> str:
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import (
            GeomAbs_BezierCurve, GeomAbs_BSplineCurve, GeomAbs_Circle,
            GeomAbs_Ellipse, GeomAbs_Hyperbola, GeomAbs_Line,
            GeomAbs_OffsetCurve, GeomAbs_OtherCurve, GeomAbs_Parabola,
        )
        mp = {
            GeomAbs_Line: "line",
            GeomAbs_Circle: "circle",
            GeomAbs_Ellipse: "ellipse",
            GeomAbs_Hyperbola: "hyperbola",
            GeomAbs_Parabola: "parabola",
            GeomAbs_BezierCurve: "bezier",
            GeomAbs_BSplineCurve: "bspline",
            GeomAbs_OffsetCurve: "offset",
            GeomAbs_OtherCurve: "other",
        }
        curve = BRepAdaptor_Curve(edge)
        return mp.get(int(curve.GetType()), "unknown")
    except Exception:
        return "unknown"


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _selector_to_dict(s: SelectorBase) -> dict:
    """Selector → JSON-serializable dict (Pydantic model_dump)."""
    try:
        return s.model_dump(mode="json")
    except Exception:
        # last-resort
        return {"kind": getattr(s, "kind", "unknown")}


@skill(
    name="selector_preview",
    category="inspect",
    level="atomic",
    summary="Dry-run a selector against the body and report what it matched "
            "(count + per-entity center/area/length/surface_type). On any "
            "error (zero match, NotImplementedError, etc.) returns "
            "diagnostic info instead of crashing.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "axis_aligned_edges", "edges_on_face", "edges_by_length",
        "edges_by_position", "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["selector_match_summary"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class SelectorPreview(SkillBase):
    class Args(BaseModel):
        selector: dict
        kind: Literal["faces", "edges"]
        max_entities: int = Field(default=50, ge=1, le=10000)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            _edge_endpoints, _edge_length, _face_area, _face_center,
            resolve_edges, resolve_faces,
        )

        shape = _occt_shape(body)
        extras: dict[str, Any] = {
            "kind": args.kind,
            "match_count": 0,
            "matched_entities": [],
            "error": None,
            "truncated": False,
        }

        # Selector parse — invalid kind 면 친절한 에러 메시지로
        try:
            sel = _coerce_selector(args.selector)
            extras["selector"] = _selector_to_dict(sel)
        except Exception as ex:
            extras["selector"] = args.selector
            extras["error"] = f"selector parse failed: {type(ex).__name__}: {ex}"
            return SkillResult(
                body=body, history=EntityHistoryMap(), extras=extras,
            )

        # Resolve
        try:
            if args.kind == "faces":
                entities = resolve_faces(shape, sel, body=body)
            else:
                entities = resolve_edges(shape, sel)
        except NotImplementedError as ex:
            extras["error"] = f"selector resolver not implemented: {ex}"
            return SkillResult(
                body=body, history=EntityHistoryMap(), extras=extras,
            )
        except Exception as ex:
            extras["error"] = f"selector resolve failed: {type(ex).__name__}: {ex}"
            return SkillResult(
                body=body, history=EntityHistoryMap(), extras=extras,
            )

        n = len(entities)
        extras["match_count"] = n
        if n > args.max_entities:
            extras["truncated"] = True
            entities = entities[: args.max_entities]

        rows: list[dict] = []
        if args.kind == "faces":
            for i, f in enumerate(entities):
                try:
                    c = _face_center(f)
                except Exception:
                    c = (0.0, 0.0, 0.0)
                try:
                    a = _face_area(f)
                except Exception:
                    a = 0.0
                rows.append({
                    "index": i,
                    "center": [round(c[0], 4), round(c[1], 4), round(c[2], 4)],
                    "area_mm2": round(a, 4),
                    "surface_type": _surface_type_name(f),
                })
        else:
            for i, e in enumerate(entities):
                try:
                    p1, p2 = _edge_endpoints(e)
                    cx = (p1.X() + p2.X()) / 2
                    cy = (p1.Y() + p2.Y()) / 2
                    cz = (p1.Z() + p2.Z()) / 2
                except Exception:
                    cx, cy, cz = 0.0, 0.0, 0.0
                try:
                    L = _edge_length(e)
                except Exception:
                    L = 0.0
                rows.append({
                    "index": i,
                    "center": [round(cx, 4), round(cy, 4), round(cz, 4)],
                    "length_mm": round(L, 4),
                    "curve_type": _curve_type_name(e),
                })
        extras["matched_entities"] = rows

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
