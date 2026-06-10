"""classify_edge_blends — atomic, read-only.

Detect FILLET and CHAMFER edge-blend faces on the body so the RE feature
catalog can carry them (plan item A3, 2026-06-10).

Algorithm
---------
FILLET candidate
    A face whose surface is a *cylinder* (constant radius) or a *torus*
    (constant minor radius) that is tangent-continuous (G1 within
    ``tangent_tol_deg``, default 2°) to BOTH neighbour faces across its
    two LONG edges. Long edges are recognised by direction:

        cylinder — edge tangent parallel to the cylinder axis
                   (the fillet rolls across the arc; its tangency lines
                   run along the axis),
        torus    — edge tangent circumferential around the torus main
                   axis (the two full/partial circles where the blend
                   meets its neighbours).

    Neighbour faces come from ``TopExp.MapShapesAndAncestors`` edge→face.
    radius = cylinder radius / torus minor radius.

CHAMFER candidate
    A planar face forming an oblique strip: its two LONGEST edges are
    roughly parallel, each meets a distinct neighbour at a SHARP
    (non-G1) edge, and the dihedral normal angle across both long edges
    is oblique — in (8°, 82°), i.e. the strip's normal is neither
    parallel nor perpendicular to either neighbour's normal. A narrow
    aspect (width = area/length « length) is required.

Conservative-by-design (false fillets pollute the catalog corpus-wide):
    * min radius / width floor 0.05 mm,
    * seam edges (same face on both sides) and degenerate edges skipped,
    * cylinder faces whose angular extent ≥ ``max_arc_extent_deg``
      (default 200°) are rejected — a rod / hole wall is NOT a blend
      even when tangent to end blends at both ends; same gate on the
      torus tube (V) extent rejects donut outer shells,
    * ≥ 2 DISTINCT tangent neighbours required, so a split half-cylinder
      tangent only to its sibling half never qualifies.

extras["edge_blends"] = [
    {"id": int, "kind": "fillet", "radius_mm": float, "face_index": int,
     "edge_length_mm": float, "convexity": "round"|"fillet",   # may be absent
     "centroid": [x, y, z]},
    {"id": int, "kind": "chamfer", "width_mm": float, "angle_deg": float,
     "length_mm": float, "face_index": int, "centroid": [x, y, z]},
    ...
]

convexity: 'round' = convex (outward rounding of an outer edge),
'fillet' = concave (inner-corner relief). Computed from the outward
normal vs the radial direction at the face mid-UV — cheap; omitted when
degenerate.

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

# Same single-source face-count cap as classify_pockets /
# extract_feature_catalog (override with PHONE_DESIGNER_MAX_FACE_COUNT).
from phone_designer.skills._face_count_guard import (
    DEFAULT_MAX_FACE_COUNT as _DEFAULT_MAX_FACE_COUNT,
)


# ──────────────────────────────────────────────────────────────────────────────
# Geometry helpers


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _unit(v):
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if m < 1e-12:
        return None
    return (v[0] / m, v[1] / m, v[2] / m)


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _angle_deg(a, b) -> float:
    """Unsigned angle between two vectors in degrees — NO anti-parallel
    folding (outward normals across a G1 edge point the SAME way; folding
    would mis-read a knife-edge as tangent)."""
    da = math.sqrt(_dot(a, a))
    db = math.sqrt(_dot(b, b))
    if da < 1e-12 or db < 1e-12:
        return 0.0
    c = max(-1.0, min(1.0, _dot(a, b) / (da * db)))
    return math.degrees(math.acos(c))


def _outward_normal_at_xyz(face, xyz):
    """Outward unit normal of ``face`` at the surface point closest to
    ``xyz`` (same math as edge_concavity_classify / continuity_audit)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.gp import gp_Pnt
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface
    from OCP.TopAbs import TopAbs_REVERSED

    try:
        surf = BRep_Tool.Surface_s(face)
        sas = ShapeAnalysis_Surface(surf)
        uv = sas.ValueOfUV(gp_Pnt(xyz[0], xyz[1], xyz[2]), 1e-4)
        u, v = uv.X(), uv.Y()
    except Exception:
        return None
    try:
        adaptor = BRepAdaptor_Surface(face)
        slp = BRepLProp_SLProps(adaptor, u, v, 1, 1e-6)
        if not slp.IsNormalDefined():
            return None
        n = slp.Normal()
        nx, ny, nz = n.X(), n.Y(), n.Z()
        if face.Orientation() == TopAbs_REVERSED:
            nx, ny, nz = -nx, -ny, -nz
        return (nx, ny, nz)
    except Exception:
        return None


