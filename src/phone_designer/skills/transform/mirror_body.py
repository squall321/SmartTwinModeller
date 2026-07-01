"""mirror_body — atomic. Mirror the working solid across an arbitrary plane.

Reflects REAL geometry across a plane (point + normal) — left/right housings,
symmetric brackets. Distinct from the pattern-category ``mirror_feature`` /
``mirror_about_two_planes``, which only reflect a re-parametrized CIRCULAR
pocket/hole/boss template, never the actual body.

A pure reflection INVERTS face orientation (a mirrored solid has flipped normals),
so the result is passed through ShapeFix to restore a consistent outward
orientation. ``merge=True`` fuses the original + mirror into one symmetric body
(the common "model half, mirror-merge" idiom); ``merge=False`` returns just the
mirror image. ``keep_original`` (only when not merging) returns a 2-body compound.
Reuses gp_Trsf.SetMirror + the assembly transform/compound helpers.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult

_EPS_VOL = 1e-6


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(float(g.Mass()))


def _fix_orientation(shape):
    """A mirror inverts face orientation — ShapeFix_Shape restores it."""
    from OCP.ShapeFix import ShapeFix_Shape
    fix = ShapeFix_Shape(shape)
    fix.Perform()
    return fix.Shape()


@skill(
    name="mirror_body",
    category="transform",
    level="atomic",
    summary="Mirror the working solid across an arbitrary plane "
            "(plane_origin_mm + plane_normal). Reflects REAL geometry (unlike "
            "pattern/mirror_feature). merge=True fuses original+mirror into one "
            "symmetric body (model-half → mirror-merge); merge=False returns just "
            "the mirror (keep_original=True → 2-body compound). Face orientation is "
            "ShapeFix-restored. Volume preserved (or doubled on merge).",
    selector_kinds=[],
    history_rules={"body_faces": HistoryRule.MODIFIED_INHERIT,
                   "mirror_faces": HistoryRule.GENERATED_NEW},
    produces_features=[],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.zero_plane_normal", "fm.mirror_failed"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class MirrorBody(SkillBase):
    class Args(BaseModel):
        plane_origin_mm: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="A point on the mirror plane.")
        plane_normal: tuple[float, float, float] = Field(
            default=(1.0, 0.0, 0.0),
            description="Normal of the mirror plane (need not be unit). Default "
                        "(1,0,0) mirrors across the YZ plane.")
        merge: bool = Field(
            default=False,
            description="True = fuse original + mirror into one symmetric solid "
                        "(model-half → mirror-merge). False = return the mirror only.")
        keep_original: bool = Field(
            default=False,
            description="When merge=False, True returns a 2-body compound "
                        "(original + mirror) instead of just the mirror.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf

        from phone_designer.skills.assembly._compound import (
            apply_transform_shape,
            build_compound,
        )

        if body is None:
            raise ValueError("fm.mirror_failed: mirror_body needs an input body.")
        shape = body.wrapped if hasattr(body, "wrapped") else body

        nx, ny, nz = args.plane_normal
        if math.sqrt(nx * nx + ny * ny + nz * nz) < 1e-12:
            raise ValueError(
                "fm.zero_plane_normal: plane_normal is a zero vector.")
        ox, oy, oz = args.plane_origin_mm

        trsf = gp_Trsf()
        # SetMirror about a plane = gp_Ax2 (origin + normal as its main axis).
        trsf.SetMirror(gp_Ax2(gp_Pnt(ox, oy, oz), gp_Dir(nx, ny, nz)))

        try:
            mirrored = _fix_orientation(apply_transform_shape(shape, trsf))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.mirror_failed: {type(exc).__name__}: {exc}")

        if args.merge:
            from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
            fuse = BRepAlgoAPI_Fuse(shape, mirrored)
            fuse.Build()
            if not fuse.IsDone():
                raise ValueError(
                    "fm.mirror_failed: mirror-merge fuse failed (IsDone=False). "
                    "The plane may not touch the body — merge needs the halves to "
                    "share the mirror plane.")
            out = fuse.Shape()
            n_bodies = 1
        elif args.keep_original:
            out = build_compound([shape, mirrored])
            n_bodies = 2
        else:
            out = mirrored
            n_bodies = 1

        vol = _volume(out)
        if out is None or out.IsNull() or vol <= _EPS_VOL:
            raise ValueError(f"fm.mirror_failed: result volume {vol:.6g}mm³ ≈ 0.")

        return SkillResult(
            body=Part(out),
            history=EntityHistoryMap(rules={
                "body_faces": HistoryRule.MODIFIED_INHERIT,
                "mirror_faces": HistoryRule.GENERATED_NEW}),
            extras={"transform": {
                "op": "mirror_body",
                "plane_origin_mm": list(args.plane_origin_mm),
                "plane_normal": list(args.plane_normal),
                "merge": args.merge,
                "n_bodies": n_bodies,
                "volume_mm3": round(vol, 3),
            }},
        )
