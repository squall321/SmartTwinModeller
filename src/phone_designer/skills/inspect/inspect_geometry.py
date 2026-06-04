"""inspect_geometry — atomic, read-only.

body 의 topology / volume / face inventory 를 측정해서 result.extras 에 dict 로
싣는다. body 자체는 손대지 않는다 (`post_conditions=[body_present]`).

LLM agent 가 "이 STEP 의 face 가 몇 개냐 / cylinder 가 어디 있냐 / bbox 가 얼마냐"
같은 질문을 답할 때 사용. 또는 plan 디버깅 (extrude_pocket 직후 face 가 늘었나
확인) 에도.

extras["inspection_report"] schema:
    {
      "volume_mm3": float,
      "bbox": {"min": [...], "max": [...], "size": [...]},
      "face_count": int,
      "edge_count": int,
      "faces": [{"index":i, "surface_type":..., "area_mm2":A,
                 "center":[x,y,z], "normal_at_center":[x,y,z]|None,
                 "is_outward": bool}, ...],
      "edges": [...] if include_edges else []
    }
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# GeomAbs surface type → string. enum value 가 OCP 빌드별로 변할 수 있으므로
# import 안에서 mapping 을 만든다 (모듈 임포트 시점에 한 번).
def _surface_type_map() -> dict[int, str]:
    from OCP.GeomAbs import (
        GeomAbs_BezierSurface,
        GeomAbs_BSplineSurface,
        GeomAbs_Cone,
        GeomAbs_Cylinder,
        GeomAbs_OffsetSurface,
        GeomAbs_OtherSurface,
        GeomAbs_Plane,
        GeomAbs_SurfaceOfExtrusion,
        GeomAbs_SurfaceOfRevolution,
        GeomAbs_Sphere,
        GeomAbs_Torus,
    )
    return {
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


def _surface_type_name(face) -> str:
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    surf = BRepAdaptor_Surface(face)
    stype_id = int(surf.GetType())
    return _surface_type_map().get(stype_id, f"unknown({stype_id})")


def _bbox(shape) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """OCCT optimal bbox — (min, max). Empty / null shape → ((0,0,0),(0,0,0))."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        try:
            BRepBndLib.Add_s(shape, bb)
        except Exception:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    if bb.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def _edge_endpoints(edge):
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp

    v1 = TopExp.FirstVertex_s(edge)
    v2 = TopExp.LastVertex_s(edge)
    return BRep_Tool.Pnt_s(v1), BRep_Tool.Pnt_s(v2)


def _edge_length(edge) -> float:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint

    return GCPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(edge))


def _is_outward_normal(face, center: tuple[float, float, float],
                        normal: tuple[float, float, float],
                        body_center: tuple[float, float, float]) -> bool:
    """body 의 중심에서 face center 로 향하는 벡터와 normal 의 dot 부호.

    Planar face 만 의미. 곡면 face 는 False 반환 (caller 가 normal=None 이면 skip).
    """
    if normal == (0.0, 0.0, 0.0):
        return False
    v = (center[0] - body_center[0],
         center[1] - body_center[1],
         center[2] - body_center[2])
    return (v[0] * normal[0] + v[1] * normal[1] + v[2] * normal[2]) > 0


