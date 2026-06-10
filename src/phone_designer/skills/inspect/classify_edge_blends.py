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

A3-FIX (2026-06-11) — round-trip (preserve_brep) stability gates
----------------------------------------------------------------
Diagnosed on the corpus round trip (orig catalog vs regen catalog):

1. SAME-SURFACE AGGREGATION (``merge_coaxial_fragments``, default True).
   Boolean cuts split one physical blend surface into N fragments and
   the fragment count is topology-dependent: Ventilator's R0.8 rim
   torus is 2 faces on the orig body but 14 on the regen body; its R40
   outer band is 2 vs 13. Per-face entries therefore can never
   round-trip. Fragments lying on the SAME underlying cylinder
   (axis + radius) or torus (centre + axis + radii) are merged into ONE
   entry: centroid = area-weighted over tangent-qualified fragments,
   ``edge_length_mm`` = sum of fragment seam lengths, ``face_index`` =
   largest-area fragment, ``fragment_count`` carries N.

2. UNION ANGULAR-EXTENT GATE. ``max_arc_extent_deg`` now applies to the
   union of a cylinder group's angular coverage (1° occupancy bins
   around the shared axis), not per fragment. A full bore / rim wall
   split into sub-200° fragments (Ventilator R40 band: 2×180° orig,
   13 fragments regen — both unions ≈ 360°) is rejected on BOTH sides;
   previously each fragment passed alone (G1-tangent to its siblings
   across the split seams, so "≥2 distinct neighbours" held).

3. STUB GATE (``min_length_to_radius_ratio``, default 1.0). A blend
   group whose total tangency-seam length is shorter than its own
   radius is a corner-castellation / spotface wall, not a designed edge
   blend: Crystal_SMD's eight 0.10–0.14 mm tall, R0.21–0.22 corner
   quarter-cylinders are simultaneously claimed by classify_holes as
   spotface hole walls, and the planner re-drills them as full bores so
   the regen body genuinely loses four of them (round-trip 8 → 4 was
   unfixable by any regen-side gate). Real corpus blends measure ≥ 5×
   their radius; the stub gate drops the castellation walls on both
   sides. Set 0.0 to disable.

   KNOWN LIMIT: the underlying double-count (a face claimed both as a
   hole wall and as a blend) is a cross-detector issue; the principled
   fix — feature-ownership de-dup in extract_feature_catalog and
   castellation awareness in classify_holes/planner — is out of scope
   here. The stub gate is the honest geometric proxy supported by the
   corpus data.