def _outward_normal_mid_uv(face):
    """(point, outward_normal) at the face's mid-UV — used for the cheap
    convexity probe. None on failure."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.TopAbs import TopAbs_REVERSED

    try:
        adaptor = BRepAdaptor_Surface(face)
        u = 0.5 * (adaptor.FirstUParameter() + adaptor.LastUParameter())
        v = 0.5 * (adaptor.FirstVParameter() + adaptor.LastVParameter())
        slp = BRepLProp_SLProps(adaptor, u, v, 1, 1e-6)
        if not slp.IsNormalDefined():
            return None
        p = slp.Value()
        n = slp.Normal()
        nx, ny, nz = n.X(), n.Y(), n.Z()
        if face.Orientation() == TopAbs_REVERSED:
            nx, ny, nz = -nx, -ny, -nz
        return ((p.X(), p.Y(), p.Z()), (nx, ny, nz))
    except Exception:
        return None


def _edge_samples(edge, fracs=(0.25, 0.5, 0.75)):
    """[(xyz, unit_tangent), ...] at parameter fractions along the edge."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.gp import gp_Pnt, gp_Vec

    out = []
    try:
        ac = BRepAdaptor_Curve(edge)
        u0 = ac.FirstParameter()
        u1 = ac.LastParameter()
    except Exception:
        return out
    for f in fracs:
        try:
            u = u0 + (u1 - u0) * f
            p = gp_Pnt()
            d1 = gp_Vec()
            ac.D1(u, p, d1)
            t = _unit((d1.X(), d1.Y(), d1.Z()))
            out.append(((p.X(), p.Y(), p.Z()), t))
        except Exception:
            continue
    return out


def _edge_length(edge) -> float:
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        props = GProp_GProps()
        BRepGProp.LinearProperties_s(edge, props)
        return float(props.Mass())
    except Exception:
        return 0.0


def _face_centroid(face):
    try:
        from phone_designer.skills._resolvers import _face_center
        c = _face_center(face)
        return (float(c[0]), float(c[1]), float(c[2]))
    except Exception:
        return (0.0, 0.0, 0.0)


def _max_normal_angle_deg(face_a, face_b, samples) -> float | None:
    """Max angle (deg) between the two faces' outward normals over the
    edge samples. None when no sample yields normals on both faces."""
    worst: float | None = None
    for xyz, _t in samples:
        na = _outward_normal_at_xyz(face_a, xyz)
        nb = _outward_normal_at_xyz(face_b, xyz)
        if na is None or nb is None:
            continue
        ang = _angle_deg(na, nb)
        if worst is None or ang > worst:
            worst = ang
    return worst


# ──────────────────────────────────────────────────────────────────────────────
# Surface probes


def _cylinder_params(face):
    """(axis_origin, axis_dir, radius, arc_extent_rad) — else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    try:
        adaptor = BRepAdaptor_Surface(face)
        if adaptor.GetType() != GeomAbs_Cylinder:
            return None
        cyl = adaptor.Cylinder()
        loc = cyl.Location()
        d = cyl.Axis().Direction()
        arc = abs(adaptor.LastUParameter() - adaptor.FirstUParameter())
        return (
            (loc.X(), loc.Y(), loc.Z()),
            (d.X(), d.Y(), d.Z()),
            float(cyl.Radius()),
            float(arc),
        )
    except Exception:
        return None


def _torus_params(face):
    """(center, axis_dir, major_r, minor_r, tube_extent_rad) — else None.
    On Geom_ToroidalSurface U runs around the main axis, V around the
    tube — the blend's partial-arc extent is the V range."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Torus

    try:
        adaptor = BRepAdaptor_Surface(face)
        if adaptor.GetType() != GeomAbs_Torus:
            return None
        tor = adaptor.Torus()
        loc = tor.Location()
        d = tor.Axis().Direction()
        tube = abs(adaptor.LastVParameter() - adaptor.FirstVParameter())
        return (
            (loc.X(), loc.Y(), loc.Z()),
            (d.X(), d.Y(), d.Z()),
            float(tor.MajorRadius()),
            float(tor.MinorRadius()),
            float(tube),
        )
    except Exception:
        return None


