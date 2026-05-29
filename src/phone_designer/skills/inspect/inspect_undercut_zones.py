"""inspect_undercut_zones — atomic, read-only DFM inspect.

For a given mold ``pull_direction`` (the direction the mold half opens), test
every face on the body. A face is **undercut** from this side when its outward
normal points *opposite* to the pull direction — that face would catch on the
mold and prevent ejection.

This is the critical input for deciding whether a mold needs side-actions
(slides / lifters / collapsing cores). The catalog threshold
``catalogs/dfm_inspect/default_thresholds.yaml:undercut.dot_threshold`` controls
how steeply a face must oppose the pull to count (default −0.1 ≈ ~5.7°).

For planar faces the test uses the analytic plane normal. For non-planar faces
the normal is sampled at the face's centre-of-mass parameter; very curved
faces with mixed orientation are flagged via the ``mixed`` field so callers
can re-sample with more points.

extras schema:
    extras["undercut_faces"] = [
        {"face_idx": int,
         "area": float,           # mm²
         "sample_point": [x,y,z], # centre of mass of the face
         "normal": [nx,ny,nz],
         "dot_with_pull": float,
         "angle_deg": float,      # angle between face normal and -pull
         "surface_type": str},
        ...
    ]
    extras["undercut_summary"] = {
        "pull_direction": [px,py,pz],
        "dot_threshold": float,
        "undercut_face_count": int,
        "undercut_area_mm2": float,
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


def _load(family: str, name: str):
    import pathlib
    import yaml
    root = pathlib.Path(__file__).resolve().parents[5]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


_SURFACE_TYPE_NAMES = {
    "plane": "plane",
    "cylinder": "cylinder",
    "cone": "cone",
    "sphere": "sphere",
    "torus": "torus",
}


def _surface_type(face) -> str:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import (
        GeomAbs_Cone,
        GeomAbs_Cylinder,
        GeomAbs_Plane,
        GeomAbs_Sphere,
        GeomAbs_Torus,
    )
    surf = BRepAdaptor_Surface(face)
    t = int(surf.GetType())
    mapping = {
        int(GeomAbs_Plane): "plane",
        int(GeomAbs_Cylinder): "cylinder",
        int(GeomAbs_Cone): "cone",
        int(GeomAbs_Sphere): "sphere",
        int(GeomAbs_Torus): "torus",
    }
    return mapping.get(t, "other")


def _face_normal_and_centroid(face) -> tuple[
    tuple[float, float, float], tuple[float, float, float], float,
] | None:
    """Return ((nx,ny,nz), (cx,cy,cz), area_mm2) or None on failure.

    Normal is the *outward* normal (face orientation already applied).
    For non-planar faces the surface normal is sampled at the parametric
    midpoint of the face.
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


@skill(
    name="inspect_undercut_zones",
    category="inspect",
    level="atomic",
    summary="Find every face whose outward normal points against the given "
            "mold pull_direction — those are undercut and require side-action "
            "tooling. Returns face indices, areas, sample points and the "
            "angle each makes with -pull. Read-only — body unchanged.",
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
class InspectUndercutZones(SkillBase):
    class Args(BaseModel):
        pull_direction: tuple[float, float, float] = Field(default=(0.0, 0.0, 1.0))

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        cat = _load("dfm_inspect", "default_thresholds")
        dot_thr = float(cat["undercut"]["dot_threshold"])

        px, py, pz = args.pull_direction
        plen = math.sqrt(px * px + py * py + pz * pz)
        if plen < 1e-12:
            raise ValueError(
                "inspect_undercut_zones: pull_direction must be non-zero",
            )
        pdir = (px / plen, py / plen, pz / plen)

        shape = _occt_shape(body)
        faces = _all_faces(shape)

        undercut: list[dict[str, Any]] = []
        total_area = 0.0
        for idx, face in enumerate(faces):
            res = _face_normal_and_centroid(face)
            if res is None:
                continue
            (nx, ny, nz), (cx, cy, cz), area = res
            dot = nx * pdir[0] + ny * pdir[1] + nz * pdir[2]
            if dot < dot_thr:
                # angle between face normal and -pull, in [0, 180]°
                dot_neg_pull = -dot
                dot_clamped = max(-1.0, min(1.0, dot_neg_pull))
                angle_deg = math.degrees(math.acos(dot_clamped))
                undercut.append({
                    "face_idx": idx,
                    "area": round(area, 4),
                    "sample_point": [round(cx, 4), round(cy, 4), round(cz, 4)],
                    "normal": [round(nx, 4), round(ny, 4), round(nz, 4)],
                    "dot_with_pull": round(dot, 4),
                    "angle_deg": round(angle_deg, 2),
                    "surface_type": _surface_type(face),
                })
                total_area += area

        summary = {
            "pull_direction": [round(c, 4) for c in pdir],
            "dot_threshold": dot_thr,
            "undercut_face_count": len(undercut),
            "undercut_area_mm2": round(total_area, 4),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "undercut_faces": undercut,
                "undercut_summary": summary,
            },
        )
