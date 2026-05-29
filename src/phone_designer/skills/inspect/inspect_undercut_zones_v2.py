"""inspect_undercut_zones_v2 — atomic, read-only DFM inspect.

For each face of the body, compute its outward normal and dot it with the
given mold ``pull_direction``. When dot < -0.1 the face points "back" against
the pull and is flagged as an undercut — those zones require side-action
tooling (slides / lifters / collapsing cores) to mold.

This v2 returns a flat, compact list suitable for downstream tooling planners:

    extras["undercut_faces"] = [
        {"idx": int,
         "area": float,           # mm²
         "normal": [nx, ny, nz],  # outward, unit
         "point": [x, y, z]},     # centre of mass of the face
        ...
    ]

Body is unchanged (post_condition body_present).
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


def _face_outward_normal_centroid(face) -> tuple[
    tuple[float, float, float], tuple[float, float, float], float,
] | None:
    """Return ((nx, ny, nz), (cx, cy, cz), area_mm2) or None on failure.

    For planar faces uses the analytic plane normal; for non-planar faces
    samples the surface normal at the parametric mid-point.
    Face orientation is applied so the returned normal is *outward* on the
    solid.
    """
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


_UNDERCUT_DOT_THRESHOLD = -0.1


@skill(
    name="inspect_undercut_zones_v2",
    category="inspect",
    level="atomic",
    summary="Find every face whose outward normal dotted with the mold "
            "pull_direction is < -0.1 — those are undercut and require "
            "side-action tooling. Returns face indices, areas, outward "
            "normals and sample points. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["undercut_report"],
    preserves=["body_topology"],
    manufacturing={
        "die_cast_al":       {"extras": {"undercut_allowed": False}},
        "injection_mold_pa": {"extras": {"undercut_allowed": False}},
    },
    failure_modes=["fm.zero_pull_vector"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class InspectUndercutZonesV2(SkillBase):
    class Args(BaseModel):
        pull_direction: tuple[float, float, float] = Field(default=(0.0, 0.0, 1.0))

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        px, py, pz = args.pull_direction
        plen = math.sqrt(px * px + py * py + pz * pz)
        if plen < 1e-12:
            raise ValueError(
                "inspect_undercut_zones_v2: pull_direction must be non-zero",
            )
        pdir = (px / plen, py / plen, pz / plen)

        shape = _occt_shape(body)
        faces = _all_faces(shape)

        undercut: list[dict[str, Any]] = []
        for idx, face in enumerate(faces):
            res = _face_outward_normal_centroid(face)
            if res is None:
                continue
            (nx, ny, nz), (cx, cy, cz), area = res
            dot = nx * pdir[0] + ny * pdir[1] + nz * pdir[2]
            if dot < _UNDERCUT_DOT_THRESHOLD:
                undercut.append({
                    "idx": idx,
                    "area": round(area, 4),
                    "normal": [round(nx, 4), round(ny, 4), round(nz, 4)],
                    "point": [round(cx, 4), round(cy, 4), round(cz, 4)],
                })

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"undercut_faces": undercut},
        )
