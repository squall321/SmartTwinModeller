"""bass_reflex_port_flared — atomic. Flared bass-reflex tuning port pocket.

A tapered (flared) cylindrical Helmholtz-style port that opens narrow at the
back (throat) and wider at the front (mouth) to reduce port-noise turbulence
at high SPL. Two flare profiles:

  - "radius"      : the port radius follows a smooth quarter-circle blend from
                     throat to mouth. Mathematically:
                         r(s) = r_throat + (r_mouth - r_throat) * (1 - cos(pi*s/2))
                     where s = z / L runs 0..1. This is the radius-flared
                     profile preferred for woofer ports.
  - "exponential" : r(s) = r_throat * exp(m * z) with m = ln(r_mouth/r_throat)/L.

The port is cut into the target face. v1 supports planar +Z / -Z target faces.
Throat lies AT the face; the cylinder runs `length_mm` into the body.
"""
from __future__ import annotations

import math
from typing import Any, Literal

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
    name="bass_reflex_port_flared",
    category="modify/pocket",
    level="atomic",
    summary="Tapered (flared) bass-reflex tuning port — narrow throat, wider "
            "mouth — for reduced port turbulence. Flare profile is either a "
            "radius-blended quarter-cosine or an exponential. v1 supports "
            "planar +Z/-Z target faces; throat is at the face, mouth opens "
            "into the body at z = +length_mm.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "port_wall":       HistoryRule.GENERATED_NEW,
        "port_mouth_face": HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.throat_le_mouth",
    ],
    produces_features=["bass_reflex_port", "flared_port"],
    preserves=["outer_envelope_outside_port"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.8},
        "injection_mold_pa": {"min_wall_mm": 1.0, "min_draft_deg": 0.5},
        "sla_3dprint":       {"min_wall_mm": 0.6},
    },
    failure_modes=[
        "fm.throat_geq_mouth",
        "fm.port_exits_body",
    ],
    cost_hint=0.28,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class BassReflexPortFlared(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of port axis",
        )
        throat_d_mm: float = Field(default=25.0, gt=0, le=200)
        mouth_d_mm: float = Field(default=40.0, gt=0, le=400)
        length_mm: float = Field(default=80.0, gt=0, le=500)
        flare_profile: Literal["radius", "exponential"] = "radius"
        samples: int = Field(default=40, ge=8, le=400)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        if args.throat_d_mm >= args.mouth_d_mm:
            raise ValueError(
                f"bass_reflex_port_flared: throat_d ({args.throat_d_mm}) must "
                f"be < mouth_d ({args.mouth_d_mm})"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"bass_reflex_port_flared: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "bass_reflex_port_flared v1: planar +Z/-Z target faces only"
            )
        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]

        # Build a (z, r) profile in a local frame where z=0 sits at the face
        # and z grows INTO the body (towards -normal). Throat at z=0,
        # mouth at z = +length_mm.
        r_t = args.throat_d_mm / 2.0
        r_m = args.mouth_d_mm / 2.0
        L = args.length_mm
        pts = _flare_profile_points(args.flare_profile, r_t, r_m, L, args.samples)

        # Revolve in a canonical orientation (axis = world Z, profile points
        # at +z direction), then transform onto the actual face axis.
        port_solid_canonical = _revolve_flare_profile(pts)

        # Translate so that the small-end (throat) sits at face center, then
        # mirror in Z if the face normal points -Z.
        port_solid = _orient_port_to_face(
            port_solid_canonical, cx, cy, center[2], nz_sign,
        )

        cut = BRepAlgoAPI_Cut(shape, port_solid)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("bass_reflex_port_flared: boolean cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "port_wall":       HistoryRule.GENERATED_NEW,
                "port_mouth_face": HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)


# ── profile + revolve helpers ────────────────────────────────────────────────


