"""raft_add — atomic. Wide flat raft pad beneath a selected face for FDM/SLA
build-plate adhesion.

A raft is a separate, slightly larger flat slab that sits directly below the
part. It improves first-layer adhesion (more plate contact than a brim) and
isolates the part from a warped build plate. Unlike a brim, the raft sits at
the base of the part and provides a wide footprint with ``raft_overhang_mm``
extension on every side of the selected face's bounding box.

v1 supports planar +Z/-Z target faces (the most common base orientation). The
raft is fused into the body so downstream tools see a single solid.

manufacturing:
  fdm — primary use case. Typical raft thickness 0.5–1.5 mm (2-4 first layers).
  sla — sometimes used in resin printing as an interface layer above tree
        supports; thinner pad recommended.
  sls — self-supporting powder bed, no raft needed.
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
    """Return (xmin, ymin, xmax, ymax) of a planar face on its XY plane.

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
    name="raft_add",
    category="modify/3dprint",
    level="atomic",
    summary="Add a wide flat raft pad beneath a planar base face for FDM/SLA "
            "build-plate adhesion. The raft extends raft_overhang_mm outward "
            "from the face bounding box on every side and is raft_height_mm "
            "tall along the outward face normal. v1: planar +Z/-Z faces only.",
    selector_kinds=["faces"],
    history_rules={
        "target_face": HistoryRule.MODIFIED_INHERIT,
        "raft_solid":  HistoryRule.GENERATED_NEW,
    },
    produces_features=["support_raft"],
    preserves=["outer_envelope_outside_raft"],
    manufacturing={
        "fdm": {"min_wall_mm": 0.4,
                "extras": {"raft_layers": 4,
                           "raft_thickness_recommended_mm": 0.8,
                           "raft_overhang_recommended_mm": 5.0,
                           "purpose": "build_plate_adhesion"}},
        "sla": {"extras": {"prefer_raft": True,
                           "raft_thickness_recommended_mm": 0.5}},
        "sls": {"extras": {"self_supporting": True, "raft_unnecessary": True}},
    },
    failure_modes=["fm.non_planar_face", "fm.face_not_axial",
                   "fm.raft_overhang_too_small"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class RaftAdd(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        raft_height_mm: float = Field(default=0.6, gt=0.0, le=5.0)
        raft_overhang_mm: float = Field(default=5.0, gt=0.0, le=50.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"raft_add: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "raft_add v1: planar +Z/-Z target faces only "
                f"(face normal = {normal})"
            )

        center = _face_center(target)
        bbox = _face_planar_bbox(target)
        if bbox is None:
            raise RuntimeError(
                "raft_add: cannot compute planar bbox of target face"
            )
        xmin, ymin, xmax, ymax = bbox

        # Raft grows along the outward face normal (away from the part body).
        z_sign = 1.0 if normal[2] > 0 else -1.0
        base_z = center[2]
        top_z = base_z + z_sign * args.raft_height_mm

        # Wide footprint — face bbox grown by raft_overhang_mm every side.
        out_xmin = xmin - args.raft_overhang_mm
        out_xmax = xmax + args.raft_overhang_mm
        out_ymin = ymin - args.raft_overhang_mm
        out_ymax = ymax + args.raft_overhang_mm

        zlo = min(base_z, top_z)
        zhi = max(base_z, top_z)
        p_lo = gp_Pnt(out_xmin, out_ymin, zlo)
        p_hi = gp_Pnt(out_xmax, out_ymax, zhi)
        try:
            mk = BRepPrimAPI_MakeBox(p_lo, p_hi)
            mk.Build()
        except Exception as exc:
            raise RuntimeError(
                f"raft_add: raft box build failed: {exc}"
            )
        if not mk.IsDone():
            raise RuntimeError("raft_add: raft box build not done")
        raft_shape = mk.Shape()

        fuse = BRepAlgoAPI_Fuse(shape, raft_shape)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("raft_add: fuse to body failed")
        new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face": HistoryRule.MODIFIED_INHERIT,
                "raft_solid":  HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
