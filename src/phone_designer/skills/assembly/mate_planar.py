"""mate_planar — atomic assembly constraint.

component_a 의 face_a 가 component_b 의 face_b 와 평면 (anti-parallel) 으로
정렬되고 ``offset_mm`` 만큼 face_b 의 outward normal 방향으로 떨어져 위치
하도록 component_a 에 rigid transform 을 적용한다.

Algorithm
---------
- 각 face 의 outward normal (orientation flag 반영) 과 surface centre-of-mass
  를 anchor 로 삼는다.
- gp_Ax3 (anchor_a, normal_a) → gp_Ax3 (anchor_b + offset*n_b, -n_b) 의
  SetDisplacement transformation 을 계산해 component_a 에 적용.
- compound 안의 다른 component 들은 그대로 보존.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.assembly._compound import (
    apply_transform_shape,
    build_compound,
    get_component_names,
    iter_compound_components,
)


def _face_center(face) -> tuple[float, float, float]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    p = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, p)
    c = p.CentreOfMass()
    return (c.X(), c.Y(), c.Z())


def _planar_outward_normal(face) -> tuple[float, float, float]:
    """outward normal of a planar face. 곡면이면 RuntimeError."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_REVERSED

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Plane:
        raise RuntimeError("mate_planar: target face is not planar")
    pl = surf.Plane()
    n = pl.Axis().Direction()
    nx, ny, nz = n.X(), n.Y(), n.Z()
    if face.Orientation() == TopAbs_REVERSED:
        nx, ny, nz = -nx, -ny, -nz
    return (nx, ny, nz)


def _resolve_one_face(shape, selector):
    """selector → first face (또는 RuntimeError)."""
    from phone_designer.skills._resolvers import resolve_faces

    faces = resolve_faces(shape, selector)
    if not faces:
        raise RuntimeError(
            f"mate_planar: selector matched 0 faces ({selector.model_dump()})"
        )
    return faces[0]


def _mate_transform(
    center_a: tuple[float, float, float],
    normal_a: tuple[float, float, float],
    center_b: tuple[float, float, float],
    normal_b: tuple[float, float, float],
    offset_mm: float,
):
    """gp_Trsf that moves face_a's frame onto face_b's frame (anti-parallel) + offset."""
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    target_pt = gp_Pnt(
        center_b[0] + offset_mm * normal_b[0],
        center_b[1] + offset_mm * normal_b[1],
        center_b[2] + offset_mm * normal_b[2],
    )
    target_dir = gp_Dir(-normal_b[0], -normal_b[1], -normal_b[2])
    ax_target = gp_Ax3(target_pt, target_dir)
    ax_from = gp_Ax3(
        gp_Pnt(center_a[0], center_a[1], center_a[2]),
        gp_Dir(normal_a[0], normal_a[1], normal_a[2]),
    )
    trsf = gp_Trsf()
    trsf.SetDisplacement(ax_from, ax_target)
    return trsf


@skill(
    name="mate_planar",
    category="assembly",
    level="atomic",
    summary="Planar mate: transform component_a so that face_a lies parallel to face_b "
            "(anti-parallel normals) at a given offset. Other components are preserved.",
    selector_kinds=["faces"],
    history_rules={"mated_component": HistoryRule.MODIFIED_INHERIT},
    produces_features=["planar_mate"],
    preserves=["assembly_topology"],
    manufacturing={},
    failure_modes=[
        "fm.component_not_found",
        "fm.non_planar_face",
        "fm.selector_no_match",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class MatePlanar(SkillBase):
    class Args(BaseModel):
        component_a: str = Field(min_length=1)
        face_selector_a: SelectorRef
        component_b: str = Field(min_length=1)
        face_selector_b: SelectorRef
        offset_mm: float = 0.0

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        if body is None:
            raise RuntimeError("mate_planar: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        prior_names = get_component_names(body)
        components = list(iter_compound_components(shape, prior_names))
        names = [n for n, _ in components]
        shapes = [s for _, s in components]

        if args.component_a not in names:
            raise RuntimeError(
                f"mate_planar: component_a '{args.component_a}' not found (known: {names})"
            )
        if args.component_b not in names:
            raise RuntimeError(
                f"mate_planar: component_b '{args.component_b}' not found (known: {names})"
            )
        ia = names.index(args.component_a)
        ib = names.index(args.component_b)
        if ia == ib:
            raise RuntimeError(
                "mate_planar: component_a and component_b must be different"
            )

        face_a = _resolve_one_face(shapes[ia], args.face_selector_a)
        face_b = _resolve_one_face(shapes[ib], args.face_selector_b)

        ca = _face_center(face_a)
        cb = _face_center(face_b)
        na = _planar_outward_normal(face_a)
        nb = _planar_outward_normal(face_b)

        trsf = _mate_transform(ca, na, cb, nb, args.offset_mm)
        new_a = apply_transform_shape(shapes[ia], trsf)

        new_shapes = list(shapes)
        new_shapes[ia] = new_a
        comp = build_compound(new_shapes)
        result_body = Part(comp)
        result_body._pd_component_names = list(names)

        history = EntityHistoryMap(
            rules={"mated_component": HistoryRule.MODIFIED_INHERIT},
        )
        return SkillResult(
            body=result_body,
            history=history,
            extras={"component_names": list(names)},
        )
