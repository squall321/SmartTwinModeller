"""anechoic_chamber_liner_mount — atomic. Pin bosses for acoustic foam attachment.

Acoustic foam / wedge liners are normally attached to a chamber wall by an
array of small pin bosses that pierce the foam and grip its open-cell
structure. This skill stamps a 2D grid of cylindrical pins onto the target
face. Each pin has diameter `mount_pin_d_mm` and height matched to the foam
thickness `foam_thickness_mm` (so the foam seats fully).

The pins are added (volume_increased) to the host body. v1 supports planar
+Z / -Z target faces. The array is centred on the face centre and uses a
square pitch of `pin_pitch_mm` in both X and Y. The footprint is sized to
fit inside the target face's UV bounding box with a single-pitch safety
margin so no pins land outside the face.
"""
from __future__ import annotations

import math
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
    name="anechoic_chamber_liner_mount",
    category="modify/pocket",  # this skill lives under modify_pocket per task spec
    level="atomic",
    summary="Square-grid array of small cylindrical pin bosses on a planar "
            "+Z/-Z face for impaling an acoustic foam / anechoic-wedge liner. "
            "Pin height = foam thickness; pin diameter and pitch are caller-"
            "controlled. Adds material (mounts) — volume_increased.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":   HistoryRule.MODIFIED_INHERIT,
        "mount_pins":    HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.pin_d_lt_pin_pitch",
    ],
    produces_features=["foam_mount_pins", "anechoic_liner_mount"],
    preserves=["outer_envelope_outside_pins"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"max_aspect_ratio": 6.0}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
    },
    failure_modes=[
        "fm.pin_d_geq_pitch",
        "fm.face_too_small_for_array",
    ],
    cost_hint=0.22,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class AnechoicChamberLinerMount(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        foam_thickness_mm: float = Field(default=25.0, gt=0, le=200)
        mount_pin_d_mm: float = Field(default=3.0, gt=0, le=20)
        pin_pitch_mm: float = Field(default=80.0, gt=0, le=500)
        margin_mm: float = Field(
            default=5.0, ge=0, le=200,
            description="Inset from the face bounding box before laying pins",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        if args.mount_pin_d_mm >= args.pin_pitch_mm:
            raise ValueError(
                f"anechoic_chamber_liner_mount: pin_d "
                f"({args.mount_pin_d_mm}) must be < pitch "
                f"({args.pin_pitch_mm})"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"anechoic_chamber_liner_mount: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "anechoic_chamber_liner_mount v1: planar +Z/-Z faces only",
            )
        nz_sign = 1.0 if normal[2] > 0 else -1.0

        # Face XY footprint via the face's bounding box.
        bb = Bnd_Box()
        BRepBndLib.Add_s(target, bb)
        xmin, ymin, _zmin, xmax, ymax, _zmax = bb.Get()
        # Inset margin so pins stay safely inside the face.
        margin = args.margin_mm + args.mount_pin_d_mm / 2.0
        x0 = xmin + margin
        x1 = xmax - margin
        y0 = ymin + margin
        y1 = ymax - margin
        if x1 <= x0 or y1 <= y0:
            raise ValueError(
                "anechoic_chamber_liner_mount: face too small for the "
                f"requested margin ({args.margin_mm}) + pin radius",
            )

        nx = max(1, int(math.floor((x1 - x0) / args.pin_pitch_mm)) + 1)
        ny = max(1, int(math.floor((y1 - y0) / args.pin_pitch_mm)) + 1)

        # Centre the lattice on the face XY centroid.
        used_w = (nx - 1) * args.pin_pitch_mm
        used_h = (ny - 1) * args.pin_pitch_mm
        ox = center[0] - used_w / 2.0
        oy = center[1] - used_h / 2.0

        new_shape = shape
        r = args.mount_pin_d_mm / 2.0
        H = args.foam_thickness_mm
        axis_dir = gp_Dir(0.0, 0.0, nz_sign)
        for i in range(nx):
            for j in range(ny):
                bx = ox + i * args.pin_pitch_mm
                by = oy + j * args.pin_pitch_mm
                base = gp_Pnt(bx, by, center[2])
                ax = gp_Ax2(base, axis_dir)
                mk = BRepPrimAPI_MakeCylinder(ax, r, H)
                mk.Build()
                if not mk.IsDone():
                    continue
                fuse = BRepAlgoAPI_Fuse(new_shape, mk.Shape())
                fuse.Build()
                if fuse.IsDone():
                    new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face": HistoryRule.MODIFIED_INHERIT,
                "mount_pins":  HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
