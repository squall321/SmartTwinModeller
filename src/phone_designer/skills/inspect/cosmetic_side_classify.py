"""cosmetic_side_classify — atomic, read-only DFM inspect.

Classify each face as **cosmetic (A-surface)** or **non-cosmetic (B-surface)**
based on visibility from a given ``viewer_position``.

Algorithm:
    For every face, take its centroid. Cast a ray from the viewer toward that
    centroid. If the ray first hits *this* face (no other face occludes it)
    AND the face normal at the centroid points back toward the viewer, the
    face is visible → cosmetic. Otherwise it is a B-surface (hidden, or
    facing away).

This is the standard simple-visibility classification used to decide where
gate marks, parting lines, and ejector pins are *forbidden* on a moulded part.
For full multi-camera coverage (left + right + front) the planner runs this
skill once per viewer and unions the cosmetic sets.

extras schema:
    extras["a_faces"]        = [face_idx, ...]    # cosmetic (visible) faces
    extras["b_faces"]        = [face_idx, ...]    # non-cosmetic faces
    extras["cosmetic_faces"] = [face_idx, ...]    # alias of a_faces (legacy)
    extras["cosmetic_report"] = {
        "viewer_position": [x,y,z],
        "face_count": int,
        "cosmetic_count": int,
        "b_count": int,
        "details": [
            {"face_idx": int, "is_cosmetic": bool,
             "centroid": [x,y,z], "facing_viewer": bool,
             "occluded": bool, "area_mm2": float},
            ...
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


def _load(family: str, name: str):
    import pathlib
    import yaml
    here = pathlib.Path(__file__).resolve()
    for root in here.parents:
        cand = root / "catalogs" / family / f"{name}.yaml"
        if cand.exists():
            return yaml.safe_load(cand.read_text())
    raise FileNotFoundError(f"catalog not found: {family}/{name}.yaml")


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _face_centroid_normal_area(face) -> tuple[
    tuple[float, float, float], tuple[float, float, float], float,
] | None:
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
    return ((c.X(), c.Y(), c.Z()), (nx, ny, nz), area)


def _first_hit_distance(shape, viewer, direction, max_t: float) -> float | None:
    """Cast a ray from viewer along direction; return the smallest positive
    intersection parameter (mm), or None if nothing hit."""
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

    dlen = math.sqrt(direction[0] ** 2 + direction[1] ** 2 + direction[2] ** 2)
    if dlen < 1e-12:
        return None
    d = gp_Dir(direction[0] / dlen, direction[1] / dlen, direction[2] / dlen)
    p = gp_Pnt(viewer[0], viewer[1], viewer[2])
    try:
        line = gp_Lin(p, d)
        inter = BRepIntCurveSurface_Inter()
        inter.Init(shape, line, 1e-6)
    except Exception:
        return None

    best_t = None
    while inter.More():
        t = inter.W()
        if 0 < t < max_t:
            if best_t is None or t < best_t:
                best_t = t
        inter.Next()
    return best_t


@skill(
    name="cosmetic_side_classify",
    category="inspect",
    level="atomic",
    summary="Classify each face as cosmetic (A-surface) or B-surface by "
            "ray-casting from the viewer toward each face centroid. A face is "
            "cosmetic when its centroid is the first ray hit AND its normal "
            "faces the viewer. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["cosmetic_classification"],
    preserves=["body_topology"],
    manufacturing={
        "injection_mold_pa": {"extras": {"cosmetic_aware": True}},
    },
    failure_modes=["fm.viewer_inside_body"],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="body_present")],
)
class CosmeticSideClassify(SkillBase):
    class Args(BaseModel):
        viewer_position: tuple[float, float, float] = Field(default=(100.0, 0.0, 50.0))

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        cat = _load("dfm_inspect", "default_thresholds")
        hit_eps = float(cat["cosmetic"]["hit_eps_mm"])

        shape = _occt_shape(body)
        faces = _all_faces(shape)
        viewer = (
            float(args.viewer_position[0]),
            float(args.viewer_position[1]),
            float(args.viewer_position[2]),
        )

        cosmetic: list[int] = []
        b_faces: list[int] = []
        details: list[dict[str, Any]] = []

        for idx, face in enumerate(faces):
            res = _face_centroid_normal_area(face)
            if res is None:
                b_faces.append(idx)
                details.append({
                    "face_idx": idx,
                    "is_cosmetic": False,
                    "centroid": [0.0, 0.0, 0.0],
                    "facing_viewer": False,
                    "occluded": True,
                    "area_mm2": 0.0,
                })
                continue
            (cx, cy, cz), (nx, ny, nz), area = res

            # vector from viewer to centroid
            vx = cx - viewer[0]
            vy = cy - viewer[1]
            vz = cz - viewer[2]
            vlen = math.sqrt(vx * vx + vy * vy + vz * vz)
            if vlen < 1e-9:
                b_faces.append(idx)
                details.append({
                    "face_idx": idx,
                    "is_cosmetic": False,
                    "centroid": [round(cx, 4), round(cy, 4), round(cz, 4)],
                    "facing_viewer": False,
                    "occluded": True,
                    "area_mm2": round(area, 4),
                })
                continue

            # facing viewer ⇔ normal · (centroid→viewer) > 0 ⇔ normal · (viewer→centroid) < 0
            facing = (nx * vx + ny * vy + nz * vz) < 0.0

            t_first = _first_hit_distance(
                shape, viewer, (vx, vy, vz), max_t=vlen * 2.0 + 10.0,
            )
            if t_first is None:
                occluded = True
            else:
                # ray hits before reaching centroid (- hit_eps) ⇒ occluded
                occluded = t_first < vlen - hit_eps

            is_cosm = facing and not occluded
            if is_cosm:
                cosmetic.append(idx)
            else:
                b_faces.append(idx)
            details.append({
                "face_idx": idx,
                "is_cosmetic": is_cosm,
                "centroid": [round(cx, 4), round(cy, 4), round(cz, 4)],
                "facing_viewer": bool(facing),
                "occluded": bool(occluded),
                "area_mm2": round(area, 4),
            })

        report = {
            "viewer_position": [round(c, 4) for c in viewer],
            "face_count": len(faces),
            "cosmetic_count": len(cosmetic),
            "b_count": len(b_faces),
            "details": details,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "a_faces": cosmetic,
                "cosmetic_faces": cosmetic,   # legacy alias
                "b_faces": b_faces,
                "cosmetic_report": report,
            },
        )
