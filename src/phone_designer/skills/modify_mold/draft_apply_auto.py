"""draft_apply_auto — atomic. Apply automatic draft to all near-vertical faces.

Uses OCCT's BRepOffsetAPI_DraftAngle (available in OCP). Strategy:

    1. Identify planar faces whose normal is near-perpendicular to the pull
       direction (|n · pull| < cos(90° - min_draft_deg)). These are the side
       walls that need draft.
    2. For each such face, call DraftAngle.Add(face, pull_dir, angle, neutral).
       The neutral plane is z = parting_z_mm (if provided) or z = body bbox
       max for +Z pull / bbox min for -Z pull. Faces above the neutral plane
       stay put; matter is removed below it (taper inward).
    3. Build. If AddDone / IsDone is False, raise — we want the post-condition
       framework to surface the failure clearly.

Note: BRepOffsetAPI_DraftAngle taper amount = tan(angle) × distance from
neutral plane. The volume change is therefore proportional to the wall area
× tan(angle) × half-height — easy to verify in tests.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


@skill(
    name="draft_apply_auto",
    category="modify/mold",
    level="atomic",
    summary="Apply automatic draft (BRepOffsetAPI_DraftAngle) to all "
            "near-vertical faces relative to the pull direction. Faces above "
            "the neutral plane (parting_z or body top) stay still; faces below "
            "taper inward by min_draft_deg.",
    selector_kinds=[],
    history_rules={},
    produces_features=["drafted_side_walls"],
    preserves=["outer_envelope"],
    manufacturing={
        "die_cast_al":       {"min_draft_deg": 1.0},
        "injection_mold_pa": {"min_draft_deg": 0.5},
    },
    failure_modes=["fm.draft_angle_failed", "fm.no_side_faces_found"],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="volume_changed")],
)
class DraftApplyAuto(SkillBase):
    class Args(BaseModel):
        min_draft_deg: float = Field(default=1.0, gt=0.0, le=45.0)
        pull_direction: Literal["+Z", "-Z"] = "+Z"
        parting_z_mm: float | None = None

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle
        from OCP.GeomAbs import GeomAbs_Plane
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
        from OCP.TopAbs import TopAbs_REVERSED

        from phone_designer.skills._resolvers import _all_faces

        shape = _occt_shape(body)
        if shape is None:
            raise RuntimeError("draft_apply_auto: body is None")

        # Pull direction unit vector.
        if args.pull_direction == "+Z":
            pdir = (0.0, 0.0, 1.0)
        else:
            pdir = (0.0, 0.0, -1.0)

        # Neutral plane Z.
        if args.parting_z_mm is not None:
            neutral_z = args.parting_z_mm
        else:
            # Default neutral = bbox top for +Z, bbox bottom for -Z.
            from OCP.Bnd import Bnd_Box
            from OCP.BRepBndLib import BRepBndLib
            bb = Bnd_Box()
            try:
                BRepBndLib.AddOptimal_s(shape, bb)
            except Exception:
                BRepBndLib.Add_s(shape, bb)
            if bb.IsVoid():
                raise RuntimeError("draft_apply_auto: body bbox is void")
            _, _, zmin, _, _, zmax = bb.Get()
            neutral_z = zmax if args.pull_direction == "+Z" else zmin

        neutral_plane = gp_Pln(
            gp_Pnt(0.0, 0.0, neutral_z), gp_Dir(0.0, 0.0, 1.0),
        )
        pull_gp = gp_Dir(pdir[0], pdir[1], pdir[2])

        # Threshold: a face is "near-vertical" wrt pull dir if |dot(n, pull)|
        # is small (n nearly perpendicular to pull). We use a generous cutoff
        # of cos(80°) = 0.174 so genuinely vertical walls qualify but slanted
        # tops/bottoms do not.
        VERT_DOT_MAX = math.cos(math.radians(80.0))

        draft = BRepOffsetAPI_DraftAngle(shape)
        angle_rad = math.radians(args.min_draft_deg)
        added = 0

        for f in _all_faces(shape):
            surf = BRepAdaptor_Surface(f)
            if surf.GetType() != GeomAbs_Plane:
                # DraftAngle only supports planar/cylindrical/conical; skip
                # other surface types for safety.
                continue
            pl = surf.Plane()
            n = pl.Axis().Direction()
            nx, ny, nz = n.X(), n.Y(), n.Z()
            if f.Orientation() == TopAbs_REVERSED:
                nx, ny, nz = -nx, -ny, -nz
            dot = nx * pdir[0] + ny * pdir[1] + nz * pdir[2]
            if abs(dot) > VERT_DOT_MAX:
                # Roughly horizontal — top/bottom face — skip.
                continue

            try:
                draft.Add(f, pull_gp, angle_rad, neutral_plane, True)
            except Exception:
                # Add can raise on unsupported faces — keep going.
                continue
            if not draft.AddDone():
                # Roll back the failed add and continue.
                try:
                    bad = draft.ProblematicShape()
                    draft.Remove(bad)
                except Exception:
                    pass
                continue
            added += 1

        if added == 0:
            raise RuntimeError(
                "draft_apply_auto: no near-vertical planar faces found "
                f"(pull={args.pull_direction})"
            )

        draft.Build()
        if not draft.IsDone():
            raise RuntimeError(
                f"draft_apply_auto: DraftAngle build failed "
                f"(added {added} faces, angle={args.min_draft_deg}°)"
            )

        new_shape = draft.Shape()
        return SkillResult(
            body=Part(new_shape),
            history=EntityHistoryMap(),
        )
