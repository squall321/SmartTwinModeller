"""grille_pattern — macro. hex/grid/radial hole 패턴.

speaker grille, vent 같은 반복 hole 패턴. hole_array 의 점 생성 wrapper.
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
    name="grille_pattern",
    category="modify/hole",
    level="macro",
    summary="Repeating hole pattern — hex / grid / radial. "
            "Generates point list then delegates to hole_array.",
    selector_kinds=[],
    history_rules={"result_cylinder_faces": HistoryRule.GENERATED_NEW},
    produces_features=["grille_pattern"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.window_outside_body", "fm.holes_too_dense"],
    cost_hint=0.3,
    expansion=["hole_array"],
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class GrillePattern(SkillBase):
    class Args(BaseModel):
        center: tuple[float, float, float] = Field(
            description="패턴 중심 (XY 평면 + Z 깊이 시작)",
        )
        pattern: Literal["hex", "grid", "radial"] = "hex"
        window_length_mm: float = Field(default=20.0, gt=0, le=200,
                                         description="패턴이 들어갈 박스 길이 (X)")
        window_width_mm: float = Field(default=10.0, gt=0, le=200,
                                        description="패턴이 들어갈 박스 폭 (Y)")
        hole_diameter_mm: float = Field(default=1.0, gt=0, le=10)
        spacing_mm: float = Field(default=2.5, gt=0, le=20,
                                   description="이웃 hole 의 중심 간 거리")
        depth_mm: float | None = Field(default=None, gt=0, le=200,
                                         description="None = through")
        direction: Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"] = "-Z"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.modify_pocket.hole_array import HoleArray

        # 점 생성
        points = _generate_pattern_points(
            args.pattern,
            cx=args.center[0],
            cy=args.center[1],
            cz=args.center[2],
            wL=args.window_length_mm,
            wW=args.window_width_mm,
            spacing=args.spacing_mm,
        )
        if not points:
            raise RuntimeError(
                f"grille_pattern: 0 points generated — "
                f"window={args.window_length_mm}×{args.window_width_mm}, "
                f"spacing={args.spacing_mm}"
            )

        # hole_array 위임
        r = HoleArray().apply(body, {
            "points": points,
            "diameter_mm": args.hole_diameter_mm,
            "depth_mm": args.depth_mm,
            "direction": args.direction,
        })
        # history 갱신
        r.history.rules = {"result_cylinder_faces": HistoryRule.GENERATED_NEW}
        return r


def _generate_pattern_points(
    pattern: str, *, cx: float, cy: float, cz: float,
    wL: float, wW: float, spacing: float,
) -> list[tuple[float, float, float]]:
    """패턴 종류별 (x, y, z=cz) 점 list."""
    pts = []

    if pattern == "grid":
        nx = int(wL / spacing)
        ny = int(wW / spacing)
        for ix in range(nx + 1):
            for iy in range(ny + 1):
                x = cx - wL / 2 + ix * spacing
                y = cy - wW / 2 + iy * spacing
                pts.append((x, y, cz))
        return pts

    if pattern == "hex":
        # 짝수/홀수 row 의 x offset 으로 hexagonal packing
        row_step = spacing * math.sqrt(3) / 2
        nx = int(wL / spacing)
        ny = int(wW / row_step)
        for iy in range(ny + 1):
            y = cy - wW / 2 + iy * row_step
            offset = (spacing / 2) if (iy % 2 == 1) else 0.0
            for ix in range(nx + 1):
                x = cx - wL / 2 + offset + ix * spacing
                if abs(x - cx) > wL / 2:
                    continue
                pts.append((x, y, cz))
        return pts

    if pattern == "radial":
        # 동심원의 N 개 ring. 가운데 1 + 각 ring 의 점 수 = round(2π × ring_R / spacing)
        max_r = min(wL, wW) / 2
        if spacing <= 0:
            return []
        n_rings = int(max_r / spacing)
        pts.append((cx, cy, cz))
        for ring in range(1, n_rings + 1):
            r = ring * spacing
            n_in_ring = max(1, round(2 * math.pi * r / spacing))
            for k in range(n_in_ring):
                theta = 2 * math.pi * k / n_in_ring
                pts.append((cx + r * math.cos(theta), cy + r * math.sin(theta), cz))
        return pts

    return []
