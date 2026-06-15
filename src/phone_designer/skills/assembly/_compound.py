"""Compound utilities for assembly skills.

이 helper 는 ``TopoDS_Compound`` 를 multi-body container 로 다루는 데 필요한
저수준 OCCT 호출들을 모은다. 모든 assembly skill 이 이 모듈을 통해 raw OCCT
와 상호작용해서 코드 중복을 줄인다.
"""
from __future__ import annotations

import math
from typing import Any, Iterable


def load_step_shape(path: str):
    """STEP 파일 → OCCT TopoDS_Shape."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    status = reader.ReadFile(path)
    if status != IFSelect_RetDone:
        raise RuntimeError(f"STEP parse failed: {path} (status={status})")
    reader.TransferRoots()
    return reader.OneShape()


def make_translation_trsf(tx: float, ty: float, tz: float):
    """gp_Trsf with pure translation."""
    from OCP.gp import gp_Trsf, gp_Vec
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(tx, ty, tz))
    return trsf


def make_axis_rotation_trsf(ax: float, ay: float, az: float, theta_rad: float):
    """gp_Trsf with rotation about axis (ax,ay,az) through origin by theta (radians).

    axis 가 영벡터면 RuntimeError. caller 가 사전 검사할 것.
    """
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf
    n = math.sqrt(ax * ax + ay * ay + az * az)
    if n < 1e-12:
        raise RuntimeError("rotation axis is a zero vector")
    direction = gp_Dir(ax / n, ay / n, az / n)
    axis = gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), direction)
    trsf = gp_Trsf()
    trsf.SetRotation(axis, theta_rad)
    return trsf


def apply_transform_shape(shape, trsf):
    """BRepBuilderAPI_Transform(shape, trsf, True).Shape() (deep copy)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def iter_compound_components(shape, prior_names: list[str]):
    """기존 body 에서 (name, sub_shape) 쌍 yield.

    - shape 가 Compound 면 SOLID/COMPSOLID/COMPOUND 자식 들을 순회.
    - shape 가 Compound 가 아니면 (예: 단일 Solid) 통째로 1개 component 로
      취급. prior_names 가 있으면 첫 이름 사용, 없으면 "base".
    - prior_names 길이가 부족하면 자동으로 ``comp_{i}`` 채움.
    """
    from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_SOLID, TopAbs_COMPSOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Iterator

    if shape is None:
        return

    is_compound = (shape.ShapeType() == TopAbs_COMPOUND)
    if not is_compound:
        name = prior_names[0] if prior_names else "base"
        yield (name, shape)
        return

    # Direct top-level iteration (가장 자연스러운 순서 — Add 한 순서대로)
    it = TopoDS_Iterator(shape)
    idx = 0
    while it.More():
        sub = it.Value()
        if idx < len(prior_names):
            name = prior_names[idx]
        else:
            name = f"comp_{idx}"
        yield (name, sub)
        idx += 1
        it.Next()


def iter_solid_components(shape):
    """SOLID-aware explode: yield every constituent ``TopoDS_Solid``.

    pillar-perf (phase-4, 2026-06-15): a STEP assembly arrives as a single
    ``TopoDS_Compound`` whose leaves are SOLIDs (one per physical body). The
    historic ``split_into_components`` enumerates *shells* — but a solid with
    an internal cavity owns TWO shells (outer + void), so a shell-split
    over-counts and slices one physical body into two bogus "components"
    (RC_Buggy: 211 solids → 293 shells). Splitting by SOLID gives one closed,
    rigid-motion-coherent body per physical part, which is what dedup +
    per-component RE actually want.

    ``TopExp_Explorer(shape, TopAbs_SOLID)`` recurses through nested
    sub-assembly compounds automatically, so a 40-top-level-kid compound of
    sub-assemblies still yields all 211 leaf solids.

    Yields:
        ``TopoDS_Solid`` instances (already down-cast). Empty if the shape
        carries no solids (e.g. a pure open-shell mesh → caller should fall
        back to the shell split).
    """
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    if shape is None:
        return
    ex = TopExp_Explorer(shape, TopAbs_SOLID)
    while ex.More():
        yield TopoDS.Solid_s(ex.Current())
        ex.Next()


def count_solids(shape) -> int:
    """Number of constituent SOLIDs (cheap topology count, no geometry)."""
    n = 0
    for _ in iter_solid_components(shape):
        n += 1
    return n


def build_compound(shapes: Iterable):
    """주어진 sub-shape list → 새 TopoDS_Compound."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    for s in shapes:
        builder.Add(comp, s)
    return comp


def get_component_names(body: Any) -> list[str]:
    """body 의 ``_pd_component_names`` attribute (있으면) — 없으면 빈 list."""
    if body is None:
        return []
    return list(getattr(body, "_pd_component_names", []) or [])


def find_component(body: Any, name: str):
    """body (compound) 안에서 name 으로 component 찾기.

    Returns:
        (idx, sub_shape) or (None, None) if not found.
    """
    if body is None:
        return None, None
    shape = body.wrapped if hasattr(body, "wrapped") else body
    names = get_component_names(body)
    components = list(iter_compound_components(shape, names))
    for i, (nm, sub) in enumerate(components):
        if nm == name:
            return i, sub
    return None, None


def list_components(body: Any) -> list[tuple[str, Any]]:
    """body 의 모든 (name, sub_shape) list."""
    if body is None:
        return []
    shape = body.wrapped if hasattr(body, "wrapped") else body
    names = get_component_names(body)
    return list(iter_compound_components(shape, names))
