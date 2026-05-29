"""core_cavity_split — atomic. Split body into two halves based on parting plane.

Build two half-spaces (above and below parting_z) and intersect each with the
body via BRepAlgoAPI_Common. The two resulting solids are exposed via
``result.extras["core_solid"]`` and ``result.extras["cavity_solid"]``. The body
itself is returned unchanged so downstream skills still see the original geometry.

By convention (for pull_direction="+Z"):
    core_solid   = body ∩ half-space above parting plane (the +Z side)
    cavity_solid = body ∩ half-space below parting plane (the -Z side)

For pull_direction="-Z" the roles are swapped.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _xy_bbox(shape) -> tuple[float, float, float, float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return (-1.0, -1.0, 1.0, 1.0)
    xmin, ymin, _zmin, xmax, ymax, _zmax = bb.Get()
    return (xmin, ymin, xmax, ymax)


def _half_space_box(
    xmin: float, ymin: float, xmax: float, ymax: float,
    z_lo: float, z_hi: float,
):
    """Build a finite box covering [xmin..xmax]×[ymin..ymax]×[z_lo..z_hi].

    Used as a half-space surrogate for BRepAlgoAPI_Common. Far simpler and
    more robust than BRepPrimAPI_MakeHalfSpace for thin parting use.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(
        gp_Pnt(xmin, ymin, z_lo),
        gp_Pnt(xmax, ymax, z_hi),
    ).Shape()


@skill(
    name="core_cavity_split",
    category="modify/mold",
    level="atomic",
    summary="Split the body into core and cavity halves at a parting plane "
            "via BRepAlgoAPI_Common with two half-spaces. Body is returned "
            "unchanged; the two solids are exposed in result.extras "
            "('core_solid', 'cavity_solid').",
    selector_kinds=[],
    history_rules={},
    produces_features=["core_solid", "cavity_solid"],
    preserves=["body_topology"],
    manufacturing={
        "die_cast_al":       {"min_draft_deg": 1.0},
        "injection_mold_pa": {"min_draft_deg": 0.5},
    },
    failure_modes=["fm.parting_plane_outside_body", "fm.boolean_common_failed"],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class CoreCavitySplit(SkillBase):
    class Args(BaseModel):
        parting_z_mm: float
        extension_mm: float = Field(default=20.0, ge=0.0, le=1000.0)
        pull_direction: Literal["+Z", "-Z"] = "+Z"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

        shape = _occt_shape(body)
        if shape is None:
            raise RuntimeError("core_cavity_split: body is None")

        # Bbox + extension to size the half-space boxes.
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        bb = Bnd_Box()
        try:
            BRepBndLib.AddOptimal_s(shape, bb)
        except Exception:
            BRepBndLib.Add_s(shape, bb)
        if bb.IsVoid():
            raise RuntimeError("core_cavity_split: body bbox is void")
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        ex = args.extension_mm
        x_lo, x_hi = xmin - ex, xmax + ex
        y_lo, y_hi = ymin - ex, ymax + ex
        z_lo = zmin - ex
        z_hi = zmax + ex
        z_p = args.parting_z_mm

        # Upper half (z >= parting_z) and lower half (z <= parting_z).
        upper_hs = _half_space_box(x_lo, y_lo, x_hi, y_hi, z_p, z_hi)
        lower_hs = _half_space_box(x_lo, y_lo, x_hi, y_hi, z_lo, z_p)

        common_upper = BRepAlgoAPI_Common(shape, upper_hs)
        common_upper.Build()
        if not common_upper.IsDone():
            raise RuntimeError("core_cavity_split: upper Common failed")
        upper_solid = common_upper.Shape()

        common_lower = BRepAlgoAPI_Common(shape, lower_hs)
        common_lower.Build()
        if not common_lower.IsDone():
            raise RuntimeError("core_cavity_split: lower Common failed")
        lower_solid = common_lower.Shape()

        # Map to core/cavity by pull direction.
        if args.pull_direction == "+Z":
            core_solid, cavity_solid = upper_solid, lower_solid
        else:
            core_solid, cavity_solid = lower_solid, upper_solid

        extras = {
            "core_solid": core_solid,
            "cavity_solid": cavity_solid,
            "parting_z_mm": args.parting_z_mm,
            "pull_direction": args.pull_direction,
        }

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
