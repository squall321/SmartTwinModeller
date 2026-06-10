"""color_by_property — atomic, read-only.

Tag each face with a per-face scalar value drawn from one of four physical /
geometric properties, then map that value through a named colormap to an
``(r, g, b)`` triple in [0, 1]³. Drives heat-map-style viz, "where is the
thinnest wall" highlights, and DFM tooltip overlays.

Supported ``property`` values:
    - ``wall_thickness``: median ray-cast wall thickness sampled from each
      face's center along its inward normal. Faces with no opposing hit get
      value ``inf`` (drawn as the colormap's "high" end).
    - ``draft_angle``: angle in degrees between each face's normal at center
      and the +Z axis (the molding-pull direction). 0° = perpendicular pull,
      90° = parallel to pull (vertical wall).
    - ``curvature``: |mean curvature| sampled at the face's UV center via
      ``BRepLProp_SLProps``.
    - ``stress``: 0.0 placeholder — real FEA stress field would be wired in by
      a simulation adapter; we return a uniform 0 field so downstream viz
      pipelines keep working in test / unwired environments.

extras["face_colors"] schema:
    [
      {"face_idx": i,
       "value":     <float | inf>,
       "color_rgb": (r, g, b)},
      ...
    ]

Body unchanged — post ``body_present``.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.inspect._draft_math import unsigned_pull_angle_deg


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


# ---------------- colormap LUTs ---------------------------------------------

# Five-stop sample of matplotlib's "viridis" — interpolated below. Storing the
# stops inline keeps the skill standalone (no matplotlib runtime dep).
_VIRIDIS = [
    (0.267, 0.005, 0.329),
    (0.275, 0.247, 0.512),
    (0.176, 0.443, 0.557),
    (0.137, 0.624, 0.529),
    (0.991, 0.906, 0.144),
]
_PLASMA = [
    (0.050, 0.030, 0.528),
    (0.490, 0.012, 0.658),
    (0.798, 0.281, 0.470),
    (0.974, 0.582, 0.205),
    (0.940, 0.975, 0.131),
]
_GRAY = [
    (0.0, 0.0, 0.0),
    (1.0, 1.0, 1.0),
]
_COOLWARM = [
    (0.230, 0.299, 0.754),
    (0.500, 0.500, 0.500),
    (0.706, 0.016, 0.150),
]


def _lookup_colormap(name: str) -> list[tuple[float, float, float]]:
    table = {
        "viridis":  _VIRIDIS,
        "plasma":   _PLASMA,
        "gray":     _GRAY,
        "grey":     _GRAY,
        "coolwarm": _COOLWARM,
    }
    return table.get(name.lower(), _VIRIDIS)


def _interp_color(t: float,
                  stops: list[tuple[float, float, float]],
                  ) -> tuple[float, float, float]:
    t = max(0.0, min(1.0, float(t)))
    if len(stops) == 1:
        return stops[0]
    seg = t * (len(stops) - 1)
    lo = int(math.floor(seg))
    hi = min(lo + 1, len(stops) - 1)
    f = seg - lo
    a = stops[lo]
    b = stops[hi]
    return (
        a[0] + (b[0] - a[0]) * f,
        a[1] + (b[1] - a[1]) * f,
        a[2] + (b[2] - a[2]) * f,
    )


# ---------------- per-face value extractors ---------------------------------

def _face_center_xyz(face) -> tuple[float, float, float]:
    from phone_designer.skills._resolvers import _face_center
    try:
        return _face_center(face)
    except Exception:
        return (0.0, 0.0, 0.0)


def _face_normal(face) -> tuple[float, float, float]:
    """Best-effort unit outward normal at face's UV center."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_REVERSED

    try:
        adaptor = BRepAdaptor_Surface(face)
        u = 0.5 * (adaptor.FirstUParameter() + adaptor.LastUParameter())
        v = 0.5 * (adaptor.FirstVParameter() + adaptor.LastVParameter())
        if adaptor.GetType() == GeomAbs_Plane:
            n = adaptor.Plane().Axis().Direction()
            nx, ny, nz = n.X(), n.Y(), n.Z()
        else:
            d1u = adaptor.DN(u, v, 1, 0)
            d1v = adaptor.DN(u, v, 0, 1)
            nx = d1u.Y() * d1v.Z() - d1u.Z() * d1v.Y()
            ny = d1u.Z() * d1v.X() - d1u.X() * d1v.Z()
            nz = d1u.X() * d1v.Y() - d1u.Y() * d1v.X()
        L = math.sqrt(nx * nx + ny * ny + nz * nz)
        if L < 1e-12:
            return (0.0, 0.0, 1.0)
        nx, ny, nz = nx / L, ny / L, nz / L
        if face.Orientation() == TopAbs_REVERSED:
            nx, ny, nz = -nx, -ny, -nz
        return (nx, ny, nz)
    except Exception:
        return (0.0, 0.0, 1.0)


