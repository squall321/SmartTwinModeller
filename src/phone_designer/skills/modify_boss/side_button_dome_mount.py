"""side_button_dome_mount — atomic. Internal boss for a tactile dome backing.

Most side buttons (power, volume) on metal-frame phones press a small
metal dome held by a flex PCB against an interior pad. That pad needs a
flat circular boss on the INNER face of the frame, directly behind the
button aperture, to register the dome and prevent flex.

This skill adds a low cylindrical boss on the selected planar face:

    boss = MakeCylinder(dome_d/2, height_mm) on face normal, fused to body.

``position_along_edge_mm`` is the offset of the boss center along the
LONGER in-plane axis of the host face (typically world Z on a ±X/±Y side
wall, world X/Y on a top/bottom face). The other in-plane offset is the
face center. v1: planar ±X / ±Y / ±Z host faces.
"""
from __future__ import annotations

from typing import Any

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
    name="side_button_dome_mount",
    category="modify/boss",
    level="atomic",
    summary="Low cylindrical boss on an inner face that backs a tactile metal "
            "dome behind a side button. Centered along the host face's longer "
            "in-plane axis at the given offset. v1: planar ±X / ±Y / ±Z host "
            "faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":    HistoryRule.MODIFIED_INHERIT,
        "dome_mount_boss": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.diameter_positive",
        "pc.height_positive",
    ],
    produces_features=["side_button_dome_mount"],
    preserves=["outer_envelope_outside_boss"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4,
                              "extras": {"flat_top_endmill": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.dome_boss_outside_face",
        "fm.dome_boss_height_excessive",
    ],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class SideButtonDomeMount(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_along_edge_mm: float = Field(
            default=0.0,
            description="offset of the boss center along the host face's "
                        "longer in-plane axis (from face center)",
        )
        dome_d_mm: float = Field(default=4.0, gt=0, le=20,
                                  description="cylindrical boss diameter "
                                              "(matches dome footprint)")
        height_mm: float = Field(default=0.5, gt=0, le=5,
                                  description="boss height above the host face")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"side_button_dome_mount: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)

        # Determine wall axis (face normal) and the two in-plane axes.
        nx, ny, nz = normal
        if abs(nz) > 0.9:
            # ±Z face: in-plane axes are X and Y; pick longer-extent direction.
            # Without face bbox lookup, fall back to world X as primary.
            long_axis = "X"
            normal_axis = "Z"
        elif abs(nx) > 0.9:
            # ±X side wall: in-plane axes are Y and Z; long axis is typically Z.
            long_axis = "Z"
            normal_axis = "X"
        elif abs(ny) > 0.9:
            # ±Y side wall: in-plane axes are X and Z; long axis is typically Z.
            long_axis = "Z"
            normal_axis = "Y"
        else:
            raise NotImplementedError(
                "side_button_dome_mount v1: planar ±X / ±Y / ±Z target faces "
                "only"
            )

        cx, cy, cz = center
        # Apply position_along_edge_mm along the long axis.
        if long_axis == "X":
            cx = cx + args.position_along_edge_mm
        elif long_axis == "Y":
            cy = cy + args.position_along_edge_mm
        else:
            cz = cz + args.position_along_edge_mm

        # Boss grows ALONG the face normal (outward).
        if normal_axis == "Z":
            n_sign = 1.0 if nz > 0 else -1.0
            direction = gp_Dir(0.0, 0.0, n_sign)
        elif normal_axis == "X":
            n_sign = 1.0 if nx > 0 else -1.0
            direction = gp_Dir(n_sign, 0.0, 0.0)
        else:
            n_sign = 1.0 if ny > 0 else -1.0
            direction = gp_Dir(0.0, n_sign, 0.0)

        ax2 = gp_Ax2(gp_Pnt(cx, cy, cz), direction)
        cyl_mk = BRepPrimAPI_MakeCylinder(
            ax2, args.dome_d_mm / 2.0, args.height_mm,
        )
        cyl_mk.Build()
        if not cyl_mk.IsDone():
            raise RuntimeError(
                "side_button_dome_mount: cylinder build failed"
            )
        boss = cyl_mk.Shape()

        fuse = BRepAlgoAPI_Fuse(shape, boss)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError(
                "side_button_dome_mount: body fuse failed"
            )
        new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "dome_mount_boss": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
