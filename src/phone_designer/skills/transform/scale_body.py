"""scale_body — atomic. Uniform or per-axis scaling of the working solid.

Scales the ACTUAL geometry (SolidWorks/Fusion/NX Scale Body) — unit conversion,
mold-shrink / print-shrink compensation, or a quick resize. Distinct from
``surface_offset`` (a normal-direction thickness offset, not proportional) and from
``import_step``'s scale (file-load only, uniform only, no per-axis / no centre).

Two modes:
  * UNIFORM (``factor`` set) — gp_Trsf.SetScale about ``center_mm`` → an exact
    similarity transform; volume scales by factor³.
  * ANISOTROPIC (``factors_xyz`` set) — a diagonal gp_GTrsf about ``center_mm`` via
    BRepBuilderAPI_GTransform; volume scales by sx·sy·sz. NURBS/analytic faces are
    converted as needed by the GTransform. Exactly one of factor / factors_xyz.
"""
from __future__ import annotations

from typing import Any, Optional

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
    # Adaptive-eps overload: the anisotropic GTransform path converts every face
    # to a B-spline, and the no-eps overload over-integrates those by ~0.9%.
    BRepGProp.VolumeProperties_s(shape, g, 1e-6)
    return abs(float(g.Mass()))


@skill(
    name="scale_body",
    category="transform",
    level="atomic",
    summary="Scale the working solid about center_mm — UNIFORM (factor, exact "
            "similarity, vol×factor³) or ANISOTROPIC (factors_xyz per-axis via "
            "gp_GTrsf, vol×sx·sy·sz). Exactly one of factor / factors_xyz. Unit "
            "conversion, mold/print-shrink compensation, resize. is_solid gated on "
            "volume>0.",
    selector_kinds=[],
    history_rules={"body_faces": HistoryRule.MODIFIED_INHERIT},
    produces_features=[],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.scale_mode_ambiguous", "fm.non_positive_factor",
                   "fm.scale_failed"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class ScaleBody(SkillBase):
    class Args(BaseModel):
        factor: Optional[float] = Field(
            default=None,
            description="UNIFORM scale factor (>0). Set this XOR factors_xyz. "
                        "0.5 halves, 2 doubles, 25.4 mm→inch reciprocal etc.")
        factors_xyz: Optional[tuple[float, float, float]] = Field(
            default=None,
            description="ANISOTROPIC per-axis factors (sx, sy, sz), each >0. Set "
                        "this XOR factor.")
        center_mm: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="Fixed point of the scaling (stays put). Default origin.")

        @model_validator(mode="after")
        def _one_mode(self):
            if (self.factor is None) == (self.factors_xyz is None):
                raise ValueError(
                    "fm.scale_mode_ambiguous: set EXACTLY one of factor "
                    "(uniform) or factors_xyz (anisotropic).")
            if self.factor is not None and self.factor <= 0:
                raise ValueError(
                    f"fm.non_positive_factor: factor={self.factor} must be > 0.")
            if self.factors_xyz is not None and any(f <= 0 for f in self.factors_xyz):
                raise ValueError(
                    f"fm.non_positive_factor: every factors_xyz must be > 0, got "
                    f"{self.factors_xyz}.")
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        if body is None:
            raise ValueError("fm.scale_failed: scale_body needs an input body.")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        cx, cy, cz = args.center_mm

        try:
            if args.factor is not None:
                from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
                from OCP.gp import gp_Pnt, gp_Trsf
                trsf = gp_Trsf()
                trsf.SetScale(gp_Pnt(cx, cy, cz), float(args.factor))
                out = BRepBuilderAPI_Transform(shape, trsf, True).Shape()
                expect_ratio = float(args.factor) ** 3
                mode = "uniform"
            else:
                from OCP.BRepBuilderAPI import BRepBuilderAPI_GTransform
                from OCP.gp import gp_GTrsf, gp_Mat, gp_XYZ
                sx, sy, sz = args.factors_xyz
                gt = gp_GTrsf()
                gt.SetVectorialPart(gp_Mat(sx, 0, 0, 0, sy, 0, 0, 0, sz))
                # translate so center_mm is the fixed point: p' = S·(p−c)+c
                gt.SetTranslationPart(gp_XYZ(cx * (1 - sx), cy * (1 - sy), cz * (1 - sz)))
                out = BRepBuilderAPI_GTransform(shape, gt, True).Shape()
                expect_ratio = sx * sy * sz
                mode = "anisotropic"
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.scale_failed: {type(exc).__name__}: {exc}")

        vol = _volume(out)
        if out is None or out.IsNull() or vol <= _EPS_VOL:
            raise ValueError(f"fm.scale_failed: result volume {vol:.6g}mm³ ≈ 0.")

        return SkillResult(
            body=Part(out),
            history=EntityHistoryMap(rules={"body_faces": HistoryRule.MODIFIED_INHERIT}),
            extras={"transform": {
                "op": "scale_body",
                "mode": mode,
                "factor": args.factor,
                "factors_xyz": list(args.factors_xyz) if args.factors_xyz else None,
                "center_mm": list(args.center_mm),
                "volume_mm3": round(vol, 3),
                "expected_volume_ratio": round(expect_ratio, 6),
            }},
        )
