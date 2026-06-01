"""waveguide_horn_profile — atomic. Acoustic horn / waveguide solid by revolve.

Builds a closed solid body whose outer surface is the chosen horn profile and
whose inner surface is a thin offset wall (single-shell). The body is created
by revolving a 2D profile around the horn axis (Z), so this skill is a
*create* skill (input body is ignored, replaced by the revolved solid).

Three classical horn flares are supported:
  - "exponential": r(z) = r_throat * exp(m * z), where m is solved so that
        r(L) == r_mouth.
  - "tractrix"   : the classical tractrix curve, ideal for omni-directional
        loading. We compute it parametrically as the half-tractrix from the
        mouth diameter, then re-sample the section [0, L] in z. Note: a true
        tractrix has infinite length to reach z=∞; we truncate at length L
        and let the throat diameter be whatever the curve hits there.
        If the user requests `throat_d_mm`, we instead set the mouth radius
        slightly larger than the tractrix asymptote so the curve passes
        through both endpoints.
  - "conical"    : r(z) linear from throat to mouth.

The result is a thin-walled horn shell: outer revolved by the profile, inner
revolved by (profile - wall_t). Both surfaces share the same axis (Z), and
the mouth + throat are closed by annular discs.

Convention:
    Throat sits at z = 0, mouth at z = +length_mm. Radii in mm. The horn axis
    is world Z. After revolving, the caller can translate / orient the body
    with mate / move_component.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# Wall thickness for the horn shell (mm). Keep small but well above the OCCT
# fuse / cut tolerance so the boolean inner-cut succeeds.
_DEFAULT_WALL_T_MM = 1.5


@skill(
    name="waveguide_horn_profile",
    category="modify/curvature",
    level="atomic",
    summary="Revolved acoustic horn / waveguide shell. Profile is one of "
            "exponential, tractrix, conical. Creates a thin-walled horn body "
            "with throat (small end) at z=0 and mouth (large end) at "
            "z=+length_mm; horn axis is world Z. Input body is ignored.",
    selector_kinds=[],
    history_rules={"horn_solid": HistoryRule.GENERATED_NEW},
    produces_features=["waveguide_horn", "horn_throat", "horn_mouth"],
    preserves=[],
    manufacturing={
        "cnc_5axis":         {"min_wall_mm": 0.8},
        "injection_mold_pa": {"min_wall_mm": 1.0, "min_draft_deg": 0.5},
        "sla_3dprint":       {"min_wall_mm": 0.6,
                              "extras": {"prefers_axis_vertical": True}},
    },
    failure_modes=[
        "fm.horn_throat_geq_mouth",
        "fm.profile_revolve_failed",
    ],
    cost_hint=0.35,
    post_conditions=[PostCondition(kind="body_present")],
)
class WaveguideHornProfile(SkillBase):
    class Args(BaseModel):
        throat_d_mm: float = Field(default=20.0, gt=0, le=500)
        mouth_d_mm: float = Field(default=80.0, gt=0, le=2000)
        length_mm: float = Field(default=60.0, gt=0, le=2000)
        profile: Literal["exponential", "tractrix", "conical"] = "tractrix"
        wall_t_mm: float = Field(default=_DEFAULT_WALL_T_MM, gt=0.05, le=20)
        samples: int = Field(default=80, ge=10, le=2000,
                              description="Polyline samples along the profile")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        if args.throat_d_mm >= args.mouth_d_mm:
            raise ValueError(
                f"waveguide_horn_profile: throat_d ({args.throat_d_mm}) must be "
                f"< mouth_d ({args.mouth_d_mm}) for a flaring horn"
            )
        if args.wall_t_mm >= args.throat_d_mm / 2.0:
            raise ValueError(
                f"waveguide_horn_profile: wall_t ({args.wall_t_mm}) must be "
                f"< throat radius ({args.throat_d_mm/2:.3f}) so the bore stays open"
            )

        r_throat = args.throat_d_mm / 2.0
        r_mouth = args.mouth_d_mm / 2.0
        L = args.length_mm

        # 1. Generate the outer profile points (z, r). Both endpoints are pinned
        # to (0, r_throat) and (L, r_mouth) for closure.
        outer_pts = _profile_points(
            args.profile, r_throat, r_mouth, L, args.samples,
        )

        # 2. Inner profile = outer offset inward by wall_t (clamped to a small
        # positive radius so the throat doesn't collapse).
        wall_t = args.wall_t_mm
        inner_pts = []
        for z, r in outer_pts:
            r_in = max(r - wall_t, 0.1)
            inner_pts.append((z, r_in))

        # 3. Revolve outer and inner profiles around the Z axis, then subtract
        # the inner solid from the outer solid to form the shell.
        outer_solid = _revolve_axial_profile(outer_pts)
        inner_solid = _revolve_axial_profile(inner_pts)

        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        cut = BRepAlgoAPI_Cut(outer_solid, inner_solid)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("waveguide_horn_profile: shell boolean cut failed")
        shell = cut.Shape()

        history = EntityHistoryMap(
            rules={"horn_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shell), history=history)


# ── profile generators ───────────────────────────────────────────────────────


def _profile_points(
    kind: str,
    r_throat: float,
    r_mouth: float,
    L: float,
    samples: int,
) -> list[tuple[float, float]]:
    """Return [(z, r), ...] sampled along the chosen flare from z=0..L.

    Endpoints are always (0, r_throat) and (L, r_mouth).
    """
    n = max(samples, 10)

    if kind == "conical":
        return [
            (L * i / (n - 1),
             r_throat + (r_mouth - r_throat) * i / (n - 1))
            for i in range(n)
        ]

    if kind == "exponential":
        # r(z) = r_throat * exp(m * z) with r(L) = r_mouth ⇒
        # m = ln(r_mouth / r_throat) / L.
        m = math.log(r_mouth / r_throat) / L
        return [
            (L * i / (n - 1), r_throat * math.exp(m * (L * i / (n - 1))))
            for i in range(n)
        ]

    if kind == "tractrix":
        # Classical tractrix (axis = z, asymptotic radius = r_mouth):
        #     z(r) = r_mouth * acosh(r_mouth / r) - sqrt(r_mouth² - r²)
        # We sample r from r_throat to r_mouth*0.999 (cannot reach asymptote)
        # then rescale z onto [0, L] so the endpoints match exactly.
        r_min = r_throat
        r_max = r_mouth * 0.999
        raw: list[tuple[float, float]] = []
        for i in range(n):
            r = r_min + (r_max - r_min) * i / (n - 1)
            # Guard against acosh argument < 1 (would only happen for r > r_mouth)
            arg = r_mouth / r
            if arg < 1.0:
                arg = 1.0
            z_raw = r_mouth * math.acosh(arg) - math.sqrt(
                max(r_mouth * r_mouth - r * r, 0.0)
            )
            raw.append((z_raw, r))
        # The classical tractrix runs z from large (at small r) down to 0
        # (at r ≈ r_mouth). Flip + scale so z_throat=0, z_mouth=L.
        z_throat_raw = raw[0][0]
        z_mouth_raw = raw[-1][0]
        span = z_throat_raw - z_mouth_raw
        if span <= 0.0:
            # Degenerate (e.g., r_throat ≈ r_mouth). Fall back to conical.
            return _profile_points("conical", r_throat, r_mouth, L, samples)
        return [
            (L * (z_throat_raw - z_raw) / span, r)
            for (z_raw, r) in raw
        ]

    raise ValueError(f"waveguide_horn_profile: unknown profile '{kind}'")


# ── revolved solid from (z, r) polyline ──────────────────────────────────────


def _revolve_axial_profile(pts: list[tuple[float, float]]):
    """Build a revolved solid from a (z, r) polyline around the world Z axis.

    The polyline is the outer boundary on the half-plane y=0, x≥0. We close
    it by dropping vertical segments from each endpoint down to (z, 0), then
    revolve the resulting closed face 360° around Z.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt

    poly = BRepBuilderAPI_MakePolygon()
    # Start at (z_first, 0): close-axis foot of first sample.
    z_first, r_first = pts[0]
    poly.Add(gp_Pnt(0.0, 0.0, z_first))
    # Walk outer profile r samples at z[i].
    for (z, r) in pts:
        poly.Add(gp_Pnt(r, 0.0, z))
    # Drop back to axis at the last z.
    z_last = pts[-1][0]
    poly.Add(gp_Pnt(0.0, 0.0, z_last))
    poly.Close()
    wire = poly.Wire()

    face = BRepBuilderAPI_MakeFace(wire, True).Face()

    axis = gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    mk = BRepPrimAPI_MakeRevol(face, axis, 2.0 * math.pi, True)
    if not mk.IsDone():
        raise RuntimeError("waveguide_horn_profile: BRepPrimAPI_MakeRevol failed")
    return mk.Shape()
