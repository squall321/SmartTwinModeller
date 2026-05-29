"""PF-2 PoC — Plan 결정성.

검증 목표:
  1. 같은 입력 (Box + fillet) 을 5회 실행 → 결과 동일성
  2. face count / edge count / volume / bbox 가 모두 일치
  3. topology_signature (정렬된 entity 튜플의 sha256) 도 일치

이 PoC 는 PF-1 의 fillet_with_history 를 재사용해 fillet 까지 적용 후 결정성 검증.

집 컴에서:
    pytest tests/poc/determinism.py -v
"""
from __future__ import annotations

import hashlib
import json

import pytest

from build123d import Box, Align
from OCP.BRep import BRep_Tool
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

from .test_persistent_naming import (
    fillet_with_history,
    _faces,
    _edges,
    _edge_length,
)


def _volume(shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def _bbox(shape) -> tuple:
    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb, True)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return (
        round(xmin, 6), round(ymin, 6), round(zmin, 6),
        round(xmax, 6), round(ymax, 6), round(zmax, 6),
    )


def _face_center(face) -> tuple:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    c = props.CentreOfMass()
    return (round(c.X(), 3), round(c.Y(), 3), round(c.Z(), 3))


def _face_area(face) -> float:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return round(props.Mass(), 3)


def topology_signature(shape) -> str:
    """[[plan-determinism#topology_signature-계산식]] 에 정의된 알고리즘."""
    items = []
    for f in _faces(shape):
        items.append(("face", _face_center(f), _face_area(f)))
    for e in _edges(shape):
        v1 = TopExp.FirstVertex_s(e)
        v2 = TopExp.LastVertex_s(e)
        p1 = BRep_Tool.Pnt_s(v1)
        p2 = BRep_Tool.Pnt_s(v2)
        center = (
            round((p1.X() + p2.X()) / 2, 3),
            round((p1.Y() + p2.Y()) / 2, 3),
            round((p1.Z() + p2.Z()) / 2, 3),
        )
        items.append(("edge", center, round(_edge_length(e), 3)))
    items.sort()
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()[:16]


def build_test_shape():
    """결정성 검증용 표준 형상: Box + top 4 edges fillet."""
    box = Box(20, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    box_shape = box.wrapped
    top_edges = []
    for e in _edges(box_shape):
        v1 = TopExp.FirstVertex_s(e)
        v2 = TopExp.LastVertex_s(e)
        p1 = BRep_Tool.Pnt_s(v1)
        p2 = BRep_Tool.Pnt_s(v2)
        if abs(p1.Z() - 10) < 0.01 and abs(p2.Z() - 10) < 0.01:
            top_edges.append(e)
    new_shape, _history = fillet_with_history(box_shape, top_edges, radius=1.5)
    return new_shape


def test_same_machine_5x_identical():
    """같은 머신에서 5회 빌드 → 결과 동일."""
    results = []
    for i in range(5):
        shape = build_test_shape()
        results.append({
            "face_count": len(_faces(shape)),
            "edge_count": len(_edges(shape)),
            "volume": round(_volume(shape), 6),
            "bbox": _bbox(shape),
            "signature": topology_signature(shape),
        })

    base = results[0]
    for i, r in enumerate(results[1:], start=1):
        assert r["face_count"] == base["face_count"], \
            f"run {i}: face count {r['face_count']} != {base['face_count']}"
        assert r["edge_count"] == base["edge_count"], \
            f"run {i}: edge count {r['edge_count']} != {base['edge_count']}"
        assert r["volume"] == base["volume"], \
            f"run {i}: volume {r['volume']} != {base['volume']}"
        assert r["bbox"] == base["bbox"], \
            f"run {i}: bbox {r['bbox']} != {base['bbox']}"
        assert r["signature"] == base["signature"], \
            f"run {i}: signature {r['signature']} != {base['signature']}"


def test_topology_signature_stable_across_rebuilds():
    """signature 가 결정적."""
    sigs = [topology_signature(build_test_shape()) for _ in range(5)]
    assert len(set(sigs)) == 1, f"signatures 불일치: {set(sigs)}"


def test_signature_differs_when_geometry_differs():
    """다른 형상 → 다른 signature."""
    shape_a = build_test_shape()  # fillet R=1.5
    box = Box(20, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    top_edges = []
    for e in _edges(box.wrapped):
        v1 = TopExp.FirstVertex_s(e)
        v2 = TopExp.LastVertex_s(e)
        p1 = BRep_Tool.Pnt_s(v1)
        p2 = BRep_Tool.Pnt_s(v2)
        if abs(p1.Z() - 10) < 0.01 and abs(p2.Z() - 10) < 0.01:
            top_edges.append(e)
    shape_b, _ = fillet_with_history(box.wrapped, top_edges, radius=2.5)  # 다른 R

    sig_a = topology_signature(shape_a)
    sig_b = topology_signature(shape_b)
    assert sig_a != sig_b, f"signature 가 다른 형상에서 같음: {sig_a}"


if __name__ == "__main__":
    print(">>> PF-2 PoC: plan determinism — same-machine 5x identical")
    test_same_machine_5x_identical()
    print("    same_machine_5x_identical  ok")
    test_topology_signature_stable_across_rebuilds()
    print("    topology_signature_stable_across_rebuilds  ok")
    test_signature_differs_when_geometry_differs()
    print("    signature_differs_when_geometry_differs  ok")
    print(">>> ALL PASS")
