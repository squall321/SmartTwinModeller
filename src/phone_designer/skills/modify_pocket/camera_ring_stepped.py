"""camera_ring_stepped — atomic.

A stepped circular pocket for a camera bezel ring: a series of concentric
cylindrical cuts, each ring `ring_d_mm` deep, with diameter tapering linearly
from `base_d_mm` (top, mouth) down to `top_d_mm` (bottom, deepest disk).

Geometry (raw OCCT, ±Z planar host faces v1):
    For i in 0..ring_count-1:
        d_i = base_d - (base_d - top_d) * i / (ring_count - 1)   (if count > 1)
        z_top_i = face_z - z_sign * i * ring_d
        cyl_i   = MakeCylinder(d_i/2, ring_d + overshoot) sunk along normal
    tool = ∪ cyl_i
    Result = body \\ tool.
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
    name="camera_ring_stepped",
    category="modify/pocket",
    level="atomic",
    summary="Stepped circular pocket for a camera bezel ring — concentric "
            "cylindrical cuts tapering from base_d at the mouth down to top_d "
            "at the bottom. v1 supports planar ±Z host faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":         HistoryRule.MODIFIED_INHERIT,
        "camera_ring_pocket":  HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["camera_ring_pocket"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3, "min_fillet_r_mm": 0.2},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_concentricity": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.camera_ring_exits_body",
        "fm.camera_ring_inverted_taper",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class CameraRingStepped(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of camera ring center",
        )
        base_d_mm: float = Field(default=11.0, gt=0, le=80,
                                  description="mouth (top) ring diameter")
        top_d_mm: float = Field(default=9.0, gt=0, le=80,
                                 description="bottom (deepest) ring diameter")
        ring_count: int = Field(default=2, ge=1, le=10,
                                 description="number of concentric ring steps")
        ring_d_mm: float = Field(default=0.4, gt=0, le=10,
                                  description="per-ring step depth")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        if args.top_d_mm > args.base_d_mm:
            raise ValueError(
                f"camera_ring_stepped: top_d_mm ({args.top_d_mm}) must be ≤ "
                f"base_d_mm ({args.base_d_mm}) — pocket tapers inward as it "
                f"descends"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"camera_ring_stepped: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "camera_ring_stepped v1: planar ±Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        face_z = center[2]

        overshoot = 0.05

        ring_shapes: list[Any] = []
        for i in range(args.ring_count):
            if args.ring_count == 1:
                d_i = args.base_d_mm
            else:
                t = i / (args.ring_count - 1)
                d_i = args.base_d_mm - (args.base_d_mm - args.top_d_mm) * t
            # Each ring starts from its top edge and extends ring_d_mm deeper.
            # Ring 0 starts at the face surface (with overshoot), ring i starts
            # i * ring_d below the face surface.
            ring_top_z = face_z - z_sign * (i * args.ring_d_mm)
            ring_bottom_z = ring_top_z - z_sign * args.ring_d_mm
            # Cylinder axis pointing into the body (-z_sign).
            # Anchor at the deepest point and grow back up so the entire ring
            # cuts cleanly. Use an overshoot above only the first ring.
            base_z = ring_bottom_z
            top_z = ring_top_z + (overshoot if i == 0 else 0.0) * z_sign
            cyl_h = abs(top_z - base_z)
            # MakeCylinder grows along +Z of its gp_Ax2.
            anchor_z = min(base_z, top_z)
            ax = gp_Ax2(gp_Pnt(cx, cy, anchor_z), gp_Dir(0.0, 0.0, 1.0))
            mk = BRepPrimAPI_MakeCylinder(ax, d_i / 2.0, cyl_h)
            mk.Build()
            if not mk.IsDone():
                raise RuntimeError(
                    f"camera_ring_stepped: ring {i} cylinder build failed"
                )
            ring_shapes.append(mk.Shape())

        # Union all rings into a single tool.
        tool_shape = ring_shapes[0]
        for s in ring_shapes[1:]:
            fuse = BRepAlgoAPI_Fuse(tool_shape, s)
            fuse.Build()
            if not fuse.IsDone():
                raise RuntimeError("camera_ring_stepped: ring fuse failed")
            tool_shape = fuse.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool_shape)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("camera_ring_stepped: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":        HistoryRule.MODIFIED_INHERIT,
                "camera_ring_pocket": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
