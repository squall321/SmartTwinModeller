"""rounded_slab — macro skill. Box + Z-축 edge 4개 fillet.

폰 외피 base 의 표준. macro 라서 expansion 으로 atomic 시퀀스 명시.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="rounded_slab",
    category="create",
    level="macro",
    summary="Rectangular slab with 4 vertical edges filleted to corner_r_mm. "
            "Standard phone-housing base. Equivalent to box + fillet on Z-aligned edges.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["slab", "fillet_face"],
    preserves=[],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5, "min_fillet_r_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["corner_r_too_large_for_dimensions"],
    cost_hint=0.1,
    expansion=["box", "fillet_edges_by_predicate"],
    post_conditions=[PostCondition(kind="body_present")],
)
class RoundedSlab(SkillBase):
    class Args(BaseModel):
        length_mm: float = Field(gt=0)
        width_mm: float = Field(gt=0)
        height_mm: float = Field(gt=0)
        corner_r_mm: float = Field(gt=0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Box as B123dBox, Align
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.BRep import BRep_Tool
        from OCP.TopExp import TopExp
        from OCP.TopoDS import TopoDS
        from OCP.TopTools import TopTools_MapOfShape

        if args.corner_r_mm * 2 >= min(args.length_mm, args.width_mm):
            raise ValueError(
                f"corner_r_mm={args.corner_r_mm} too large for "
                f"L={args.length_mm} × W={args.width_mm}"
            )

        # 1. base box
        b = B123dBox(
            args.length_mm, args.width_mm, args.height_mm,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        shape = b.wrapped

        # 2. Z-축 vertical edges (4개) 추출 — sub-shape 중복 제거 + Edge cast
        seen = TopTools_MapOfShape()
        z_edges = []
        it = TopExp_Explorer(shape, TopAbs_EDGE)
        while it.More():
            raw = it.Current()
            if not seen.Contains(raw):
                seen.Add(raw)
                e = TopoDS.Edge_s(raw)
                v1 = TopExp.FirstVertex_s(e)
                v2 = TopExp.LastVertex_s(e)
                p1 = BRep_Tool.Pnt_s(v1)
                p2 = BRep_Tool.Pnt_s(v2)
                # Z 방향 vertical = Z 변동 ≈ height, XY 변동 ≈ 0
                if (abs(p1.Z() - p2.Z()) > 0.5 * args.height_mm
                        and abs(p1.X() - p2.X()) < 0.01
                        and abs(p1.Y() - p2.Y()) < 0.01):
                    z_edges.append(e)
            it.Next()

        if len(z_edges) != 4:
            raise RuntimeError(
                f"expected 4 Z-aligned edges, found {len(z_edges)}"
            )

        # 3. fillet 적용
        maker = BRepFilletAPI_MakeFillet(shape)
        for e in z_edges:
            maker.Add(args.corner_r_mm, e)
        maker.Build()
        if not maker.IsDone():
            raise RuntimeError("BRepFilletAPI_MakeFillet failed")
        new_shape = maker.Shape()

        # 4. build123d Part 로 wrap
        from build123d import Part
        result_part = Part(new_shape)

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=result_part, history=history)
