"""mate_concentric — atomic assembly constraint.

두 component 의 cylindrical face 가 동축이 되도록 component_a 를 강체 변환
(translate + rotate) 한다. 예: shaft 의 외경 surface 를 hole 의 내경 surface 와
정렬.

Algorithm
---------
- selector 로 component_a / component_b 의 첫 cylindrical face 를 얻는다.
- 각 face 의 BRepAdaptor_Surface().Cylinder() 로 axis (방향 + 한 점) 을 추출.
- gp_Ax1 (point_a, dir_a) → gp_Ax1 (point_b, dir_b) 의 SetDisplacement 로
  rigid transform 을 만들고 component_a 에 적용.
- 두 cylinder 의 반경은 일치 검사하지 않는다 (shaft 와 hole 은 의도적으로 다른
  반경). 사용자가 일관성을 책임진다.

Failure modes
-------------
- selector 가 face 0 개 매칭
- 매칭된 face 가 cylindrical 이 아님
- 같은 component 를 a/b 로 지정
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


def _resolve_one_face(shape, selector):
    from phone_designer.skills._resolvers import resolve_faces

    faces = resolve_faces(shape, selector)
    if not faces:
        raise RuntimeError(
            f"mate_concentric: selector matched 0 faces ({selector.model_dump()})"
        )
    return faces[0]


def _cylinder_axis(face) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """cylindrical face 의 (axis_point, axis_direction). 곡면이 cylinder 가 아니면 RuntimeError.

    Returns:
        (point_on_axis, unit_direction)
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cylinder:
        raise RuntimeError("mate_concentric: target face is not cylindrical")
    cyl = surf.Cylinder()
    ax = cyl.Axis()
    loc = ax.Location()
    dir_ = ax.Direction()
    return (
        (loc.X(), loc.Y(), loc.Z()),
        (dir_.X(), dir_.Y(), dir_.Z()),
    )


def _concentric_transform(
    point_a: tuple[float, float, float],
    dir_a: tuple[float, float, float],
    point_b: tuple[float, float, float],
    dir_b: tuple[float, float, float],
):
    """gp_Trsf that aligns axis_a onto axis_b (same direction).

    Uses gp_Ax3 SetDisplacement so we also fix the rotation about the axis
    (deterministic — derives the X reference from the gp_Ax3 default).
    """
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    ax_from = gp_Ax3(
        gp_Pnt(point_a[0], point_a[1], point_a[2]),
        gp_Dir(dir_a[0], dir_a[1], dir_a[2]),
    )
    ax_to = gp_Ax3(
        gp_Pnt(point_b[0], point_b[1], point_b[2]),
        gp_Dir(dir_b[0], dir_b[1], dir_b[2]),
    )
    trsf = gp_Trsf()
    trsf.SetDisplacement(ax_from, ax_to)
    return trsf


@skill(
    name="mate_concentric",
    category="assembly",
    level="atomic",
    summary="Concentric mate: align two cylindrical faces so their axes coincide. "
            "Component_a is rigidly transformed; component_b is preserved.",
    selector_kinds=["faces"],
    history_rules={"mated_component": HistoryRule.MODIFIED_INHERIT},
    produces_features=["concentric_mate"],
    preserves=["assembly_topology"],
    manufacturing={},
    failure_modes=[
        "fm.component_not_found",
        "fm.non_cylindrical_face",
        "fm.selector_no_match",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class MateConcentric(SkillBase):
    class Args(BaseModel):
        component_a: str = Field(min_length=1)
        face_selector_a: SelectorRef
        component_b: str = Field(min_length=1)
        face_selector_b: SelectorRef

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        if body is None:
            raise RuntimeError("mate_concentric: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        prior_names = get_component_names(body)
        components = list(iter_compound_components(shape, prior_names))
        names = [n for n, _ in components]
        shapes = [s for _, s in components]

        if args.component_a not in names:
            raise RuntimeError(
                f"mate_concentric: component_a '{args.component_a}' not found "
                f"(known: {names})"
            )
        if args.component_b not in names:
            raise RuntimeError(
                f"mate_concentric: component_b '{args.component_b}' not found "
                f"(known: {names})"
            )
        ia = names.index(args.component_a)
        ib = names.index(args.component_b)
        if ia == ib:
            raise RuntimeError(
                "mate_concentric: component_a and component_b must be different"
            )

        face_a = _resolve_one_face(shapes[ia], args.face_selector_a)
        face_b = _resolve_one_face(shapes[ib], args.face_selector_b)

        pa, da = _cylinder_axis(face_a)
        pb, db = _cylinder_axis(face_b)

        trsf = _concentric_transform(pa, da, pb, db)
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
