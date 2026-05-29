"""loft_side_profile — atomic. 두 단면 사이 측면 곡률 loft.

Phase 4 v1: 원형 + 원형 (cylinder-like) 또는 사각 + 사각 (slab-like). top/bottom
단면을 사용자가 지정. 두 단면 위치 + 크기로 loft.

create skill 처럼 새 body 생성 (기존 body 무시).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union as TUnion

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


class CircularSection(BaseModel):
    kind: Literal["circle"] = "circle"
    diameter_mm: float = Field(gt=0)
    z_mm: float = 0.0
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0


class RectangularSection(BaseModel):
    kind: Literal["rectangle"] = "rectangle"
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    z_mm: float = 0.0
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0


SectionSpec = Annotated[
    TUnion[CircularSection, RectangularSection],
    Field(discriminator="kind"),
]


@skill(
    name="loft_side_profile",
    category="modify/curvature",
    level="atomic",
    summary="Loft a solid between two 2D sections (top + bottom). v1 supports "
            "circle→circle, rectangle→rectangle. Sections must be same kind.",
    selector_kinds=[],
    history_rules={"lofted_solid": HistoryRule.GENERATED_NEW},
    produces_features=["lofted_solid"],
    preserves=[],
    manufacturing={
        "cnc_5axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.section_kinds_mismatch", "fm.loft_failed"],
    cost_hint=0.3,
    # Loft replaces the input body with a freshly lofted solid; the input
    # body is ignored entirely (essentially a create skill). body_present
    # is the only safe invariant.
    post_conditions=[PostCondition(kind="body_present")],
)
class LoftSideProfile(SkillBase):
    class Args(BaseModel):
        bottom: SectionSpec
        top: SectionSpec

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Align, Box, Cylinder, Part
        from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections

        if args.bottom.kind != args.top.kind:
            raise ValueError(
                f"loft: bottom kind={args.bottom.kind} != top kind={args.top.kind}"
            )

        # 두 단면의 wire 추출 + ThruSections
        bottom_wire = _section_to_wire(args.bottom)
        top_wire = _section_to_wire(args.top)

        maker = BRepOffsetAPI_ThruSections(True, False)  # isSolid=True, isRuled=False
        maker.AddWire(bottom_wire)
        maker.AddWire(top_wire)
        maker.Build()
        if not maker.IsDone():
            raise RuntimeError("loft_side_profile: ThruSections IsDone=False")

        history = EntityHistoryMap(
            rules={"lofted_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(maker.Shape()), history=history)


def _section_to_wire(spec):
    """SectionSpec → OCCT TopoDS_Wire."""
    from OCP.gp import gp_Pnt, gp_Vec, gp_Trsf, gp_Ax2, gp_Dir, gp_Circ, gp_Pln
    from OCP.GC import GC_MakeCircle
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire,
        BRepBuilderAPI_MakePolygon,
    )

    if spec.kind == "circle":
        # XY 평면 위 (z = z_mm) 의 원
        center = gp_Pnt(spec.center_x_mm, spec.center_y_mm, spec.z_mm)
        axis = gp_Ax2(center, gp_Dir(0, 0, 1))
        circ = gp_Circ(axis, spec.diameter_mm / 2)
        edge = BRepBuilderAPI_MakeEdge(circ).Edge()
        wire = BRepBuilderAPI_MakeWire(edge).Wire()
        return wire

    if spec.kind == "rectangle":
        cx, cy = spec.center_x_mm, spec.center_y_mm
        hl, hw = spec.length_mm / 2, spec.width_mm / 2
        p1 = gp_Pnt(cx - hl, cy - hw, spec.z_mm)
        p2 = gp_Pnt(cx + hl, cy - hw, spec.z_mm)
        p3 = gp_Pnt(cx + hl, cy + hw, spec.z_mm)
        p4 = gp_Pnt(cx - hl, cy + hw, spec.z_mm)
        poly = BRepBuilderAPI_MakePolygon()
        for p in (p1, p2, p3, p4):
            poly.Add(p)
        poly.Close()
        return poly.Wire()

    raise NotImplementedError(f"section kind={spec.kind}")
