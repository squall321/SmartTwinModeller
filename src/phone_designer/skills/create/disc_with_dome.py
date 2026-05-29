"""disc_with_dome — macro skill. 워치 외피 base.

원형 disc + 상부 dome (sphere section) + 하부 모서리 fillet.
Galaxy Watch / Apple Watch 외피의 일반 기본형.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="disc_with_dome",
    category="create",
    level="macro",
    summary="Cylindrical disc with optional spherical-cap dome on top and bottom-edge fillet. "
            "Standard watch-housing base. Equivalent to cylinder + sphere section + fillet.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["disc", "dome_cap", "fillet_face"],
    preserves=[],
    manufacturing={
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                               "extras": {"dome_via_post_machining": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
        "cnc_5axis":         {"min_wall_mm": 0.5},   # dome 곡면은 5축이 자연
    },
    failure_modes=["fm.dome_rise_too_large", "fm.corner_r_too_large"],
    cost_hint=0.2,
    expansion=["cylinder", "boolean_union_dome", "fillet_edges_by_predicate"],
    post_conditions=[PostCondition(kind="body_present")],
)
class DiscWithDome(SkillBase):
    class Args(BaseModel):
        diameter_mm: float = Field(gt=0, le=200)
        height_mm: float = Field(gt=0, le=50)
        dome_rise_mm: float = Field(ge=0, le=20,
                                     description="top dome 의 sag (0 = flat top)")
        corner_r_mm: float = Field(ge=0, le=20,
                                    description="bottom edge fillet (0 = sharp)")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Align, Cylinder, Sphere, Box, Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Common
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
        from OCP.BRep import BRep_Tool
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp, TopExp_Explorer
        from OCP.TopoDS import TopoDS
        from OCP.TopTools import TopTools_MapOfShape

        D = args.diameter_mm
        H = args.height_mm
        rise = args.dome_rise_mm
        corner_r = args.corner_r_mm

        if corner_r * 2 >= D:
            raise ValueError(f"corner_r {corner_r} too large for diameter D={D}")

        # 1. base cylinder
        cyl = Cylinder(radius=D / 2, height=H,
                        align=(Align.CENTER, Align.CENTER, Align.MIN))
        result_shape = cyl.wrapped

        # 2. dome 부착 (rise > 0 시)
        if rise > 0:
            # sphere section: R = (D/2)² / (2*rise) + rise/2
            R = (D / 2) ** 2 / (2 * rise) + rise / 2
            sph = Sphere(radius=R)
            # sphere 중심을 top face 아래 (H - R + rise) 에 둠
            sph.position = (0, 0, H - R + rise)
            # sphere 와 큰 disc-shaped slab (dome 영역만 자를 cylinder) 교차
            cap_disc = Cylinder(
                radius=D / 2, height=R,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )
            cap_disc.position = (0, 0, H - rise)
            common = BRepAlgoAPI_Common(sph.wrapped, cap_disc.wrapped)
            common.Build()
            if not common.IsDone():
                raise RuntimeError("dome cap 교차 실패")
            cap_shape = common.Shape()

            # base cylinder 의 top 부분 (rise 만큼) 제거 후 cap 추가.
            # cut_box 가 cylinder 의 corner 까지 다 덮으려면 D 보다 충분히 커야 함
            # (정사각형 box 의 corner-corner 거리 ≥ cylinder D).
            cut_size = D * 1.5
            cut_box = Box(cut_size, cut_size, rise + 1.0,
                          align=(Align.CENTER, Align.CENTER, Align.MIN))
            cut_box.position = (0, 0, H - rise)
            from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
            cut = BRepAlgoAPI_Cut(result_shape, cut_box.wrapped)
            cut.Build()
            if not cut.IsDone():
                raise RuntimeError("dome 영역 절단 실패")
            base_without_top = cut.Shape()

            fuse = BRepAlgoAPI_Fuse(base_without_top, cap_shape)
            fuse.Build()
            if not fuse.IsDone():
                raise RuntimeError("dome 융합 실패")
            result_shape = fuse.Shape()

        # 3. bottom edge fillet (corner_r > 0 시)
        if corner_r > 0:
            # bottom 원형 edge — z ≈ 0 + curve 가 circle.
            # TopExp_Explorer 결과는 TopoDS_Shape 라서 Edge 캐스트 필요. 또한 sub-shape
            # iteration 의 중복 제거.
            seen = TopTools_MapOfShape()
            bottom_edges = []
            it = TopExp_Explorer(result_shape, TopAbs_EDGE)
            while it.More():
                raw = it.Current()
                if not seen.Contains(raw):
                    seen.Add(raw)
                    e = TopoDS.Edge_s(raw)
                    v1 = TopExp.FirstVertex_s(e)
                    v2 = TopExp.LastVertex_s(e)
                    p1 = BRep_Tool.Pnt_s(v1)
                    p2 = BRep_Tool.Pnt_s(v2)
                    if abs(p1.Z()) < 0.1 and abs(p2.Z()) < 0.1:
                        bottom_edges.append(e)
                it.Next()
            if bottom_edges:
                maker = BRepFilletAPI_MakeFillet(result_shape)
                for e in bottom_edges:
                    maker.Add(corner_r, e)
                maker.Build()
                if maker.IsDone():
                    result_shape = maker.Shape()

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(result_shape), history=history)
