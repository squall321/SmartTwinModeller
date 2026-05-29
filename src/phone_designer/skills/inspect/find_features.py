"""find_features — atomic, read-only.

휴리스틱 feature detector. LLM 가 "이 body 에 hole 이 어디 있냐 / fillet 이
어느 모서리에 걸렸냐" 같은 hint 를 받을 때 사용. 정밀 ground truth 는
``reference/topology_analyzer.py`` 가 담당; 본 skill 은 가벼운 휴리스틱이다.

분류 규칙 (Phase 4 v1 휴리스틱):
    hole     — cylindrical face. radius, axis, center.
    pocket   — body bbox 안쪽에 위치하고 normal 이 안쪽을 향하는 planar face.
    fillet   — toroidal face (작은 minor radius) 또는 인접 planar 사이 cylinder.
    chamfer  — 인접 평면 두 개와 각도 ≠ 0,90 인 planar face (휴리스틱 — pair 검사 생략).
    boss / rib — skipped (return empty list).

result.extras schema:
    {"features": [
        {"kind": "hole", "center": [x,y,z], "radius_mm": float, "axis": [x,y,z]},
        {"kind": "pocket", "center": [x,y,z], "area_mm2": float, "normal": [...]},
        {"kind": "fillet", "center": [x,y,z], "radius_mm": float, "surface_type": "torus|cylinder"},
        {"kind": "chamfer", "center": [x,y,z], "area_mm2": float, "normal": [...]},
        ...
    ],
     "notes": [str, ...]}     # boss/rib 같은 punt 알림
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


FeatureKind = Literal["hole", "pocket", "fillet", "chamfer", "boss", "rib"]


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _body_bbox(shape) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def _detect_hole(face) -> dict | None:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cylinder:
        return None
    cyl = surf.Cylinder()
    axis = cyl.Axis().Direction()
    loc = cyl.Location()
    r = float(cyl.Radius())

    from phone_designer.skills._resolvers import _face_center
    c = _face_center(face)

    return {
        "kind": "hole",
        "center": [round(c[0], 4), round(c[1], 4), round(c[2], 4)],
        "radius_mm": round(r, 4),
        "axis": [round(axis.X(), 4), round(axis.Y(), 4), round(axis.Z(), 4)],
        "axis_origin": [round(loc.X(), 4), round(loc.Y(), 4), round(loc.Z(), 4)],
    }


def _detect_fillet(face) -> dict | None:
    """Toroidal face 또는 작은 cylinder 사이 fillet 휴리스틱.

    1) Torus : minor radius 가 major 의 절반 이하면 fillet 으로 분류.
    2) Cylinder : 본 휴리스틱에서는 hole 과 구분이 어려워 fillet 분류는 skip.
       (hole 검출이 우선.)
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Torus

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Torus:
        return None
    t = surf.Torus()
    minor = float(t.MinorRadius())
    major = float(t.MajorRadius())

    from phone_designer.skills._resolvers import _face_center
    c = _face_center(face)

    return {
        "kind": "fillet",
        "center": [round(c[0], 4), round(c[1], 4), round(c[2], 4)],
        "radius_mm": round(minor, 4),
        "major_radius_mm": round(major, 4),
        "surface_type": "torus",
    }