def _is_plane(face) -> bool:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    try:
        return BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane
    except Exception:
        return False


def _fillet_convexity(face, kind_params) -> str | None:
    """'round' (convex) / 'fillet' (concave) via the outward normal vs the
    radial direction at the face mid-UV. None when degenerate (omitted).

    cylinder: radial = point − its projection onto the axis.
    torus:    radial = point − tube-circle centre.
    """
    probe = _outward_normal_mid_uv(face)
    if probe is None:
        return None
    p, n = probe
    if len(kind_params) == 4:  # cylinder
        origin, axis, _r, _arc = kind_params
        a = _unit(axis)
        if a is None:
            return None
        w = (p[0] - origin[0], p[1] - origin[1], p[2] - origin[2])
        t = _dot(w, a)
        radial = (w[0] - t * a[0], w[1] - t * a[1], w[2] - t * a[2])
    else:  # torus
        center, axis, major_r, _minor_r, _tube = kind_params
        a = _unit(axis)
        if a is None:
            return None
        w = (p[0] - center[0], p[1] - center[1], p[2] - center[2])
        t = _dot(w, a)
        w_perp = (w[0] - t * a[0], w[1] - t * a[1], w[2] - t * a[2])
        ru = _unit(w_perp)
        if ru is None:
            return None
        tube_c = (
            center[0] + major_r * ru[0],
            center[1] + major_r * ru[1],
            center[2] + major_r * ru[2],
        )
        radial = (p[0] - tube_c[0], p[1] - tube_c[1], p[2] - tube_c[2])
    rm = math.sqrt(_dot(radial, radial))
    if rm < 1e-9:
        return None
    return "round" if _dot(n, radial) > 0.0 else "fillet"


# ──────────────────────────────────────────────────────────────────────────────
# Edge → neighbour-face plumbing


def _edge_face_map(shape):
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, m)
    return m


def _face_index_map(faces):
    from OCP.TopTools import TopTools_IndexedMapOfShape

    m = TopTools_IndexedMapOfShape()
    for f in faces:
        m.Add(f)
    return m


