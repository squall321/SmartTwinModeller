"""body_orientation_check — diagnostic for inverted-shell topology.

COMPLEX-CAD pass-6 (2026-06-09): pythonocc__11752.stp imports with a
*signed* volume of -732 mm³ from BRepGProp because every face on the
shell carries TopAbs_REVERSED. The magnitude (|Mass|) is ~4.6 G mm³,
but the negative sign made every volume_decreased post-condition gate
behave unpredictably until we restored the abs() guard.

A first-class inspection skill makes the condition discoverable BEFORE
any reverse-engineering plan runs, so the planner / runner can warn the
user (or auto-flip the orientation) rather than silently producing
broken cuts.

extras schema::

    extras["body_orientation"] = {
        "signed_volume_mm3": float,    # BRepGProp.Mass() — signed
        "magnitude_mm3":     float,    # |Mass|
        "bbox_volume_mm3":   float,    # outer-bbox volume (sanity floor)
        "solidity_ratio":    float,    # |Mass| / bbox_volume_mm3
        "inverted":          bool,     # True if signed < 0 AND |Mass| > 0
        "advice":            str | None,
    }

post_conditions = [body_present]. The body itself is returned unchanged.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return getattr(body, "wrapped", body)


def _signed_volume_mm3(shape) -> float | None:
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        p = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, p)
        return float(p.Mass())
    except Exception:
        return None


def _bbox_volume_mm3(shape) -> float | None:
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        bb = Bnd_Box()
        try:
            BRepBndLib.AddOptimal_s(shape, bb)
        except Exception:
            BRepBndLib.Add_s(shape, bb)
        if bb.IsVoid():
            return None
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        return float(xmax - xmin) * float(ymax - ymin) * float(zmax - zmin)
    except Exception:
        return None


@skill(
    name="body_orientation_check",
    category="inspect",
    level="atomic",
    summary="Check whether the body's signed BRepGProp volume disagrees with "
            "its bbox — i.e. consistently REVERSED face orientation. Surfaces "
            "the 11752-class import bug before any RE plan runs.",
    selector_kinds=[],
    history_rules={},
    produces_features=["body_orientation_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class BodyOrientationCheck(SkillBase):
    class Args(BaseModel):
        min_magnitude_mm3: float = Field(
            default=1e-3, ge=0.0,
            description="Bodies whose |Mass| is below this floor are flagged "
                        "as 'empty' rather than 'inverted'.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        shape = _occt_shape(body)
        if shape is None:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"body_orientation": {
                    "signed_volume_mm3": None,
                    "magnitude_mm3": None,
                    "bbox_volume_mm3": None,
                    "solidity_ratio": None,
                    "inverted": False,
                    "advice": "no body to check",
                }},
            )

        signed = _signed_volume_mm3(shape)
        bbox_v = _bbox_volume_mm3(shape)
        magnitude = abs(signed) if signed is not None else None
        solidity = (
            magnitude / bbox_v
            if magnitude is not None and bbox_v and bbox_v > 0
            else None
        )

        inverted = False
        advice: str | None = None
        if signed is None:
            advice = "BRepGProp.VolumeProperties failed — body may be malformed"
        elif magnitude is not None and magnitude < args.min_magnitude_mm3:
            advice = (
                f"body magnitude {magnitude:.3e} mm³ below "
                f"{args.min_magnitude_mm3:.3e} — body is empty or sheet-like"
            )
        elif signed < 0:
            inverted = True
            advice = (
                "INVERTED SHELL — BRepGProp returns negative signed volume "
                "(magnitude is correct). Boolean cuts will still work because "
                "the planner uses abs() internally, but any external consumer "
                "comparing signed volumes will misbehave. Consider running "
                "ShapeFix_Shell or re-importing with face-orientation repair."
            )

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"body_orientation": {
                "signed_volume_mm3": signed,
                "magnitude_mm3": magnitude,
                "bbox_volume_mm3": bbox_v,
                "solidity_ratio": round(solidity, 6) if solidity is not None else None,
                "inverted": inverted,
                "advice": advice,
            }},
        )