def _build_report(
    body: Any, *,
    include_faces: bool,
    include_edges: bool,
    max_entities: int,
    bbox_only: bool,
) -> dict[str, Any]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from phone_designer.skills._resolvers import (
        _all_edges,
        _all_faces,
        _face_area,
        _face_center,
        _face_normal_at_center,
    )

    shape = body.wrapped if hasattr(body, "wrapped") else body

    # Volume — open shells with mixed face orientation produce a negative
    # integral. For solids the sign tells you nothing the user cares about,
    # so report the magnitude. Flag the body kind so callers know whether the
    # volume is "true" (closed solid) or "shell magnitude" (open shell).
    from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer

    body_kind = "other"
    if TopExp_Explorer(shape, TopAbs_SOLID).More():
        body_kind = "solid"
    elif TopExp_Explorer(shape, TopAbs_SHELL).More():
        body_kind = "shell"

    volume = 0.0
    raw_volume = 0.0
    try:
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        raw_volume = float(props.Mass())
        volume = abs(raw_volume)
    except Exception:
        volume = 0.0
        raw_volume = 0.0

    # Bbox
    (mn, mx) = _bbox(shape)
    size = (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])
    body_center = ((mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2)

    report: dict[str, Any] = {
        "volume_mm3": round(volume, 4),
        "raw_volume_mm3": round(raw_volume, 4),  # signed; negative ⇒ shell flip
        "body_kind": body_kind,
        "bbox": {
            "min": [round(c, 4) for c in mn],
            "max": [round(c, 4) for c in mx],
            "size": [round(c, 4) for c in size],
        },
    }

    # Shell-aware: TopExp_Explorer with TopAbs_FACE/EDGE enumerates faces from
    # any TopoDS_Shape (compound, solid, shell). Counting is cheap; do it even
    # in bbox_only so callers always get face_count / edge_count.
    faces = _all_faces(shape)
    edges = _all_edges(shape)
    report["face_count"] = len(faces)
    report["edge_count"] = len(edges)

    if bbox_only:
        report["faces"] = []
        report["edges"] = []
        return report

    face_entries: list[dict[str, Any]] = []
    if include_faces:
        for i, f in enumerate(faces[:max_entities]):
            try:
                stype = _surface_type_name(f)
            except Exception:
                stype = "unknown"
            try:
                center = _face_center(f)
            except Exception:
                center = (0.0, 0.0, 0.0)
            try:
                area = _face_area(f)
            except Exception:
                area = 0.0
            try:
                normal = _face_normal_at_center(f)
            except Exception:
                normal = (0.0, 0.0, 0.0)
            normal_out: list[float] | None = (
                [round(n, 4) for n in normal] if normal != (0.0, 0.0, 0.0) else None
            )
            face_entries.append({
                "index": i,
                "surface_type": stype,
                "area_mm2": round(area, 4),
                "center": [round(c, 4) for c in center],
                "normal_at_center": normal_out,
                "is_outward": _is_outward_normal(f, center, normal, body_center),
            })
    report["faces"] = face_entries

    edge_entries: list[dict[str, Any]] = []
    if include_edges:
        for i, e in enumerate(edges[:max_entities]):
            try:
                p1, p2 = _edge_endpoints(e)
                start = (p1.X(), p1.Y(), p1.Z())
                end = (p2.X(), p2.Y(), p2.Z())
                center = ((start[0] + end[0]) / 2,
                          (start[1] + end[1]) / 2,
                          (start[2] + end[2]) / 2)
            except Exception:
                start = end = center = (0.0, 0.0, 0.0)
            try:
                length = _edge_length(e)
            except Exception:
                length = 0.0
            edge_entries.append({
                "index": i,
                "length_mm": round(length, 4),
                "start": [round(c, 4) for c in start],
                "end": [round(c, 4) for c in end],
                "center": [round(c, 4) for c in center],
            })
    report["edges"] = edge_entries

    return report


@skill(
    name="inspect_geometry",
    category="inspect",
    level="atomic",
    summary="Read-only inspection of a body — volume, bbox, face/edge inventory "
            "with surface types and normals. Result is attached to "
            "result.extras['inspection_report']; body is returned unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["inspection_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    # Read-only — just ensure body still exists.
    post_conditions=[PostCondition(kind="body_present")],
)
class InspectGeometry(SkillBase):
    class Args(BaseModel):
        include_faces: bool = True
        include_edges: bool = False
        max_entities: int = Field(default=200, ge=1, le=10000)
        bbox_only: bool = False

    def _apply(self, body: Any, args: Args) -> SkillResult:
        report = _build_report(
            body,
            include_faces=args.include_faces,
            include_edges=args.include_edges,
            max_entities=args.max_entities,
            bbox_only=args.bbox_only,
        )
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"inspection_report": report},
        )
