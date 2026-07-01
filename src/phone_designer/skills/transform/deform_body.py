"""deform_body — atomic. Free-form deform (twist / taper) of the working solid.

The Flex / Deform family (SolidWorks Flex, Fusion, NX Deform) — a smooth space warp
along the Z axis, NOT a rigid motion. Two modes:

  * ``twist`` — rotate every cross-section about Z by an angle proportional to its
    height (``twist_deg`` total, linearly 0→twist_deg from z_min→z_max). Turbine
    blades, augers, decorative twists.
  * ``taper`` — scale every cross-section's XY about the Z axis by a factor that
    ramps linearly from 1.0 at z_min to ``taper_ratio`` at z_max. A tapered post /
    draft-like flare (distinct from scale_body's uniform/per-axis scale — here the
    factor VARIES with height).

This is a genuine kernel-effort op (no one-call OCCT primitive): the shape is
NurbsConvert-ed so every face is a B-spline, each face's control poles are refined
(degree raise + knot insertion, ``refine`` steps) so they can track the nonlinear
warp, then each pole is displaced by the height-dependent map and the faces re-sewn
into a solid. HONEST: because a B-spline face only approximates the warped surface,
the result VOLUME is approximate — it converges to the true value as ``refine``
grows (a 20×20×40 box twisted 80°: refine 0 → 28% low, refine 16 → 0.24% low). The
default refine=12 keeps volume error under ~1%. is_solid is gated on volume>1e-6.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

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


def _z_range(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    _, _, zmn, _, _, zmx = bb.Get()
    return zmn, zmx


@skill(
    name="deform_body",
    category="transform",
    level="atomic",
    summary="Free-form deform the working solid along Z: mode='twist' (rotate each "
            "cross-section about Z by twist_deg·(z-z0)/(z1-z0)) or 'taper' (ramp the "
            "XY scale from 1.0 at z_min to taper_ratio at z_max). A genuine space "
            "warp (NurbsConvert → refine poles → displace → re-sew), unlike "
            "scale_body (uniform) or move_body (rigid). Volume is APPROXIMATE, "
            "converging as `refine` grows. is_solid gated on volume>0.",
    selector_kinds=[],
    history_rules={"body_faces": HistoryRule.MODIFIED_INHERIT},
    produces_features=["deform"],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.deform_needs_param", "fm.deform_flat_z",
                   "fm.deform_failed", "fm.deform_not_solid"],
    cost_hint=0.5,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class DeformBody(SkillBase):
    class Args(BaseModel):
        mode: Literal["twist", "taper"] = Field(
            description="twist = rotate cross-sections about Z with height; "
                        "taper = ramp the XY scale with height.")
        twist_deg: float = Field(
            default=0.0,
            description="TOTAL twist over the full Z height (mode='twist'). The "
                        "rotation at height z is twist_deg·(z-z_min)/(z_max-z_min).")
        taper_ratio: float = Field(
            default=1.0, gt=0.0,
            description="XY scale at z_max (mode='taper'); 1.0 at z_min. <1 shrinks "
                        "the top (a spire), >1 flares it.")
        refine: int = Field(
            default=12, ge=0, le=64,
            description="Pole-refinement steps (degree raise + knot insertion) so "
                        "the B-spline faces track the warp. Higher = more accurate "
                        "volume, slower. 0 = raw (fast, coarse).")

        @model_validator(mode="after")
        def _needs_param(self):
            if self.mode == "twist" and self.twist_deg == 0.0:
                raise ValueError(
                    "fm.deform_needs_param: mode='twist' needs a non-zero twist_deg.")
            if self.mode == "taper" and self.taper_ratio == 1.0:
                raise ValueError(
                    "fm.deform_needs_param: mode='taper' needs taper_ratio != 1.0.")
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRep import BRep_Tool
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakeSolid,
            BRepBuilderAPI_NurbsConvert,
            BRepBuilderAPI_Sewing,
        )
        from OCP.Geom import Geom_BSplineSurface
        from OCP.gp import gp_Pnt
        from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        if body is None:
            raise ValueError("fm.deform_failed: deform_body needs an input body.")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        vol_before = _volume(shape)

        zmn, zmx = _z_range(shape)
        span = zmx - zmn
        if span < 1e-9:
            raise ValueError(
                "fm.deform_flat_z: body has ~zero Z extent — nothing to warp along Z.")

        twist_total = math.radians(args.twist_deg)

        def warp(x, y, z):
            t = (z - zmn) / span  # 0..1 up the body
            if args.mode == "twist":
                a = twist_total * t
                ca, sa = math.cos(a), math.sin(a)
                return x * ca - y * sa, x * sa + y * ca, z
            # taper: scale XY about the Z axis (x=y=0)
            s = 1.0 + (args.taper_ratio - 1.0) * t
            return x * s, y * s, z

        try:
            nurbs = BRepBuilderAPI_NurbsConvert(shape, True).Shape()
            sew = BRepBuilderAPI_Sewing(1e-6)
            ex = TopExp_Explorer(nurbs, TopAbs_FACE)
            n_faces = 0
            while ex.More():
                f = TopoDS.Face_s(ex.Current())
                surf = BRep_Tool.Surface_s(f)
                if isinstance(surf, Geom_BSplineSurface):
                    bs = surf
                    if args.refine > 0:
                        bs.IncreaseDegree(min(bs.UDegree() + 2, 8),
                                          min(bs.VDegree() + 2, 8))
                        for k in range(1, args.refine):
                            fr = k / args.refine
                            u0 = bs.UKnot(1) + fr * (bs.UKnot(bs.NbUKnots()) - bs.UKnot(1))
                            v0 = bs.VKnot(1) + fr * (bs.VKnot(bs.NbVKnots()) - bs.VKnot(1))
                            try:
                                bs.InsertUKnot(u0, 1, 1e-9)
                                bs.InsertVKnot(v0, 1, 1e-9)
                            except Exception:  # noqa: BLE001,S110
                                pass
                    for i in range(1, bs.NbUPoles() + 1):
                        for j in range(1, bs.NbVPoles() + 1):
                            p = bs.Pole(i, j)
                            nx, ny, nz = warp(p.X(), p.Y(), p.Z())
                            bs.SetPole(i, j, gp_Pnt(nx, ny, nz))
                    mf = BRepBuilderAPI_MakeFace(bs, 1e-6)
                    if mf.IsDone():
                        sew.Add(mf.Face())
                        n_faces += 1
                ex.Next()
            sew.Perform()
            sewed = sew.SewedShape()
            solid = None
            exs = TopExp_Explorer(sewed, TopAbs_SHELL)
            if exs.More():
                ms = BRepBuilderAPI_MakeSolid(TopoDS.Shell_s(exs.Current()))
                if ms.IsDone():
                    solid = ms.Solid()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.deform_failed: {type(exc).__name__}: {exc}")

        if solid is None:
            raise ValueError(
                "fm.deform_not_solid: the warped faces did not re-sew into a closed "
                "solid (try a higher refine, or a smaller twist/taper).")
        vol_after = _volume(solid)
        if solid.IsNull() or vol_after <= _EPS_VOL:
            raise ValueError(f"fm.deform_not_solid: warped volume {vol_after:.6g} ≈ 0.")

        return SkillResult(
            body=Part(solid),
            history=EntityHistoryMap(rules={"body_faces": HistoryRule.MODIFIED_INHERIT}),
            extras={"deform": {
                "mode": args.mode,
                "twist_deg": args.twist_deg,
                "taper_ratio": args.taper_ratio,
                "refine": args.refine,
                "n_faces": n_faces,
                "volume_before_mm3": round(vol_before, 3),
                "volume_after_mm3": round(vol_after, 3),
                "volume_note": "approximate — converges as refine grows",
            }},
        )
