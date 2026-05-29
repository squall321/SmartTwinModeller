"""prism_n_sided — atomic create skill.

규칙적 n-각형 prism. n-gon wire → face → MakePrism(face, vec).
axis ≠ Z 인 경우 base 가 XY 면 그대로 만든 뒤 transform 으로 회전.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="prism_n_sided",
    category="create",
    level="atomic",
    summary="Regular n-sided prism. Vertices on circumscribed circle of given radius.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["prism_solid"],
    preserves=[],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["zero_or_negative_dimension", "n_sides_lt_3"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class PrismNSided(SkillBase):
    class Args(BaseModel):
        n_sides: int = Field(ge=3)
        circumscribed_radius_mm: float = Field(gt=0)
        height_mm: float = Field(gt=0)
        center_x_mm: float = 0.0
        center_y_mm: float = 0.0
        base_z_mm: float = 0.0
        axis: Literal["X", "Y", "Z"] = "Z"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeEdge,
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakeWire,
            BRepBuilderAPI_Transform,
        )
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
        from build123d import Part

        n = args.n_sides
        R = args.circumscribed_radius_mm
        h = args.height_mm

        # 1. n-gon vertices on XY plane at z=0 (will translate later)
        pts = []
        for k in range(n):
            theta = 2.0 * math.pi * k / n
            pts.append(gp_Pnt(R * math.cos(theta), R * math.sin(theta), 0.0))

        # 2. n edges → wire
        wire_maker = BRepBuilderAPI_MakeWire()
        for k in range(n):
            p1 = pts[k]
            p2 = pts[(k + 1) % n]
            edge_maker = BRepBuilderAPI_MakeEdge(p1, p2)
            edge_maker.Build()
            if not edge_maker.IsDone():
                raise RuntimeError(f"prism_n_sided: edge {k} build failed")
            wire_maker.Add(edge_maker.Edge())
        wire_maker.Build()
        if not wire_maker.IsDone():
            raise RuntimeError("prism_n_sided: wire build failed")
        wire = wire_maker.Wire()

        # 3. wire → face
        face_maker = BRepBuilderAPI_MakeFace(wire, True)
        face_maker.Build()
        if not face_maker.IsDone():
            raise RuntimeError("prism_n_sided: face build failed")
        face = face_maker.Face()

        # 4. prism via extrusion along +Z by h
        prism_maker = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, h))
        prism_maker.Build()
        if not prism_maker.IsDone():
            raise RuntimeError("BRepPrimAPI_MakePrism failed")
        shape = prism_maker.Shape()

        # 5. rotate for non-Z axis. Z extrusion → +Z 방향. axis="X" 면 Y 축으로 +90°.
        if args.axis == "X":
            trsf = gp_Trsf()
            trsf.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 1, 0)), math.pi / 2.0)
            t = BRepBuilderAPI_Transform(shape, trsf, True)
            t.Build()
            if not t.IsDone():
                raise RuntimeError("prism rotation X failed")
            shape = t.Shape()
        elif args.axis == "Y":
            trsf = gp_Trsf()
            trsf.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)), -math.pi / 2.0)
            t = BRepBuilderAPI_Transform(shape, trsf, True)
            t.Build()
            if not t.IsDone():
                raise RuntimeError("prism rotation Y failed")
            shape = t.Shape()

        # 6. translate to (center_x, center_y, base_z)
        if (args.center_x_mm, args.center_y_mm, args.base_z_mm) != (0.0, 0.0, 0.0):
            trsf = gp_Trsf()
            trsf.SetTranslation(
                gp_Vec(args.center_x_mm, args.center_y_mm, args.base_z_mm)
            )
            t = BRepBuilderAPI_Transform(shape, trsf, True)
            t.Build()
            if not t.IsDone():
                raise RuntimeError("prism translation failed")
            shape = t.Shape()

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shape), history=history)