def _edges_with_neighbours(face, edge_faces, face_idx_map, self_index):
    """[(edge, neighbour_face, neighbour_index), ...] for the face's
    non-degenerate, non-seam edges. A seam edge (same face on both sides)
    yields no distinct neighbour and is skipped."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_MapOfShape

    out = []
    seen = TopTools_MapOfShape()
    it = TopExp_Explorer(face, TopAbs_EDGE)
    while it.More():
        cur = it.Current()
        if seen.Contains(cur):
            it.Next()
            continue
        seen.Add(cur)
        edge = TopoDS.Edge_s(cur)
        it.Next()
        try:
            if BRep_Tool.Degenerated_s(edge):
                continue
        except Exception:
            continue
        try:
            owners = edge_faces.FindFromKey(edge)
        except Exception:
            continue
        neighbour = None
        neighbour_idx = -1
        for sh in owners:
            try:
                fi1 = face_idx_map.FindIndex(sh)
            except Exception:
                continue
            if fi1 <= 0 or (fi1 - 1) == self_index:
                continue
            neighbour = TopoDS.Face_s(sh)
            neighbour_idx = fi1 - 1
            break
        if neighbour is None:
            continue  # boundary or seam edge
        out.append((edge, neighbour, neighbour_idx))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Per-face classifiers

_LONG_EDGE_DIR_TOL = math.cos(math.radians(20.0))  # direction gate
_SHARP_MIN_DEG = 8.0      # chamfer long edges must be at least this sharp
_OBLIQUE_MAX_DEG = 82.0   # ... and not perpendicular to a neighbour


def _try_fillet(face, fi, edge_records, args):
    """Return a fillet entry dict or None. ``edge_records`` is the
    _edges_with_neighbours output for this face."""
    params = _cylinder_params(face)
    torus = None
    if params is None:
        torus = _torus_params(face)
        if torus is None:
            return None

    max_arc_rad = math.radians(args.max_arc_extent_deg)
    if params is not None:
        origin, axis, radius, arc = params
        if arc > max_arc_rad:
            return None  # rod / hole wall, not a blend
        axis_u = _unit(axis)
        if axis_u is None:
            return None
    else:
        center, axis, major_r, minor_r, tube = torus
        if tube > max_arc_rad:
            return None  # donut shell, not a blend
        if minor_r >= major_r:
            return None  # not a sane edge blend geometry
        radius = minor_r
        axis_u = _unit(axis)
        if axis_u is None:
            return None

    if radius < args.min_radius_mm:
        return None

    tangent_neighbours: set[int] = set()
    tangent_edge_len = 0.0
    for edge, neighbour, ni in edge_records:
        samples = _edge_samples(edge)
        if not samples:
            continue
        # direction gate — only the blend's LONG edges count.
        mid_t = samples[len(samples) // 2][1]
        if mid_t is None:
            continue
        if params is not None:
            # cylinder: long edges run along the axis.
            if abs(_dot(mid_t, axis_u)) < _LONG_EDGE_DIR_TOL:
                continue
        else:
            # torus: long edges are circumferential around the main axis.
            xyz = samples[len(samples) // 2][0]
            w = (xyz[0] - center[0], xyz[1] - center[1], xyz[2] - center[2])
            t = _dot(w, axis_u)
            ru = _unit((w[0] - t * axis_u[0], w[1] - t * axis_u[1],
                        w[2] - t * axis_u[2]))
            if ru is None:
                continue
            circ = _cross(axis_u, ru)
            if abs(_dot(mid_t, circ)) < _LONG_EDGE_DIR_TOL:
                continue
        worst = _max_normal_angle_deg(face, neighbour, samples)
        if worst is None or worst > args.tangent_tol_deg:
            continue
        tangent_neighbours.add(ni)
        tangent_edge_len = max(tangent_edge_len, _edge_length(edge))

    if len(tangent_neighbours) < 2:
        return None  # must be tangent to BOTH sides

    entry: dict[str, Any] = {
        "kind": "fillet",
        "radius_mm": round(radius, 4),
        "face_index": fi,
        "edge_length_mm": round(tangent_edge_len, 4),
        "centroid": [round(c, 4) for c in _face_centroid(face)],
    }
    cv = _fillet_convexity(face, params if params is not None else torus)
    if cv is not None:
        entry["convexity"] = cv
    return entry


def _try_chamfer(face, fi, edge_records, args):
    """Return a chamfer entry dict or None."""
    if not _is_plane(face):
        return None
    if len(edge_records) < 2:
        return None

    measured = []
    for edge, neighbour, ni in edge_records:
        length = _edge_length(edge)
        if length <= 1e-9:
            continue
        measured.append((length, edge, neighbour, ni))
    if len(measured) < 2:
        return None
    measured.sort(key=lambda t: -t[0])
    (len1, e1, n1, ni1), (len2, e2, n2, ni2) = measured[0], measured[1]
    if ni1 == ni2:
        return None  # both long edges on the same neighbour — not a strip

    s1 = _edge_samples(e1)
    s2 = _edge_samples(e2)
    if not s1 or not s2:
        return None
    t1 = s1[len(s1) // 2][1]
    t2 = s2[len(s2) // 2][1]
    if t1 is None or t2 is None:
        return None
    # The strip's two long edges run roughly parallel.
    if abs(_dot(t1, t2)) < math.cos(math.radians(25.0)):
        return None

    # Both long edges must be SHARP and the dihedral must be oblique —
    # the strip normal neither parallel (< _SHARP_MIN_DEG) nor
    # perpendicular (> _OBLIQUE_MAX_DEG) to either neighbour's normal.
    angles = []
    for samples, neighbour in ((s1, n1), (s2, n2)):
        worst = _max_normal_angle_deg(face, neighbour, samples)
        if worst is None:
            return None
        if worst < _SHARP_MIN_DEG or worst > _OBLIQUE_MAX_DEG:
            return None
        angles.append(worst)

    # Narrow aspect: width = area / longest edge « length.
    try:
        from phone_designer.skills._resolvers import _face_area
        area = float(_face_area(face))
    except Exception:
        return None
    if area <= 1e-9 or len1 <= 1e-9:
        return None
    width = area / len1
    if width < args.min_width_mm:
        return None
    if width > args.max_width_to_length_ratio * len1:
        return None  # square-ish oblique face (pyramid wall, gusset) — skip

    return {
        "kind": "chamfer",
        "width_mm": round(width, 4),
        "angle_deg": round(min(angles), 2),
        "length_mm": round(len1, 4),
        "face_index": fi,
        "centroid": [round(c, 4) for c in _face_centroid(face)],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="classify_edge_blends",
    category="inspect",
    level="atomic",
    summary="Detect fillet (tangent cylinder/torus blend, ≥2 G1 neighbours "
            "across its long edges) and chamfer (oblique narrow planar strip "
            "with sharp long edges) faces. Conservative gates — radius floor, "
            "arc-extent cap, seam/degenerate edges skipped — so rods, hole "
            "walls and split cylinder halves never false-positive. Result on "
            "extras['edge_blends']; read-only, body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["edge_blend_inventory"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="body_present")],
)
class ClassifyEdgeBlends(SkillBase):
    class Args(BaseModel):
        tangent_tol_deg: float = Field(
            default=2.0, gt=0.0, le=10.0,
            description="G1 gate — max normal angle (deg) across a long edge "
                        "for the neighbour to count as tangent-continuous.",
        )
        min_radius_mm: float = Field(
            default=0.05, ge=0.0,
            description="Fillet radius floor — sub-noise blends are skipped.",
        )
        min_width_mm: float = Field(
            default=0.05, ge=0.0,
            description="Chamfer strip width floor.",
        )
        max_arc_extent_deg: float = Field(
            default=200.0, gt=0.0, le=360.0,
            description="Reject cylinder faces whose angular extent (or torus "
                        "tube extent) exceeds this — a rod / hole wall / donut "
                        "shell is never an edge blend even when tangent to "
                        "blends at its ends.",
        )
        max_width_to_length_ratio: float = Field(
            default=0.5, gt=0.0, le=1.0,
            description="Chamfer narrow-aspect gate: width must be below this "
                        "fraction of the strip length.",
        )
        max_face_count: int | None = Field(
            default=_DEFAULT_MAX_FACE_COUNT,
            description="If the body has more than this many faces, skip and "
                        "return extras['edge_blends']={'skipped': True, ...}. "
                        "Set None to disable the guard. Default "
                        f"{_DEFAULT_MAX_FACE_COUNT} (shared with "
                        "classify_pockets via _face_count_guard).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        shape = _occt_shape(body)
        faces = _all_faces(shape)

        # ── face-count guard (DEFAULT_MAX_FACE_COUNT pattern) ──────────────
        if args.max_face_count is not None and len(faces) > args.max_face_count:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"edge_blends": {
                    "skipped": True,
                    "face_count": len(faces),
                    "reason": "too_big",
                    "limit": args.max_face_count,
                    "advice": "decimate the input mesh (mesh_decimate skill) "
                              "or simplify_to_canonical the BREP before "
                              "calling classify_edge_blends.",
                }},
            )

        if not faces:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"edge_blends": []},
            )

        edge_faces = _edge_face_map(shape)
        face_idx_map = _face_index_map(faces)

        blends: list[dict[str, Any]] = []
        for fi, face in enumerate(faces):
            try:
                edge_records = _edges_with_neighbours(
                    face, edge_faces, face_idx_map, fi,
                )
                if not edge_records:
                    continue
                entry = _try_fillet(face, fi, edge_records, args)
                if entry is None:
                    entry = _try_chamfer(face, fi, edge_records, args)
                if entry is not None:
                    entry["id"] = len(blends)
                    blends.append(entry)
            except Exception:
                # per-face isolation — one pathological face must not
                # poison the inventory.
                continue

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"edge_blends": blends},
        )
