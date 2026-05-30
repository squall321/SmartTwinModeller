"""normal_map_compute — atomic, read-only.

For each face of the body, sample N points on a near-square (u, v) grid and
compute the unit surface normal at each sample using OCCT
``BRepAdaptor_Surface`` derivatives (with plane-fast-path). The result is a
"normal map" suitable for driving shaded viz, draft-angle DFM checks, and
overhang heuristics.

extras["normal_map"] schema:
    [
      {"face_idx": i,
       "samples": [
         {"uv":     [u, v],
          "point":  [x, y, z],
          "normal": [nx, ny, nz]},
         ...
       ]},
      ...
    ]

Body unchanged — post ``body_present``.
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


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if n < 1e-12:
        return (0.0, 0.0, 1.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def _normal_at_uv(adaptor, u: float, v: float,
                  reversed_face: bool) -> tuple[float, float, float]:
    from OCP.GeomAbs import GeomAbs_Plane

    try:
        if adaptor.GetType() == GeomAbs_Plane:
            n = adaptor.Plane().Axis().Direction()
            nx, ny, nz = n.X(), n.Y(), n.Z()
        else:
            d1u = adaptor.DN(u, v, 1, 0)
            d1v = adaptor.DN(u, v, 0, 1)
            nx = d1u.Y() * d1v.Z() - d1u.Z() * d1v.Y()
            ny = d1u.Z() * d1v.X() - d1u.X() * d1v.Z()
            nz = d1u.X() * d1v.Y() - d1u.Y() * d1v.X()
        if reversed_face:
            nx, ny, nz = -nx, -ny, -nz
        return _unit((nx, ny, nz))
    except Exception:
        return (0.0, 0.0, 1.0)


def _grid_side(samples_per_face: int) -> int:
    n = max(1, int(samples_per_face))
    side = int(math.ceil(math.sqrt(n)))
    return max(1, side)


def _sample_face(face, samples_per_face: int) -> list[dict[str, Any]]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_REVERSED

    samples: list[dict[str, Any]] = []
    try:
        adaptor = BRepAdaptor_Surface(face)
    except Exception:
        return samples

    u0 = adaptor.FirstUParameter()
    u1 = adaptor.LastUParameter()
    v0 = adaptor.FirstVParameter()
    v1 = adaptor.LastVParameter()

    side = _grid_side(samples_per_face)
    reversed_face = (face.Orientation() == TopAbs_REVERSED)

    for i in range(side):
        for j in range(side):
            fu = (i + 0.5) / side
            fv = (j + 0.5) / side
            u = u0 + (u1 - u0) * fu
            v = v0 + (v1 - v0) * fv
            try:
                p = adaptor.Value(u, v)
                xyz = (p.X(), p.Y(), p.Z())
            except Exception:
                xyz = (0.0, 0.0, 0.0)
            n = _normal_at_uv(adaptor, u, v, reversed_face)
            samples.append({
                "uv": [round(u, 6), round(v, 6)],
                "point":  [round(xyz[0], 6), round(xyz[1], 6), round(xyz[2], 6)],
                "normal": [round(n[0], 6),   round(n[1], 6),   round(n[2], 6)],
            })
    return samples


@skill(
    name="normal_map_compute",
    category="inspect",
    level="atomic",
    summary="For every face on the body, sample N points on a UV grid and "
            "compute the unit outward normal at each. Result is a normal map "
            "suitable for shaded viz / draft-angle checks. Read-only — body "
            "unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["normal_map"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class NormalMapCompute(SkillBase):
    class Args(BaseModel):
        samples_per_face: int = Field(default=10, ge=1, le=10000)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        if body is None:
            raise ValueError("normal_map_compute: body is None")

        shape = _occt_shape(body)
        if shape is None:
            raise ValueError("normal_map_compute: body.wrapped is None")

        faces = _all_faces(shape)
        normal_map: list[dict[str, Any]] = []
        for i, f in enumerate(faces):
            try:
                samples = _sample_face(f, args.samples_per_face)
            except Exception:
                samples = []
            normal_map.append({
                "face_idx": i,
                "samples":  samples,
            })

        extras = {
            "normal_map": normal_map,
            "face_count": len(faces),
            "samples_per_face": int(args.samples_per_face),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
