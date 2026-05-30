"""support_volume_estimate — atomic, read-only 3D printing DFM inspect.

Rough estimate of the volume of support material needed to print the body in
a given build orientation. For each overhang face (angle past the threshold
relative to -build_direction):

    projected_area = face_area_mm2 * |dot(face_normal, build_direction)|
    drop_height    = max(0, face_centroid_proj - build_plate_proj)
    support_volume = projected_area * drop_height * support_density

The build plate is taken as the body's minimum extent along ``build_direction``.
``support_density`` (0..1) accounts for the fact that printer slicers fill
support regions with a sparse pattern (FDM ~20%, SLA tree supports ~5–10%).

extras["support_estimate"] schema:
    {
        "total_support_volume_mm3": float,
        "build_direction": [bx, by, bz],
        "max_overhang_deg": float,
        "support_density": float,
        "support_face_count": int,
        "support_height_per_face": [
            {"face_idx": int, "height_mm": float, "volume_mm3": float}, ...
        ],
    }
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


def _face_normal_centroid_area(face):
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


def _project_extents(shape, bdir: tuple[float, float, float]) -> tuple[float, float]:
    """Return (min_proj, max_proj) of the body along the unit build direction.

    Iterates all vertices for a robust extreme. Empty shape → (0, 0).
    """
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopTools import TopTools_MapOfShape

    seen = TopTools_MapOfShape()
    pmin = math.inf
    pmax = -math.inf
    it = TopExp_Explorer(shape, TopAbs_VERTEX)
    while it.More():
        v = it.Current()
        if not seen.Contains(v):
            seen.Add(v)
            try:
                p = BRep_Tool.Pnt_s(v)  # type: ignore[attr-defined]
                pv = p.X() * bdir[0] + p.Y() * bdir[1] + p.Z() * bdir[2]
                if pv < pmin:
                    pmin = pv
                if pv > pmax:
                    pmax = pv
            except Exception:
                pass
        it.Next()
    if not math.isfinite(pmin):
        return (0.0, 0.0)
    return (pmin, pmax)


@skill(
    name="support_volume_estimate",
    category="inspect",
    level="atomic",
    summary="Estimate total support material volume needed to print the body "
            "by projecting every overhang face down to the build plate and "
            "multiplying projected area by drop height and support density. "
            "Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["support_volume_report"],
    preserves=["body_topology"],
    manufacturing={
        "fdm":  {"extras": {"support_density_default": 0.2}},
        "sla":  {"extras": {"support_density_default": 0.1}},
        "sls":  {"extras": {"support_density_default": 0.0, "self_supporting": True}},
    },
    failure_modes=["fm.zero_build_vector"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class SupportVolumeEstimate(SkillBase):
    class Args(BaseModel):
        build_direction: tuple[float, float, float] = Field(default=(0.0, 0.0, 1.0))
        max_overhang_deg: float = Field(default=45.0, ge=0.0, le=90.0)
        support_density: float = Field(default=0.2, ge=0.0, le=1.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        bx, by, bz = args.build_direction
        blen = math.sqrt(bx * bx + by * by + bz * bz)
        if blen < 1e-12:
            raise ValueError(
                "support_volume_estimate: build_direction must be non-zero",
            )
        bdir = (bx / blen, by / blen, bz / blen)
        ndown = (-bdir[0], -bdir[1], -bdir[2])

        shape = _occt_shape(body)
        plate_min, _plate_max = _project_extents(shape, bdir)

        faces = _all_faces(shape)
        per_face: list[dict[str, Any]] = []
        total_vol = 0.0
        for idx, face in enumerate(faces):
            res = _face_normal_centroid_area(face)
            if res is None:
                continue
            (nx, ny, nz), (cx, cy, cz), area = res
            # angle vs -build_direction
            dot_ndown = nx * ndown[0] + ny * ndown[1] + nz * ndown[2]
            dot_clamped = max(-1.0, min(1.0, dot_ndown))
            angle_deg = math.degrees(math.acos(dot_clamped))
            if angle_deg <= args.max_overhang_deg:
                continue
            # face is an overhang. Projected footprint along build_direction:
            #   projected_area = area * |dot(normal, build_direction)|
            # (this is the area of the face projected onto a plane perpendicular
            # to the build direction)
            dot_build = nx * bdir[0] + ny * bdir[1] + nz * bdir[2]
            projected_area = area * abs(dot_build)
            # drop height: distance from face centroid down to the build plate
            cproj = cx * bdir[0] + cy * bdir[1] + cz * bdir[2]
            drop_h = max(0.0, cproj - plate_min)
            vol = projected_area * drop_h * args.support_density
            total_vol += vol
            per_face.append({
                "face_idx": idx,
                "height_mm": round(drop_h, 4),
                "projected_area_mm2": round(projected_area, 4),
                "volume_mm3": round(vol, 4),
            })

        report = {
            "total_support_volume_mm3": round(total_vol, 4),
            "build_direction": [round(c, 4) for c in bdir],
            "max_overhang_deg": args.max_overhang_deg,
            "support_density": args.support_density,
            "support_face_count": len(per_face),
            "support_height_per_face": per_face,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"support_estimate": report},
        )