extras["edge_blends"] = [
    {"id": int, "kind": "fillet", "radius_mm": float, "face_index": int,
     "edge_length_mm": float, "convexity": "round"|"fillet",   # may be absent
     "fragment_count": int,    # A3-FIX (2026-06-11) — merged faces, ≥ 1
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


def _fillet_fragment(face, fi, edge_records, args):
    """A3-FIX (2026-06-11): per-face FRAGMENT record (or None) — the
    fillet decision moved to group level (_fillet_group_entry) so that
    fragments of one blend surface split by boolean cuts aggregate into
    a single entry. ``edge_records`` is the _edges_with_neighbours
    output for this face.

    Per-face gates kept here: radius floor; torus tube extent + sanity.
    The cylinder angular-extent gate moved to the group UNION (a split
    bore must be rejected by its total coverage, not per fragment)."""
    params = _cylinder_params(face)
    torus = None
    if params is None:
        torus = _torus_params(face)
        if torus is None:
            return None

    max_arc_rad = math.radians(args.max_arc_extent_deg)
    if params is not None:
        origin, axis, radius, arc = params
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

    try:
        from phone_designer.skills._resolvers import _face_area
        area = float(_face_area(face))
    except Exception:
        area = 0.0

    return {
        "fi": fi,
        "face": face,
        "cyl": params,            # (origin, axis, radius, arc) or None
        "tor": torus,             # (center, axis, majR, minR, tube) or None
        "radius": radius,
        "area": area,
        "tangent_nbrs": tangent_neighbours,
        "max_tan_edge": tangent_edge_len,
    }


# A3-FIX (2026-06-11): same-surface identity tolerances. Fragments of one
# boolean-split face keep the IDENTICAL underlying Geom surface, so these
# only need to absorb float round-off — tight by design (a failed merge
# degrades to the pre-fix per-face behaviour).
_AXIS_PARALLEL_TOL = math.cos(math.radians(0.1))


def _pos_tol(radius: float) -> float:
    return 1e-3 + 1e-4 * abs(radius)


def _same_surface(a: dict, b: dict) -> bool:
    """True when two fragment records lie on the same cylinder (axis line
    + radius) or the same torus (centre + axis + both radii)."""
    if (a["cyl"] is None) != (b["cyl"] is None):
        return False
    if a["cyl"] is not None:
        o1, d1, r1, _ = a["cyl"]
        o2, d2, r2, _ = b["cyl"]
        u1 = _unit(d1)
        u2 = _unit(d2)
        if u1 is None or u2 is None:
            return False
        if abs(_dot(u1, u2)) < _AXIS_PARALLEL_TOL:
            return False
        tol = _pos_tol(r1)
        if abs(r1 - r2) > tol:
            return False
        # perpendicular distance of o2 from the (o1, u1) axis line
        w = (o2[0] - o1[0], o2[1] - o1[1], o2[2] - o1[2])
        t = _dot(w, u1)
        perp = (w[0] - t * u1[0], w[1] - t * u1[1], w[2] - t * u1[2])
        return math.sqrt(_dot(perp, perp)) <= tol
    c1, d1, mj1, mn1, _ = a["tor"]
    c2, d2, mj2, mn2, _ = b["tor"]
    u1 = _unit(d1)
    u2 = _unit(d2)
    if u1 is None or u2 is None:
        return False
    if abs(_dot(u1, u2)) < _AXIS_PARALLEL_TOL:
        return False
    tol = _pos_tol(mj1)
    if abs(mj1 - mj2) > tol or abs(mn1 - mn2) > _pos_tol(mn1):
        return False
    w = (c2[0] - c1[0], c2[1] - c1[1], c2[2] - c1[2])
    return math.sqrt(_dot(w, w)) <= tol


def _group_fragments(frags: list[dict], merge: bool) -> list[list[dict]]:
    """Cluster fragment records by surface identity (face order kept —
    deterministic). merge=False ⇒ one group per fragment (pre-fix
    per-face behaviour)."""
    if not merge:
        return [[f] for f in frags]
    groups: list[list[dict]] = []
    for rec in frags:
        for g in groups:
            if _same_surface(g[0], rec):
                g.append(rec)
                break
        else:
            groups.append([rec])
    return groups


def _cyl_group_coverage_deg(group: list[dict]) -> float:
    """A3-FIX (2026-06-11): UNION angular coverage of a cylinder group
    around its shared axis, via 1° occupancy bins over sampled surface
    points (frame-free: each point's angle is measured directly, so
    per-face parametrisation differences don't matter). Sampling is
    ≤ 0.5° apart — denser than the bins, no false gaps."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    origin, axis, _r, _arc = group[0]["cyl"]
    a = _unit(axis)
    if a is None:
        return 360.0  # conservative: unknown ⇒ treat as full wall
    # any unit vector perpendicular to the axis
    pick = (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0)
    x = _unit(_cross(a, pick))
    if x is None:
        return 360.0
    y = _cross(a, x)
    bins = [False] * 360
    for rec in group:
        try:
            ad = BRepAdaptor_Surface(rec["face"])
            u0 = ad.FirstUParameter()
            u1 = ad.LastUParameter()
            v = 0.5 * (ad.FirstVParameter() + ad.LastVParameter())
        except Exception:
            return 360.0
        arc_deg = abs(math.degrees(u1 - u0))
        n = max(16, int(arc_deg * 2.0) + 1)
        for k in range(n + 1):
            u = u0 + (u1 - u0) * k / n
            try:
                p = ad.Value(u, v)
            except Exception:
                continue
            w = (p.X() - origin[0], p.Y() - origin[1], p.Z() - origin[2])
            theta = math.degrees(math.atan2(_dot(w, y), _dot(w, x)))
            bins[int(theta) % 360] = True
    return float(sum(1 for b in bins if b))


def _fillet_group_entry(group: list[dict], args) -> dict[str, Any] | None:
    """A3-FIX (2026-06-11): group-level fillet decision. Returns the
    merged entry dict or None."""
    member_idx = {rec["fi"] for rec in group}

    # union angular-extent gate (cylinders) — split bores / rim walls.
    if group[0]["cyl"] is not None:
        if _cyl_group_coverage_deg(group) > args.max_arc_extent_deg:
            return None

    # tangency: the GROUP must be G1 to ≥ 2 distinct NON-member faces
    # (sibling fragments no longer count as evidence).
    outside: set[int] = set()
    for rec in group:
        outside |= rec["tangent_nbrs"]
    outside -= member_idx
    if len(outside) < 2:
        return None

    qualified = [rec for rec in group if rec["max_tan_edge"] > 0.0]
    if not qualified:
        return None

    radius = group[0]["radius"]
    total_len = sum(rec["max_tan_edge"] for rec in qualified)
    # stub gate — castellation / spotface corner walls (Crystal_SMD).
    if total_len < args.min_length_to_radius_ratio * radius:
        return None

    rep = max(qualified, key=lambda r: (r["area"], -r["fi"]))
    area_sum = sum(rec["area"] for rec in qualified)
    if area_sum > 1e-12:
        cx = cy = cz = 0.0
        for rec in qualified:
            fc = _face_centroid(rec["face"])
            cx += fc[0] * rec["area"]
            cy += fc[1] * rec["area"]
            cz += fc[2] * rec["area"]
        centroid = (cx / area_sum, cy / area_sum, cz / area_sum)
    else:
        centroid = _face_centroid(rep["face"])

    entry: dict[str, Any] = {
        "kind": "fillet",
        "radius_mm": round(radius, 4),
        "face_index": rep["fi"],
        "edge_length_mm": round(total_len, 4),
        "fragment_count": len(group),
        "centroid": [round(c, 4) for c in centroid],
    }
    cv = _fillet_convexity(
        rep["face"], rep["cyl"] if rep["cyl"] is not None else rep["tor"],
    )
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
            "walls and split cylinder halves never false-positive. A3-FIX "
            "(2026-06-11): fragments of one boolean-split blend surface merge "
            "into a single entry, the arc cap applies to the group UNION, and "
            "sub-radius stubs (castellation/spotface walls) are dropped — "
            "makes the inventory preserve_brep round-trip stable. Result on "
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
        # A3-FIX (2026-06-11): round-trip stability gates — see module
        # docstring for the Ventilator / Crystal_SMD diagnosis.
        merge_coaxial_fragments: bool = Field(
            default=True,
            description="Aggregate fragments of the SAME cylinder/torus "
                        "surface (split by boolean cuts) into one fillet "
                        "entry; the angular-extent gate then applies to the "
                        "UNION of the fragments, so a split bore / rim wall "
                        "is rejected whole. False restores per-face entries.",
        )
        min_length_to_radius_ratio: float = Field(
            default=1.0, ge=0.0,
            description="Reject fillet groups whose total tangency-seam "
                        "length is below ratio × radius — sub-radius stubs "
                        "are castellation / spotface corner walls (often "
                        "double-counted by classify_holes), not designed "
                        "edge blends. 0 disables.",
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

        # A3-FIX (2026-06-11): two-phase fillet pipeline — per-face
        # FRAGMENT collection, then group-level (same-surface) decision.
        # Chamfers stay per-face (planar strips; no instability observed
        # on the corpus — don't gate blindly).
        fragments: list[dict] = []
        chamfers: list[dict[str, Any]] = []
        for fi, face in enumerate(faces):
            try:
                edge_records = _edges_with_neighbours(
                    face, edge_faces, face_idx_map, fi,
                )
                if not edge_records:
                    continue
                rec = _fillet_fragment(face, fi, edge_records, args)
                if rec is not None:
                    fragments.append(rec)
                    continue  # cylinder/torus face — never a chamfer strip
                entry = _try_chamfer(face, fi, edge_records, args)
                if entry is not None:
                    chamfers.append(entry)
            except Exception:
                # per-face isolation — one pathological face must not
                # poison the inventory.
                continue

        entries: list[dict[str, Any]] = list(chamfers)
        for group in _group_fragments(
            fragments, merge=args.merge_coaxial_fragments,
        ):
            try:
                entry = _fillet_group_entry(group, args)
            except Exception:
                continue  # group isolation, same rationale as per-face
            if entry is not None:
                entries.append(entry)

        # deterministic ordering — by representative face index, as the
        # old single per-face loop produced.
        entries.sort(key=lambda e: e["face_index"])
        blends: list[dict[str, Any]] = []
        for entry in entries:
            entry["id"] = len(blends)
            blends.append(entry)

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"edge_blends": blends},
        )