def _wall_thickness_at(face, shape) -> float:
    """Cast a ray from the face's center along the inward normal, return the
    distance to the first surface hit. inf if no opposing surface intersects."""
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

    cx, cy, cz = _face_center_xyz(face)
    n = _face_normal(face)
    if n == (0.0, 0.0, 0.0):
        return float("inf")
    # Inward = negative outward normal.
    inv = (-n[0], -n[1], -n[2])
    # Nudge ray origin slightly inward so we don't pick up the source face.
    eps = 1e-4
    origin = gp_Pnt(cx + inv[0] * eps, cy + inv[1] * eps, cz + inv[2] * eps)
    direction = gp_Dir(inv[0], inv[1], inv[2])
    line = gp_Lin(origin, direction)

    try:
        intersector = IntCurvesFace_ShapeIntersector()
        intersector.Load(shape, 1e-6)
        intersector.PerformNearest(line, 0.0, 1e30)
        if intersector.IsDone() and intersector.NbPnt() > 0:
            d = float(intersector.WParameter(1))
            if d > 0 and math.isfinite(d):
                return d + eps
    except Exception:
        pass
    return float("inf")


def _draft_angle_deg(face) -> float:
    # Angle between normal and +Z, hemispheres collapsed — math now lives in
    # the shared _draft_math helper (also used by inspect_draft_angles).
    # _face_normal always returns a unit vector, so the degenerate (None)
    # branch is unreachable; 90.0 fallback preserves the old acos(0) value.
    n = _face_normal(face)
    ang = unsigned_pull_angle_deg(n, (0.0, 0.0, 1.0))
    return 90.0 if ang is None else ang


def _abs_mean_curvature(face) -> float:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps

    try:
        adaptor = BRepAdaptor_Surface(face)
        u = 0.5 * (adaptor.FirstUParameter() + adaptor.LastUParameter())
        v = 0.5 * (adaptor.FirstVParameter() + adaptor.LastVParameter())
        slp = BRepLProp_SLProps(adaptor, u, v, 2, 1e-6)
        if slp.IsCurvatureDefined():
            return abs(float(slp.MeanCurvature()))
    except Exception:
        pass
    return 0.0


# ---------------- driver -----------------------------------------------------

def _normalise(values: list[float]) -> list[float]:
    """Map raw values → [0, 1] for color lookup. ``inf`` is treated as 1.0."""
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return [0.0 for _ in values]
    vmin = min(finite)
    vmax = max(finite)
    if vmax - vmin < 1e-12:
        return [0.5 for _ in values]
    out: list[float] = []
    for v in values:
        if not math.isfinite(v):
            out.append(1.0)
        else:
            out.append((v - vmin) / (vmax - vmin))
    return out


@skill(
    name="color_by_property",
    category="inspect",
    level="atomic",
    summary="Tag every face with a scalar value drawn from a physical / "
            "geometric property (wall_thickness, draft_angle, curvature, "
            "stress) and map it through a colormap to per-face RGB. Drives "
            "heat-map viz / DFM overlays. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["face_colormap"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class ColorByProperty(SkillBase):
    class Args(BaseModel):
        property: Literal["wall_thickness", "draft_angle",
                          "curvature", "stress"] = "wall_thickness"
        colormap: str = Field(default="viridis")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        if body is None:
            raise ValueError("color_by_property: body is None")

        shape = _occt_shape(body)
        if shape is None:
            raise ValueError("color_by_property: body.wrapped is None")

        faces = _all_faces(shape)

        # Per-face raw values.
        values: list[float] = []
        prop = args.property
        for f in faces:
            try:
                if prop == "wall_thickness":
                    v = _wall_thickness_at(f, shape)
                elif prop == "draft_angle":
                    v = _draft_angle_deg(f)
                elif prop == "curvature":
                    v = _abs_mean_curvature(f)
                elif prop == "stress":
                    # Placeholder — no FEA wired up here.
                    v = 0.0
                else:
                    v = 0.0
            except Exception:
                v = 0.0
            values.append(float(v))

        stops = _lookup_colormap(args.colormap)
        t_vals = _normalise(values)

        face_colors: list[dict[str, Any]] = []
        for i, (raw, t) in enumerate(zip(values, t_vals)):
            r, g, b = _interp_color(t, stops)
            out_value: Any
            if math.isfinite(raw):
                out_value = round(raw, 6)
            else:
                out_value = float("inf")
            face_colors.append({
                "face_idx":  i,
                "value":     out_value,
                "color_rgb": (round(r, 4), round(g, 4), round(b, 4)),
            })

        extras = {
            "face_colors": face_colors,
            "property":    prop,
            "colormap":    args.colormap,
            "face_count":  len(faces),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
