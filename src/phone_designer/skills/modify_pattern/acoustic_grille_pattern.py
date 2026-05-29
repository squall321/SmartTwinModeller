"""acoustic_grille_pattern — atomic. Hex or square array of acoustic mesh
through-holes inside a rectangular region on a planar face.

Used to model the perforated cover above a microspeaker, mic, or sealed
membrane vent. Holes are tiny (default 0.8 mm) and packed at a tight pitch
(default 1.5 mm). Each hole is cut all the way through the body so the array
forms a real acoustic mesh, not a blind grille.

v1 supports planar +Z/-Z target faces.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._resolvers import (
    _face_center,
    _face_normal_at_center,
    resolve_faces,
)
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="acoustic_grille_pattern",
    category="modify/pattern",
    level="atomic",
    summary="Hex or square array of through-holes inside a rectangular region "
            "on a planar face. Models a microspeaker / mic acoustic mesh. "
            "v1 supports planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":       HistoryRule.MODIFIED_INHERIT,
        "grille_hole_set":   HistoryRule.GENERATED_NEW,
        "consumed_volume":   HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.hole_diameter_lt_pitch",
    ],
    produces_features=["acoustic_grille", "perforated_cover"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4,
                              "extras": {"min_drill_mm": 0.5}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_small_holes": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.hole_diameter_geq_pitch",
        "fm.region_outside_face",
        "fm.no_points_inside_region",
    ],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class AcousticGrillePattern(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of the region center",
        )
        region_w_mm: float = Field(gt=0, le=200,
                                    description="region width along X")
        region_h_mm: float = Field(gt=0, le=200,
                                    description="region height along Y")
        hole_d_mm: float = Field(default=0.8, gt=0, le=10)
        pitch_mm: float = Field(default=1.5, gt=0, le=20,
                                 description="center-to-center spacing")
        kind: Literal["hex", "square"] = "hex"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        if args.hole_d_mm >= args.pitch_mm:
            raise ValueError(
                f"acoustic_grille_pattern: hole_d_mm ({args.hole_d_mm}) "
                f">= pitch_mm ({args.pitch_mm}) — holes would overlap"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"acoustic_grille_pattern: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "acoustic_grille_pattern v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]

        # Build the candidate (lx, ly) point list in face-local coords centered
        # on (0, 0). Clip to the region rectangle.
        half_w = args.region_w_mm / 2.0
        half_h = args.region_h_mm / 2.0
        pitch = args.pitch_mm

        points_local: list[tuple[float, float]] = []
        if args.kind == "square":
            nx = int(args.region_w_mm / pitch)
            ny = int(args.region_h_mm / pitch)
            # Distribute evenly within the rectangle, centered.
            for iy in range(ny + 1):
                for ix in range(nx + 1):
                    lx = -half_w + ix * pitch + (args.region_w_mm - nx * pitch) / 2.0
                    ly = -half_h + iy * pitch + (args.region_h_mm - ny * pitch) / 2.0
                    if -half_w <= lx <= half_w and -half_h <= ly <= half_h:
                        points_local.append((lx, ly))
        else:
            # hex packing: row spacing = pitch * sqrt(3)/2, odd rows offset by
            # pitch/2.
            row_step = pitch * math.sqrt(3.0) / 2.0
            ny = int(args.region_h_mm / row_step)
            nx = int(args.region_w_mm / pitch)
            for iy in range(ny + 1):
                ly = -half_h + iy * row_step
                if ly > half_h:
                    continue
                offset = (pitch / 2.0) if (iy % 2 == 1) else 0.0
                for ix in range(nx + 2):
                    lx = -half_w + offset + ix * pitch
                    if -half_w <= lx <= half_w and -half_h <= ly <= half_h:
                        points_local.append((lx, ly))

        if not points_local:
            raise RuntimeError(
                f"acoustic_grille_pattern: 0 points generated for region "
                f"{args.region_w_mm}x{args.region_h_mm} at pitch {pitch}"
            )

        # Cylindrical through-hole tool: long enough to traverse the body.
        overshoot = 1.0
        cyl_h = 200.0 + 2.0 * overshoot
        cyl_r = args.hole_d_mm / 2.0
        new_shape = shape
        for (lx, ly) in points_local:
            wx = cx + lx
            wy = cy + ly
            base_z = center[2] + z_sign * overshoot
            axis_dir = gp_Dir(0.0, 0.0, -z_sign)
            ax = gp_Ax2(gp_Pnt(wx, wy, base_z), axis_dir)
            mk = BRepPrimAPI_MakeCylinder(ax, cyl_r, cyl_h)
            mk.Build()
            if not mk.IsDone():
                raise RuntimeError(
                    f"acoustic_grille_pattern: cylinder build failed at "
                    f"({wx:.3f},{wy:.3f})"
                )
            cut = BRepAlgoAPI_Cut(new_shape, mk.Shape())
            cut.Build()
            if not cut.IsDone():
                raise RuntimeError(
                    f"acoustic_grille_pattern: boolean cut failed at "
                    f"({wx:.3f},{wy:.3f})"
                )
            new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "grille_hole_set": HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