def _detect_pocket(face, body_bbox, all_faces) -> dict | None:
    """Pocket 휴리스틱.

    Planar face 가 body bbox 의 strict interior 에 있고 (body 표면이 아니라
    내부 깊이를 갖는 cavity 의 base 라는 의미), face area 가 가장 큰 outer
    planar face 보다 작다면 pocket base 후보.

    구체적으로:
        - face 가 planar 여야 함
        - face center 가 body bbox 의 (각 축마다) 내부 (margin 0.5mm 이상)
          또는 face area 가 body 의 가장 큰 face 면적의 절반 이하
    """
    from phone_designer.skills._resolvers import (
        _face_area, _face_center, _face_normal_at_center,
    )

    n = _face_normal_at_center(face)
    if n == (0.0, 0.0, 0.0):
        return None  # 곡면 — pocket base 가 아님

    c = _face_center(face)
    (mn, mx) = body_bbox

    margin = 0.5
    inside_x = (mn[0] + margin) < c[0] < (mx[0] - margin)
    inside_y = (mn[1] + margin) < c[1] < (mx[1] - margin)
    inside_z = (mn[2] + margin) < c[2] < (mx[2] - margin)
    # 단순 휴리스틱: 적어도 한 축에서 strict interior 이고, 면적이 작아야 함.
    if not (inside_x or inside_y or inside_z):
        return None

    area = _face_area(face)
    # 비교: body 전체 face 중 최대 planar area
    max_planar = 0.0
    for f in all_faces:
        if _face_normal_at_center(f) == (0.0, 0.0, 0.0):
            continue
        a = _face_area(f)
        if a > max_planar:
            max_planar = a
    if max_planar > 0 and area > max_planar * 0.8:
        return None  # 외부 큰 face — pocket base 가 아닐 확률 높음

    return {
        "kind": "pocket",
        "center": [round(c[0], 4), round(c[1], 4), round(c[2], 4)],
        "area_mm2": round(area, 4),
        "normal": [round(v, 4) for v in n],
    }


def _detect_chamfer(face, body_bbox, all_faces) -> dict | None:
    """Chamfer 휴리스틱.

    Planar face 의 normal 이 body 축 (X/Y/Z) 과 일치하지 않으면 (즉 사선) chamfer.
    margin 5° 안에서 축에 align 하면 chamfer 가 아니라 본체 면.

    한계: 너무 단순. dome / loft sloped face 도 chamfer 로 잡힐 수 있음.
    """
    from phone_designer.skills._resolvers import (
        _face_area, _face_center, _face_normal_at_center,
    )

    n = _face_normal_at_center(face)
    if n == (0.0, 0.0, 0.0):
        return None

    # 축 align 여부
    axes = [(1.0, 0.0, 0.0), (-1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0), (0.0, -1.0, 0.0),
            (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)]
    tol = math.cos(math.radians(5.0))
    for ax in axes:
        dot = n[0] * ax[0] + n[1] * ax[1] + n[2] * ax[2]
        if dot >= tol:
            return None  # 축 align — 본체 면

    # 사선 planar — chamfer 후보. (실제로는 boss 의 sloped wall 일 수도 있음 —
    # 휴리스틱 한계.)
    c = _face_center(face)
    area = _face_area(face)
    return {
        "kind": "chamfer",
        "center": [round(c[0], 4), round(c[1], 4), round(c[2], 4)],
        "area_mm2": round(area, 4),
        "normal": [round(v, 4) for v in n],
    }


@skill(
    name="find_features",
    category="inspect",
    level="atomic",
    summary="Heuristically detect features (hole/pocket/fillet/chamfer) on "
            "the body. LLM hint, not ground truth. boss/rib are punted.",
    selector_kinds=[],
    history_rules={},
    produces_features=["feature_inventory"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class FindFeatures(SkillBase):
    class Args(BaseModel):
        kinds: list[FeatureKind] = Field(
            default_factory=lambda: ["hole", "pocket", "fillet", "chamfer"]
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        shape = _occt_shape(body)
        faces = _all_faces(shape)
        body_bbox = _body_bbox(shape)

        kinds = set(args.kinds)
        features: list[dict] = []
        notes: list[str] = []

        # 1) Hole — cylindrical faces
        if "hole" in kinds:
            for f in faces:
                feat = _detect_hole(f)
                if feat is not None:
                    features.append(feat)

        # 2) Fillet — toroidal faces
        if "fillet" in kinds:
            for f in faces:
                feat = _detect_fillet(f)
                if feat is not None:
                    features.append(feat)

        # 3) Pocket
        if "pocket" in kinds:
            for f in faces:
                feat = _detect_pocket(f, body_bbox, faces)
                if feat is not None:
                    features.append(feat)

        # 4) Chamfer
        if "chamfer" in kinds:
            for f in faces:
                feat = _detect_chamfer(f, body_bbox, faces)
                if feat is not None:
                    features.append(feat)

        # 5) boss / rib — punted
        if "boss" in kinds:
            notes.append("boss detection not implemented (Phase 4 v2)")
        if "rib" in kinds:
            notes.append("rib detection not implemented (Phase 4 v2)")

        extras = {
            "features": features,
            "notes": notes,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
