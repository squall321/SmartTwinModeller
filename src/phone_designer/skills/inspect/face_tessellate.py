"""face_tessellate — atomic, read-only.

For each face matched by the given selector, run OCCT
``BRepMesh_IncrementalMesh`` with the requested linear deflection and extract
the resulting triangle mesh (vertices + triangles + per-vertex normals).

LLM agents / planners use this to (a) drive 3D viz of a single feature, (b)
feed a downstream surface-analysis skill that wants raw triangles, or (c)
visualise the result of a UV-grid sampler in true geometry space.

extras["mesh"] schema:
    {
      "deflection_mm": float,
      "face_count": int,
      "faces": [
        {"face_idx": i,
         "vertices":  [(x, y, z), ...],
         "triangles": [(i, j, k), ...],
         "normals":   [(nx, ny, nz), ...]},
        ...
      ],
      "vertex_count_total":   int,
      "triangle_count_total": int,
    }

Body is returned unchanged — post ``body_present``.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
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


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if n < 1e-12:
        return (0.0, 0.0, 1.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def _normal_at_uv(adaptor, u: float, v: float,
                  reversed_face: bool) -> tuple[float, float, float]:
    """Unit surface normal at (u, v). Honors face orientation."""
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


def _extract_face_mesh(face, deflection_mm: float) -> dict[str, Any]:
    """Run BRepMesh on the parent shape if needed and pull triangles for face."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopLoc import TopLoc_Location

    loc = TopLoc_Location()
    tri = BRep_Tool.Triangulation_s(face, loc)
    if tri is None or tri.NbTriangles() == 0:
        return {
            "vertices": [],
            "triangles": [],
            "normals": [],
        }

    trsf = loc.Transformation()

    nb_nodes = int(tri.NbNodes())
    nb_tris = int(tri.NbTriangles())

    vertices: list[tuple[float, float, float]] = []
    for i in range(1, nb_nodes + 1):
        p = tri.Node(i)
        p2 = p.Transformed(trsf)
        vertices.append((
            round(p2.X(), 6),
            round(p2.Y(), 6),
            round(p2.Z(), 6),
        ))

    reversed_face = (face.Orientation() == TopAbs_REVERSED)

    triangles: list[tuple[int, int, int]] = []
    for i in range(1, nb_tris + 1):
        t = tri.Triangle(i)
        a, b, c = t.Get()
        # OCCT node indices are 1-based; convert to 0-based.
        ai, bi, ci = int(a) - 1, int(b) - 1, int(c) - 1
        # Match face orientation so the right-hand rule points outward.
        if reversed_face:
            triangles.append((ai, ci, bi))
        else:
            triangles.append((ai, bi, ci))

    # Per-vertex normals via surface UV → fall back to face-average if no UV.
    normals: list[tuple[float, float, float]] = []
    try:
        adaptor = BRepAdaptor_Surface(face)
        has_uv = tri.HasUVNodes()
    except Exception:
        adaptor = None
        has_uv = False

    if adaptor is not None and has_uv:
        for i in range(1, nb_nodes + 1):
            try:
                uv = tri.UVNode(i)
                n = _normal_at_uv(adaptor, uv.X(), uv.Y(), reversed_face)
            except Exception:
                n = (0.0, 0.0, 1.0)
            normals.append((round(n[0], 6), round(n[1], 6), round(n[2], 6)))
    else:
        # Compute one per-triangle normal and splat to incident vertices.
        accum = [[0.0, 0.0, 0.0] for _ in range(nb_nodes)]
        for (ai, bi, ci) in triangles:
            p1 = vertices[ai]
            p2 = vertices[bi]
            p3 = vertices[ci]
            ux, uy, uz = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
            vx, vy, vz = p3[0] - p1[0], p3[1] - p1[1], p3[2] - p1[2]
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            for idx in (ai, bi, ci):
                accum[idx][0] += nx
                accum[idx][1] += ny
                accum[idx][2] += nz
        for n in accum:
            u = _unit((n[0], n[1], n[2]))
            normals.append((round(u[0], 6), round(u[1], 6), round(u[2], 6)))

    return {
        "vertices": vertices,
        "triangles": triangles,
        "normals": normals,
    }


@skill(
    name="face_tessellate",
    category="inspect",
    level="atomic",
    summary="Tessellate the body with BRepMesh_IncrementalMesh at the given "
            "linear deflection and extract triangles + per-vertex normals for "
            "each face matched by the selector. Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["face_mesh"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.tessellation_failed"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class FaceTessellate(SkillBase):
    class Args(BaseModel):
        face_selector: dict
        deflection_mm: float = Field(default=0.05, gt=0, le=10.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepMesh import BRepMesh_IncrementalMesh

        from phone_designer.skills._resolvers import resolve_faces

        if body is None:
            raise ValueError("face_tessellate: body is None")

        shape = _occt_shape(body)
        if shape is None:
            raise ValueError("face_tessellate: body.wrapped is None")

        # Always re-mesh at requested deflection — caller controls fidelity.
        mesher = BRepMesh_IncrementalMesh(
            shape, float(args.deflection_mm), False, 0.5, True,
        )
        mesher.Perform()
        if not mesher.IsDone():
            raise RuntimeError(
                f"face_tessellate: BRepMesh failed "
                f"(deflection={args.deflection_mm})",
            )

        sel = _coerce_selector(args.face_selector)
        faces = resolve_faces(shape, sel, body=body)

        face_entries: list[dict[str, Any]] = []
        v_total = 0
        t_total = 0
        for i, f in enumerate(faces):
            try:
                fm = _extract_face_mesh(f, args.deflection_mm)
            except Exception:
                fm = {"vertices": [], "triangles": [], "normals": []}
            face_entries.append({
                "face_idx": i,
                "vertices": fm["vertices"],
                "triangles": fm["triangles"],
                "normals": fm["normals"],
            })
            v_total += len(fm["vertices"])
            t_total += len(fm["triangles"])

        extras = {
            "mesh": {
                "deflection_mm": float(args.deflection_mm),
                "face_count": len(faces),
                "faces": face_entries,
                "vertex_count_total": v_total,
                "triangle_count_total": t_total,
            },
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
