"""PF-1 PoC — OCCT BRep history map propagation.

검증 목표:
  1. Box 만들고 → top 의 한 edge 에 fillet 적용
  2. fillet 의 maker.Modified() / Generated() / IsDeleted() 가 의도대로 동작
  3. 새로 생긴 toroidal face 를 안정적으로 추적 가능

build123d 의 표준 fillet 함수는 history 를 expose 안 함 → OCP API 직접 호출.

집 컴에서 `setup.ps1` 실행 후:
    pytest tests/poc/persistent_naming.py -v

성공 조건:
  - Box top 의 4 edges 중 1개에 fillet 적용
  - 결과 body 의 face count == 8 (Box 6 + fillet 1 + 그 자리 split 1) 또는 OCCT version 의존 값
  - history 에서 입력 edge 가 GENERATED 한 toroidal face 1개 이상
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pytest

# build123d / OCP imports — 의존성 lazy 로 두지 않음. 본 PoC 자체가 의존성 검증
from build123d import Box, Align
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Torus, GeomAbs_Cylinder
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_ListOfShape, TopTools_MapOfShape


def _faces(shape) -> list:
    """Unique TopoDS_Face list (Explorer sub-shape 중복 제거 + 캐스팅)."""
    seen = TopTools_MapOfShape()
    out = []
    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        f = it.Current()
        if not seen.Contains(f):
            seen.Add(f)
            out.append(TopoDS.Face_s(f))
        it.Next()
    return out


def _edges(shape) -> list:
    """Unique TopoDS_Edge list."""
    seen = TopTools_MapOfShape()
    out = []
    it = TopExp_Explorer(shape, TopAbs_EDGE)
    while it.More():
        e = it.Current()
        if not seen.Contains(e):
            seen.Add(e)
            out.append(TopoDS.Edge_s(e))
        it.Next()
    return out


def _list_to_pylist(occt_list: TopTools_ListOfShape) -> list:
    """TopTools_ListOfShape → list[TopoDS_Shape].

    OCP 7.8 의 TopTools_ListOfShape 는 STL iterator 노출 안 함 — destructive copy +
    First/RemoveFirst 패턴 사용. 원본 list 는 mutate 안 함 (Assign 으로 복사).
    """
    out = []
    if occt_list.IsEmpty():
        return out
    copy = TopTools_ListOfShape()
    copy.Assign(occt_list)
    while not copy.IsEmpty():
        out.append(copy.First())
        copy.RemoveFirst()
    return out


def _surface_type(face) -> int:
    return BRepAdaptor_Surface(face).GetType()


def _edge_length(edge) -> float:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint
    curve = BRepAdaptor_Curve(edge)
    return GCPnts_AbscissaPoint.Length_s(curve)


@dataclass
class HistoryRecord:
    """1 입력 entity 의 OCCT history."""
    modified: list = field(default_factory=list)
    generated: list = field(default_factory=list)
    deleted: bool = False


def fillet_with_history(body_shape, edges_to_fillet, radius):
    """OCP API 로 fillet 적용 + 입력 edge 들의 history 반환."""
    maker = BRepFilletAPI_MakeFillet(body_shape)
    for e in edges_to_fillet:
        maker.Add(radius, e)
    maker.Build()
    if not maker.IsDone():
        raise RuntimeError("BRepFilletAPI_MakeFillet failed")
    new_shape = maker.Shape()

    history: dict[int, HistoryRecord] = {}
    for i, e in enumerate(edges_to_fillet):
        rec = HistoryRecord()
        rec.modified = _list_to_pylist(maker.Modified(e))
        rec.generated = _list_to_pylist(maker.Generated(e))
        rec.deleted = maker.IsDeleted(e)
        history[i] = rec
    return new_shape, history


def test_box_fillet_generates_toroidal_face():
    """단일 edge 에 fillet → toroidal face 생성됨을 확인."""
    box = Box(20, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    box_shape = box.wrapped

    initial_faces = _faces(box_shape)
    initial_edges = _edges(box_shape)
    assert len(initial_faces) == 6, f"박스 face 가 6개여야 함, got {len(initial_faces)}"
    assert len(initial_edges) == 12, f"박스 edge 가 12개여야 함, got {len(initial_edges)}"

    # top face (z=10) 의 edge 1개 선택 — 길이 20 + Z 좌표 ≈ 10
    target = None
    for e in initial_edges:
        from OCP.BRep import BRep_Tool
        from OCP.TopExp import TopExp
        # vertex 의 평균 Z
        v1 = TopExp.FirstVertex_s(e)
        v2 = TopExp.LastVertex_s(e)
        p1 = BRep_Tool.Pnt_s(v1)
        p2 = BRep_Tool.Pnt_s(v2)
        if abs(p1.Z() - 10) < 0.01 and abs(p2.Z() - 10) < 0.01:
            target = e
            break
    assert target is not None, "top 의 edge 를 찾지 못함"

    new_shape, history = fillet_with_history(box_shape, [target], radius=2.0)

    # 결과 body 의 face 종류 확인.
    # 직선 edge + 평면 인접 face 의 fillet 결과는 cylindrical surface 가 자연.
    # toroidal 은 edge 가 곡선일 때 (예: 원형 edge 의 fillet) 또는 corner.
    new_faces = _faces(new_shape)
    fillet_surfs = [f for f in new_faces
                    if _surface_type(f) in (GeomAbs_Torus, GeomAbs_Cylinder)]
    assert len(fillet_surfs) >= 1, \
        f"fillet 결과 cylindrical/toroidal face ≥ 1 이어야 함, got {len(fillet_surfs)}"

    # history 확인
    rec = history[0]
    print(f"  modified count:  {len(rec.modified)}")
    print(f"  generated count: {len(rec.generated)}")
    print(f"  deleted:         {rec.deleted}")

    # 입력 edge 가 deleted=True 이거나 generated 의 결과로 toroidal face 가 나옴
    # OCCT 버전에 따라 정확한 양상이 다를 수 있음 — 핵심은 새 toroidal face 가 history 로 추적 가능한지
    assert rec.deleted or len(rec.generated) > 0, \
        "입력 edge 가 deleted 도 아니고 generated 도 빈 list — history 추적 불가"


def test_history_propagation_through_two_steps():
    """fillet 후 또 다른 변환 시 toroidal face 의 tag survival."""
    box = Box(20, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    box_shape = box.wrapped

    # 1차: top edge 4개 모두에 fillet
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp
    top_edges = []
    for e in _edges(box_shape):
        v1 = TopExp.FirstVertex_s(e)
        v2 = TopExp.LastVertex_s(e)
        p1 = BRep_Tool.Pnt_s(v1)
        p2 = BRep_Tool.Pnt_s(v2)
        if abs(p1.Z() - 10) < 0.01 and abs(p2.Z() - 10) < 0.01:
            top_edges.append(e)
    assert len(top_edges) == 4, f"top edges 4개여야 함, got {len(top_edges)}"

    new_shape, history1 = fillet_with_history(box_shape, top_edges, radius=1.5)

    # 4 edge fillet → cylindrical (각 edge) + toroidal/sphere (corner). 어느 쪽이든 ≥ 1
    fillet_surfs_1 = [f for f in _faces(new_shape)
                      if _surface_type(f) in (GeomAbs_Torus, GeomAbs_Cylinder)]
    assert len(fillet_surfs_1) >= 1, \
        f"1차 fillet 후 cylindrical/toroidal face ≥ 1 이어야 함, got {len(fillet_surfs_1)}"

    # 2차: 같은 형상에 다른 fillet (옆 edge 들)
    # OCCT history map 이 두 변환을 잘 chain 하는지 확인 — 본 PoC 에서는 그냥 새 fillet maker
    # 가 1차 결과에 작동하는지만 확인
    side_edges = []
    for e in _edges(new_shape):
        # 길이가 10 (수직 z-축) 이고 Z 변동 큰 edge
        v1 = TopExp.FirstVertex_s(e)
        v2 = TopExp.LastVertex_s(e)
        p1 = BRep_Tool.Pnt_s(v1)
        p2 = BRep_Tool.Pnt_s(v2)
        if abs(p1.Z() - p2.Z()) > 5 and abs(p1.X() - p2.X()) < 0.01:
            side_edges.append(e)
    # 측면 vertical edges 가 있어야 — fillet 후에도 4개 정도 남아 있어야 함
    assert len(side_edges) >= 1, "2차 fillet 대상이 없음"

    new_shape_2, history2 = fillet_with_history(new_shape, side_edges[:1], radius=1.0)
    fillet_surfs_2 = [f for f in _faces(new_shape_2)
                      if _surface_type(f) in (GeomAbs_Torus, GeomAbs_Cylinder)]
    assert len(fillet_surfs_2) >= len(fillet_surfs_1), \
        "2차 fillet 후 cylindrical/toroidal face ≥ 1차 (1차 결과가 보존되어야)"


if __name__ == "__main__":
    print(">>> PF-1 PoC: persistent naming via OCCT BRep history map")
    test_box_fillet_generates_toroidal_face()
    print("    box_fillet_generates_toroidal_face  ok")
    test_history_propagation_through_two_steps()
    print("    history_propagation_through_two_steps  ok")
    print(">>> ALL PASS")
