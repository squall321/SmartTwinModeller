"""mate_axis — atomic assembly constraint.

두 component 의 (직선) edge 가 공선이 되도록 component_a 를 강체 변환한다.
shaft 의 중심선 edge 를 hole 의 axis edge 와 정렬하는 등.

Algorithm
---------
- selector 로 각 component 의 첫 edge 를 얻는다.
- BRepAdaptor_Curve().Line() 으로 (point_on_line, unit_direction) 추출
  (linear edge 가 아니면 RuntimeError).
- gp_Ax3 SetDisplacement(ax_from→ax_to) 로 transform 만들어 component_a 에 적용.

Notes
-----
- 두 edge 의 길이 차이는 허용 (실제 axis 는 무한 직선).
- edge 가 곡선이면 mate_concentric 을 사용해야 한다.
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


def _resolve_one_edge(shape, selector):
    from phone_designer.skills._resolvers import resolve_edges

    edges = resolve_edges(shape, selector)
    if not edges:
        raise RuntimeError(
            f"mate_axis: selector matched 0 edges ({selector.model_dump()})"
        )
    return edges[0]


def _line_axis(edge) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """linear edge → (point_on_line, unit_direction).

    Falls back to (start_point, normalized end-start) when the curve is not a
    pure line — robust enough for poly-line edges that happen to be straight.
    Raises if the edge is degenerate.
    """
    import math

    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Line

    curve = BRepAdaptor_Curve(edge)
    if curve.GetType() == GeomAbs_Line:
        ln = curve.Line()
        loc = ln.Location()
        dir_ = ln.Direction()
        return (
            (loc.X(), loc.Y(), loc.Z()),
            (dir_.X(), dir_.Y(), dir_.Z()),
        )

    # Fallback: take start and end vertices.
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp

    v1 = TopExp.FirstVertex_s(edge)
    v2 = TopExp.LastVertex_s(edge)
    p1 = BRep_Tool.Pnt_s(v1)
    p2 = BRep_Tool.Pnt_s(v2)
    dx, dy, dz = p2.X() - p1.X(), p2.Y() - p1.Y(), p2.Z() - p1.Z()
    n = math.sqrt(dx * dx + dy * dy + dz * dz)
    if n < 1e-12:
        raise RuntimeError("mate_axis: edge is degenerate (zero length)")
    return ((p1.X(), p1.Y(), p1.Z()), (dx / n, dy / n, dz / n))


def _axis_transform(
    point_a: tuple[float, float, float],
    dir_a: tuple[float, float, float],
    point_b: tuple[float, float, float],
    dir_b: tuple[float, float, float],
):
    """gp_Trsf aligning axis_a to axis_b (same direction)."""
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
    name="mate_axis",
    category="assembly",
    level="atomic",
    summary="Axis mate: align two linear edges so their (infinite) axes coincide. "
            "Component_a is rigidly transformed; component_b is preserved.",
    selector_kinds=["edges"],
    history_rules={"mated_component": HistoryRule.MODIFIED_INHERIT},
    produces_features=["axis_mate"],
    preserves=["assembly_topology"],
    manufacturing={},
    failure_modes=[
        "fm.component_not_found",
        "fm.non_linear_edge",
        "fm.selector_no_match",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class MateAxis(SkillBase):
    class Args(BaseModel):
        component_a: str = Field(min_length=1)
        edge_selector_a: SelectorRef
        component_b: str = Field(min_length=1)
        edge_selector_b: SelectorRef

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        if body is None:
            raise RuntimeError("mate_axis: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        prior_names = get_component_names(body)
        components = list(iter_compound_components(shape, prior_names))
        names = [n for n, _ in components]
        shapes = [s for _, s in components]

        if args.component_a not in names:
            raise RuntimeError(
                f"mate_axis: component_a '{args.component_a}' not found "
                f"(known: {names})"
            )
        if args.component_b not in names:
            raise RuntimeError(
                f"mate_axis: component_b '{args.component_b}' not found "
                f"(known: {names})"
            )
        ia = names.index(args.component_a)
        ib = names.index(args.component_b)
        if ia == ib:
            raise RuntimeError(
                "mate_axis: component_a and component_b must be different"
            )

        edge_a = _resolve_one_edge(shapes[ia], args.edge_selector_a)
        edge_b = _resolve_one_edge(shapes[ib], args.edge_selector_b)

        pa, da = _line_axis(edge_a)
        pb, db = _line_axis(edge_b)

        trsf = _axis_transform(pa, da, pb, db)
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
