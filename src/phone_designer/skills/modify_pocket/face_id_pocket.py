"""face_id_pocket — atomic. Multi-component cutout for a Face-ID style sensor stack.

Modern smartphones embed a 3D structured-light sensor stack behind the front
glass (Face ID / TrueDepth style). The stack typically requires four small
adjacent pockets cut into the inner frame:

    1. **projector** — IR dot/structured-light projector (rectangular,
       ``projector_w_mm × projector_h_mm``).
    2. **camera**    — IR camera (circular, ``camera_d_mm``).
    3. **sensor**    — flood/ambient sensor (circular, ``sensor_d_mm``).
    4. all three are spaced laterally along the face-local X axis by
       ``spacing_mm`` between successive component edges.

A single fused pocket tool is built from
    box(projector_w × projector_h) ∪ cyl(camera_d) ∪ cyl(sensor_d)
and cut from the body. All four components share the same depth = the
projector height (deepest component) so the cavity has a single flat floor.

v1: planar ±Z host faces only. ``position_xy`` is face-local and refers to
the centroid of the **projector** rectangle; the camera and sensor are
placed to the +X side of the projector.
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
    name="face_id_pocket",
    category="modify/pocket",
    level="macro",
    summary="Multi-component cutout (projector + camera + sensor) for a Face-ID "
            "style structured-light sensor stack. Fuses one rectangular and "
            "two circular pockets into a single tool, then cuts from the body. "
            "v1 supports planar ±Z host faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "projector_well":  HistoryRule.GENERATED_NEW,
        "camera_well":     HistoryRule.GENERATED_NEW,
        "sensor_well":     HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
        "pc.spacing_positive",
    ],
    produces_features=[
        "face_id_pocket",
        "projector_pocket",
        "camera_pocket",
        "sensor_pocket",
    ],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3,
                              "extras": {"min_drill_mm": 0.5}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_sensor_fit": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.face_id_pocket_exits_body",
        "fm.face_id_components_overlap",
    ],
    cost_hint=0.45,
    post_conditions=[PostCondition(kind="volume_decreased")],
    expansion=["extrude_pocket", "hole", "hole"],
)
class FaceIdPocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of projector centroid",
        )
        projector_w_mm: float = Field(default=6.0, gt=0, le=30,
                                       description="projector rectangle width "
                                                   "(world X)")
        projector_h_mm: float = Field(default=4.0, gt=0, le=30,
                                       description="projector rectangle height "
                                                   "(world Y)")
        camera_d_mm: float = Field(default=3.0, gt=0, le=20,
                                    description="IR camera bore diameter")
        sensor_d_mm: float = Field(default=2.0, gt=0, le=20,
                                    description="flood/ambient sensor bore "
                                                "diameter")
        spacing_mm: float = Field(default=1.5, gt=0, le=20,
                                   description="lateral gap between successive "
                                               "component edges along +X")
        depth_mm: float = Field(default=2.5, gt=0, le=20,
                                 description="pocket depth (common floor)")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"face_id_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "face_id_pocket v1: planar ±Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        face_z = center[2]
        floor_z = face_z - z_sign * args.depth_mm

        overshoot = 0.05
        zmin = min(face_z, floor_z)
        zmax = max(face_z, floor_z)
        if z_sign > 0:
            zmax += overshoot
        else:
            zmin -= overshoot

        # 1) Projector rectangle centered at (cx, cy)
        pj_lo = gp_Pnt(cx - args.projector_w_mm / 2.0,
                       cy - args.projector_h_mm / 2.0, zmin)
        pj_hi = gp_Pnt(cx + args.projector_w_mm / 2.0,
                       cy + args.projector_h_mm / 2.0, zmax)
        pj_mk = BRepPrimAPI_MakeBox(pj_lo, pj_hi)
        pj_mk.Build()
        if not pj_mk.IsDone():
            raise RuntimeError("face_id_pocket: projector box build failed")
        tool = pj_mk.Shape()

        # 2) Camera cylinder (next to projector +X by spacing + camera_r)
        cam_x = (cx + args.projector_w_mm / 2.0
                 + args.spacing_mm + args.camera_d_mm / 2.0)
        cam_y = cy
        cyl_h = args.depth_mm + 2.0 * overshoot
        base_z = face_z + z_sign * overshoot
        cam_ax = gp_Ax2(gp_Pnt(cam_x, cam_y, base_z), gp_Dir(0.0, 0.0, -z_sign))
        cam_mk = BRepPrimAPI_MakeCylinder(cam_ax, args.camera_d_mm / 2.0, cyl_h)
        cam_mk.Build()
        if not cam_mk.IsDone():
            raise RuntimeError("face_id_pocket: camera cylinder build failed")
        fuse = BRepAlgoAPI_Fuse(tool, cam_mk.Shape())
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("face_id_pocket: projector+camera fuse failed")
        tool = fuse.Shape()

        # 3) Sensor cylinder (next to camera +X by spacing + (cam_r + sens_r))
        sens_x = (cam_x + args.camera_d_mm / 2.0
                  + args.spacing_mm + args.sensor_d_mm / 2.0)
        sens_y = cy
        sens_ax = gp_Ax2(gp_Pnt(sens_x, sens_y, base_z),
                         gp_Dir(0.0, 0.0, -z_sign))
        sens_mk = BRepPrimAPI_MakeCylinder(
            sens_ax, args.sensor_d_mm / 2.0, cyl_h,
        )
        sens_mk.Build()
        if not sens_mk.IsDone():
            raise RuntimeError("face_id_pocket: sensor cylinder build failed")
        fuse2 = BRepAlgoAPI_Fuse(tool, sens_mk.Shape())
        fuse2.Build()
        if not fuse2.IsDone():
            raise RuntimeError("face_id_pocket: +sensor fuse failed")
        tool = fuse2.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("face_id_pocket: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "projector_well":  HistoryRule.GENERATED_NEW,
                "camera_well":     HistoryRule.GENERATED_NEW,
                "sensor_well":     HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
