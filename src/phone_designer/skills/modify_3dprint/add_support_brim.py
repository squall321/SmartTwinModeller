"""add_support_brim — atomic. FDM build-plate brim.

Adds a thin rectangular brim around the perimeter of a selected planar base
face. Brims help FDM prints adhere to the build plate and resist warping on
ABS / large flat parts. The brim is a thin rectangular plate that extends
``brim_width_mm`` outward from the face bounding box on every side, with
height ``brim_height_mm`` along the outward face normal.

v1 supports planar +Z/-Z target faces (the most common base orientation).

manufacturing:
  fdm — primary use case. brim_height typically 0.2–0.4 mm (one or two layers).
  sla — rafts/supports are normal practice, brims less common.
  sls — self-supporting, no brim needed.
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


def _face_planar_bbox(face) -> tuple[float, float, float, float] | None:
    """Return the (xmin, ymin, xmax, ymax) of the planar face on its XY plane.

    v1 supports +Z/-Z faces only — the face lives in the XY plane at a
    constant Z. Returns None if computation fails.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.Add_s(face, bb)
    except Exception:
        return None
    if bb.IsVoid():
        return None
    xmin, ymin, _zmin, xmax, ymax, _zmax = bb.Get()
    return (xmin, ymin, xmax, ymax)


@skill(
    name="add_support_brim",
    category="modify/3dprint",
    level="atomic",
    summary="Add a thin rectangular brim around a planar base face for FDM "
            "build-plate adhesion. The brim extends brim_width_mm outward "
            "from the face bounding box and is brim_height_mm tall along the "
            "outward face normal. v1: planar +Z / -Z target faces only.",
    selector_kinds=["faces"],
    history_rules={
        "target_face": HistoryRule.MODIFIED_INHERIT,
        "brim_solid":  HistoryRule.GENERATED_NEW,
    },
    produces_features=["support_brim"],
    preserves=["outer_envelope_outside_brim"],
    manufacturing={
        "fdm": {"min_wall_mm": 0.4,
                "extras": {"brim_layers": 2,
                           "brim_width_recommended_mm": 5.0,
                           "purpose": "build_plate_adhesion"}},
        "sla": {"extras": {"prefer_raft": True}},
        "sls": {"extras": {"self_supporting": True, "brim_unnecessary": True}},
    },
    failure_modes=["fm.non_planar_face", "fm.face_not_axial",
                   "fm.brim_width_too_small"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class AddSupportBrim(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        brim_width_mm: float = Field(default=5.0, gt=0.0, le=50.0)
        brim_height_mm: float = Field(default=0.4, gt=0.0, le=5.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"add_support_brim: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "add_support_brim v1: planar +Z/-Z target faces only "
                f"(face normal = {normal})"
            )

        center = _face_center(target)
        bbox = _face_planar_bbox(target)
        if bbox is None:
            raise RuntimeError(
                "add_support_brim: cannot compute planar bbox of target face"
            )
        xmin, ymin, xmax, ymax = bbox

        # outward direction along build axis
        z_sign = 1.0 if normal[2] > 0 else -1.0
        base_z = center[2]
        top_z = base_z + z_sign * args.brim_height_mm

        # brim outer rectangle = face bbox grown by brim_width on every side
        out_xmin = xmin - args.brim_width_mm
        out_xmax = xmax + args.brim_width_mm
        out_ymin = ymin - args.brim_width_mm
        out_ymax = ymax + args.brim_width_mm

        zlo = min(base_z, top_z)
        zhi = max(base_z, top_z)
        p_lo = gp_Pnt(out_xmin, out_ymin, zlo)
        p_hi = gp_Pnt(out_xmax, out_ymax, zhi)
        try:
            mk = BRepPrimAPI_MakeBox(p_lo, p_hi)
            mk.Build()
        except Exception as exc:
            raise RuntimeError(
                f"add_support_brim: brim box build failed: {exc}"
            )
        if not mk.IsDone():
            raise RuntimeError("add_support_brim: brim box build not done")
        brim_shape = mk.Shape()

        fuse = BRepAlgoAPI_Fuse(shape, brim_shape)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("add_support_brim: fuse to body failed")
        new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face": HistoryRule.MODIFIED_INHERIT,
                "brim_solid":  HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
