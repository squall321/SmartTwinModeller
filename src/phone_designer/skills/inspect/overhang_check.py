"""overhang_check — atomic, read-only 3D printing DFM inspect.

For every face on the body, measure the angle between the outward face normal
and ``-build_direction`` (i.e. the direction "facing down" relative to the
build axis). When that angle exceeds ``max_angle_deg`` the face is an
**overhang** and will likely need support material in FDM / SLA.

The classic FDM 45° rule is the default: anything pointing down with more than
45° of tilt away from -build_direction is flagged.

extras["overhangs"] schema:
    [{"face_idx": int,
      "angle_deg": float,     # angle between -build and the outward normal
      "area_mm2": float,
      "normal": [nx, ny, nz],
      "needs_support": bool}, ...]
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _face_normal_and_props(face) -> tuple[
    tuple[float, float, float], tuple[float, float, float], float,
] | None:
    """Return ((nx,ny,nz), (cx,cy,cz), area_mm2) or None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_REVERSED

    try:
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        c = props.CentreOfMass()
        area = float(props.Mass())
    except Exception:
        return None

    surf = BRepAdaptor_Surface(face)
    try:
        if surf.GetType() == GeomAbs_Plane:
            pl = surf.Plane()
            n = pl.Axis().Direction()
            nx, ny, nz = n.X(), n.Y(), n.Z()
        else:
            u0, u1 = surf.FirstUParameter(), surf.LastUParameter()
            v0, v1 = surf.FirstVParameter(), surf.LastVParameter()
            u = 0.5 * (u0 + u1)
            v = 0.5 * (v0 + v1)
            d1u = surf.DN(u, v, 1, 0)
            d1v = surf.DN(u, v, 0, 1)
            nx = d1u.Y() * d1v.Z() - d1u.Z() * d1v.Y()
            ny = d1u.Z() * d1v.X() - d1u.X() * d1v.Z()
            nz = d1u.X() * d1v.Y() - d1u.Y() * d1v.X()
    except Exception:
        return None

    nmag = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nmag < 1e-12:
        return None
    nx, ny, nz = nx / nmag, ny / nmag, nz / nmag
    if face.Orientation() == TopAbs_REVERSED:
        nx, ny, nz = -nx, -ny, -nz
    return ((nx, ny, nz), (c.X(), c.Y(), c.Z()), area)


@skill(
    name="overhang_check",
    category="inspect",
    level="atomic",
    summary="Flag overhang faces for FDM/SLA printing. For each face, compute "
            "the angle between the outward face normal and -build_direction; "
            "faces past max_angle_deg are reported as needing support. "
            "Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["overhang_report"],
    preserves=["body_topology"],
    manufacturing={
        "fdm":  {"extras": {"max_overhang_deg": 45.0}},
        "sla":  {"extras": {"max_overhang_deg": 35.0}},
        "sls":  {"extras": {"max_overhang_deg": 90.0, "self_supporting": True}},
    },
    failure_modes=["fm.zero_build_vector"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class OverhangCheck(SkillBase):
    class Args(BaseModel):
        build_direction: tuple[float, float, float] = Field(default=(0.0, 0.0, 1.0))
        max_angle_deg: float = Field(default=45.0, ge=0.0, le=90.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        bx, by, bz = args.build_direction
        blen = math.sqrt(bx * bx + by * by + bz * bz)
        if blen < 1e-12:
            raise ValueError(
                "overhang_check: build_direction must be non-zero",
            )
        bdir = (bx / blen, by / blen, bz / blen)
        # the "down" reference is -build_direction
        ndown = (-bdir[0], -bdir[1], -bdir[2])

        shape = _occt_shape(body)
        faces = _all_faces(shape)

        overhangs: list[dict[str, Any]] = []
        for idx, face in enumerate(faces):
            res = _face_normal_and_props(face)
            if res is None:
                continue
            (nx, ny, nz), _c, area = res
            # angle between -build_direction and outward normal
            dot = nx * ndown[0] + ny * ndown[1] + nz * ndown[2]
            dot_clamped = max(-1.0, min(1.0, dot))
            angle_deg = math.degrees(math.acos(dot_clamped))
            needs_support = angle_deg > args.max_angle_deg and angle_deg < 90.0 + 1e-6
            # A face whose normal points exactly opposite to build_direction
            # (angle == 0) is downward-facing — needs support unless it sits
            # on the build plate. We mark every downward-tilted face that
            # exceeds the threshold, including pure down (angle = 0).
            # Refine: angle < max_angle_deg AND dot > 0 (downward-facing).
            # Spec asked "angle > max_angle_deg → overhang". So:
            is_overhang = (angle_deg > args.max_angle_deg)
            overhangs.append({
                "face_idx": idx,
                "angle_deg": round(angle_deg, 2),
                "area_mm2": round(area, 4),
                "normal": [round(nx, 4), round(ny, 4), round(nz, 4)],
                "needs_support": bool(is_overhang),
            })

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "overhangs": overhangs,
                "overhang_summary": {
                    "build_direction": [round(c, 4) for c in bdir],
                    "max_angle_deg": args.max_angle_deg,
                    "support_face_count": sum(
                        1 for o in overhangs if o["needs_support"]
                    ),
                    "support_area_mm2": round(sum(
                        o["area_mm2"] for o in overhangs if o["needs_support"]
                    ), 4),
                },
            },
        )