def _flare_profile_points(
    kind: str, r_throat: float, r_mouth: float, L: float, samples: int,
) -> list[tuple[float, float]]:
    n = max(samples, 8)
    if kind == "radius":
        return [
            (
                L * i / (n - 1),
                r_throat + (r_mouth - r_throat) * (
                    1.0 - math.cos(math.pi * 0.5 * (i / (n - 1)))
                ),
            )
            for i in range(n)
        ]
    if kind == "exponential":
        m = math.log(r_mouth / r_throat) / L
        return [
            (L * i / (n - 1),
             r_throat * math.exp(m * (L * i / (n - 1))))
            for i in range(n)
        ]
    raise ValueError(f"bass_reflex_port_flared: unknown flare '{kind}'")


def _revolve_flare_profile(pts: list[tuple[float, float]]):
    """Revolve (z, r) polyline around world Z to build the port cutter solid.

    Slightly oversizes the mouth end by 0.5 mm so the boolean cut produces a
    crisp opening edge at the face level (throat side) — z_first is pulled
    upward by `overshoot`.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt

    overshoot = 0.5
    z_first, _ = pts[0]
    z_last = pts[-1][0]

    poly = BRepBuilderAPI_MakePolygon()
    poly.Add(gp_Pnt(0.0, 0.0, z_first - overshoot))
    for (z, r) in pts:
        poly.Add(gp_Pnt(r, 0.0, z if z != z_first else z - overshoot))
    poly.Add(gp_Pnt(0.0, 0.0, z_last))
    poly.Close()
    wire = poly.Wire()
    face = BRepBuilderAPI_MakeFace(wire, True).Face()

    axis = gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    mk = BRepPrimAPI_MakeRevol(face, axis, 2.0 * math.pi, True)
    if not mk.IsDone():
        raise RuntimeError(
            "bass_reflex_port_flared: revolve of flare profile failed",
        )
    return mk.Shape()


def _orient_port_to_face(canonical_shape, cx: float, cy: float, cz: float,
                          nz_sign: float):
    """Position the canonical port solid (z=0 small-end at origin) onto the
    target face. nz_sign > 0 means face normal is +Z so the port cuts in -Z;
    we mirror Z so the small-end aligns with the face and the cutter runs
    INTO the body.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Mat, gp_Trsf, gp_Vec, gp_GTrsf

    # Step 1: mirror in z if face normal points +Z so the cutter goes -Z.
    shape = canonical_shape
    if nz_sign > 0:
        mirror = gp_Trsf()
        # Mirror across plane z = 0 by negating Z scale via a rotation
        # around X by π would also flip Y — use explicit Z-flip via a
        # GTrsf affine instead.
        gt = gp_GTrsf()
        gt.SetValue(1, 1, 1.0); gt.SetValue(1, 2, 0.0); gt.SetValue(1, 3, 0.0); gt.SetValue(1, 4, 0.0)  # noqa: E702
        gt.SetValue(2, 1, 0.0); gt.SetValue(2, 2, 1.0); gt.SetValue(2, 3, 0.0); gt.SetValue(2, 4, 0.0)  # noqa: E702
        gt.SetValue(3, 1, 0.0); gt.SetValue(3, 2, 0.0); gt.SetValue(3, 3, -1.0); gt.SetValue(3, 4, 0.0)  # noqa: E702
        from OCP.BRepBuilderAPI import BRepBuilderAPI_GTransform
        xf = BRepBuilderAPI_GTransform(shape, gt, True)
        xf.Build()
        if not xf.IsDone():
            raise RuntimeError(
                "bass_reflex_port_flared: Z mirror of canonical port failed",
            )
        shape = xf.Shape()

    # Step 2: translate so origin → (cx, cy, cz).
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(cx, cy, cz))
    tr = BRepBuilderAPI_Transform(shape, t, True)
    tr.Build()
    if not tr.IsDone():
        raise RuntimeError(
            "bass_reflex_port_flared: translate of canonical port failed",
        )
    return tr.Shape()
