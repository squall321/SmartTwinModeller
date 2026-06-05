"""classify_pockets — atomic, read-only.

Detect every pocket / hole / cavity on the body and classify it.

Strategy
--------
We use the face adjacency graph together with surface-type heuristics to
locate recessed face clusters:

    1. Build the face graph (helpers reused from face_adjacency_graph).
    2. Seed pocket faces:
         - every cylindrical, conical or spherical face (revolved cavities)
         - every planar face whose centre lies STRICTLY inside the body
           bounding box and whose outward normal is body-axis aligned
           (extruded pocket floors and side walls).
       The outer shell faces are excluded because their centres lie ON the
       bbox surface — never strictly interior.
    3. Group the seed faces into connected components via shared edges.
    4. Classify each component:
         spherical — at least one spherical face present
         conical   — at least one conical face present (no sphere)
         stepped   — two or more planar floors at distinct axis projections
         blind     — a single planar floor (closed bottom)
         through   — no planar floor at all (open both sides)

extras["pockets"] = [
    {
        "id": int,
        "type": "blind"|"through"|"stepped"|"conical"|"spherical",
        "axis_origin": [x, y, z],
        "axis_dir":    [x, y, z],   # unit
        "top_d_mm":    float,
        "bottom_d_mm": float,
        "depth_mm":    float,
        "face_indices": [int, ...],
    },
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

# Above this raw face count the O(F·E) adjacency walk becomes the dominant
# cost (>30s on 16 k-face shells even with the OCCT indexed-map fast path is
# fine; it's the per-face surface-type / centre / area probing that explodes).
# Mirror the extract_feature_catalog guard: bail with skipped=True so callers
# decimate the mesh first.
_DEFAULT_MAX_FACE_COUNT = 8000


# ──────────────────────────────────────────────────────────────────────────────
# Geometry helpers


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _body_bbox(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def _face_bbox(face):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(face, bb)
    except Exception:
        BRepBndLib.Add_s(face, bb)
    if bb.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def _surface_kind(face) -> str:
    """plane / cylinder / cone / sphere / torus / other."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import (
        GeomAbs_Cone,
        GeomAbs_Cylinder,
        GeomAbs_Plane,
        GeomAbs_Sphere,
        GeomAbs_Torus,
    )

    surf = BRepAdaptor_Surface(face)
    t = surf.GetType()
    if t == GeomAbs_Plane:
        return "plane"
    if t == GeomAbs_Cylinder:
        return "cylinder"
    if t == GeomAbs_Cone:
        return "cone"
    if t == GeomAbs_Sphere:
        return "sphere"
    if t == GeomAbs_Torus:
        return "torus"
    return "other"


def _cylinder_info(face):
    """(axis_origin, axis_dir, radius) for cylindrical face — else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cylinder:
        return None
    cyl = surf.Cylinder()
    loc = cyl.Location()
    d = cyl.Axis().Direction()
    return (
        (loc.X(), loc.Y(), loc.Z()),
        (d.X(), d.Y(), d.Z()),
        float(cyl.Radius()),
    )


def _is_interior_planar(face, body_bbox, margin=0.5) -> bool:
    """A planar face is considered an interior pocket face when its centre
    is strictly inside the body bbox in at least one axis (the axis along
    which the pocket was carved).
    """
    from phone_designer.skills._resolvers import _face_center, _face_normal_at_center

    n = _face_normal_at_center(face)
    if n == (0.0, 0.0, 0.0):
        return False
    c = _face_center(face)
    (mn, mx) = body_bbox

    # Strict interior along the face's normal direction — the pocket sinks
    # INTO the body along that axis, so the face must not sit on the bbox
    # boundary of that axis.
    nx, ny, nz = n
    on_boundary = False
    if abs(nx) > 0.9:
        on_boundary = (abs(c[0] - mn[0]) < margin) or (abs(c[0] - mx[0]) < margin)
    elif abs(ny) > 0.9:
        on_boundary = (abs(c[1] - mn[1]) < margin) or (abs(c[1] - mx[1]) < margin)
    elif abs(nz) > 0.9:
        on_boundary = (abs(c[2] - mn[2]) < margin) or (abs(c[2] - mx[2]) < margin)
    return not on_boundary


# ──────────────────────────────────────────────────────────────────────────────
# Face adjacency


def _shared_face_pairs(shape, faces) -> set[tuple[int, int]]:
    """Return all (a, b) face-index pairs that share at least one edge.

    Uses OCCT's ``TopExp::MapShapesAndUniqueAncestors`` to build the
    edge → incident-faces map in a single O(N) pass, then looks each
    incident face's index up in an ``TopTools_IndexedMapOfShape`` of the
    face list. This replaces the previous O(F · E · E_per_face) hand-rolled
    ``IsSame`` loop, which was the dominant cost on 16 k-face shells.
    """
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import (
        TopTools_IndexedDataMapOfShapeListOfShape,
        TopTools_IndexedMapOfShape,
    )

    # face → index lookup (1-based in OCCT, we convert to 0-based)
    face_idx_map = TopTools_IndexedMapOfShape()
    for f in faces:
        face_idx_map.Add(f)

    # edge → list-of-incident-faces
    edge_faces = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndUniqueAncestors_s(
        shape, TopAbs_EDGE, TopAbs_FACE, edge_faces,
    )

    pairs: set[tuple[int, int]] = set()
    n = edge_faces.Extent()
    for i in range(1, n + 1):
        owners = edge_faces.FindFromIndex(i)
        if owners.Extent() < 2:
            continue
        # collect 0-based face indices; OCCT IndexedMap is 1-based.
        owner_indices: list[int] = []
        for sh in owners:
            fi1 = face_idx_map.FindIndex(sh)
            if fi1 <= 0:
                continue
            owner_indices.append(fi1 - 1)
        # emit every unordered pair within this edge's owner set
        m = len(owner_indices)
        for ii in range(m):
            ia = owner_indices[ii]
            for jj in range(ii + 1, m):
                ib = owner_indices[jj]
                if ia == ib:
                    continue
                a, b = (ia, ib) if ia < ib else (ib, ia)
                pairs.add((a, b))
    return pairs


# ──────────────────────────────────────────────────────────────────────────────
# Pocket cluster discovery


def _seed_pocket_faces(faces, body_bbox) -> set[int]:
    """A face is a pocket-seed candidate if it is cylindrical / conical /
    spherical (revolved cavity wall), OR a planar face whose centre is
    strictly interior to the body bbox along its normal axis (extruded-pocket
    side or floor).
    """
    seeds: set[int] = set()
    for i, f in enumerate(faces):
        kind = _surface_kind(f)
        if kind in ("cylinder", "cone", "sphere", "torus"):
            seeds.add(i)
        elif kind == "plane":
            if _is_interior_planar(f, body_bbox):
                seeds.add(i)
    return seeds


def _components(seeds, pairs):
    """Connected components of `seeds` linked via shared edges."""
    adj: dict[int, set[int]] = {s: set() for s in seeds}
    for a, b in pairs:
        if a in seeds and b in seeds:
            adj[a].add(b)
            adj[b].add(a)

    components: list[set[int]] = []
    visited: set[int] = set()
    for s in seeds:
        if s in visited:
            continue
        stack = [s]
        comp: set[int] = set()
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            comp.add(cur)
            stack.extend(adj.get(cur, ()) - visited)
        components.append(comp)
    return components


# ──────────────────────────────────────────────────────────────────────────────
# Per-pocket classifier


def _classify_one(faces, comp, body_bbox):
    from phone_designer.skills._resolvers import _face_center, _face_normal_at_center

    cyl_idx: list[int] = []
    cone_idx: list[int] = []
    sphere_idx: list[int] = []
    plane_idx: list[int] = []

    for fi in comp:
        k = _surface_kind(faces[fi])
        if k == "cylinder":
            cyl_idx.append(fi)
        elif k == "cone":
            cone_idx.append(fi)
        elif k == "sphere":
            sphere_idx.append(fi)
        elif k == "plane":
            plane_idx.append(fi)

    # ── Determine pocket axis ──────────────────────────────────────────────
    axis_origin = (0.0, 0.0, 0.0)
    axis_dir = (0.0, 0.0, 1.0)
    if cyl_idx:
        ox = oy = oz = 0.0
        dx = dy = dz = 0.0
        n_used = 0
        for fi in cyl_idx:
            info = _cylinder_info(faces[fi])
            if info is None:
                continue
            (lx, ly, lz), (vx, vy, vz), _ = info
            ox += lx; oy += ly; oz += lz
            # Use a consistent axis sign — collapse signs by aligning each
            # cylinder's axis vector to a canonical hemisphere.
            sgn = 1.0 if (vx + vy + vz) >= 0 else -1.0
            dx += sgn * vx; dy += sgn * vy; dz += sgn * vz
            n_used += 1
        if n_used:
            axis_origin = (ox / n_used, oy / n_used, oz / n_used)
        mag = math.sqrt(dx * dx + dy * dy + dz * dz)
        if mag > 1e-9:
            axis_dir = (dx / mag, dy / mag, dz / mag)
    elif plane_idx:
        # Use the planar face with the largest area as the floor; the inward
        # opposite of its outward normal is the axis "into the body".
        from phone_designer.skills._resolvers import _face_area
        big_fi = max(plane_idx, key=lambda i: _face_area(faces[i]))
        n = _face_normal_at_center(faces[big_fi])
        c = _face_center(faces[big_fi])
        axis_origin = c
        if n != (0.0, 0.0, 0.0):
            # axis points INTO the pocket — opposite of the outward normal of
            # the floor face
            axis_dir = (-n[0], -n[1], -n[2])

    ax, ay, az = axis_dir

    # ── Floor levels along axis ────────────────────────────────────────────
    # Only planar faces whose outward normal is parallel (or anti-parallel)
    # to the pocket axis count as "floors". Side-wall planes that face
    # radially are not floors.
    floor_levels: list[float] = []
    floor_radii: list[float] = []
    for fi in plane_idx:
        n = _face_normal_at_center(faces[fi])
        dot = n[0] * ax + n[1] * ay + n[2] * az
        if abs(dot) < 0.85:
            continue  # side wall, not a floor
        c = _face_center(faces[fi])
        proj = (c[0] - axis_origin[0]) * ax + (c[1] - axis_origin[1]) * ay + (c[2] - axis_origin[2]) * az
        floor_levels.append(proj)
        # in-plane radius estimate = half of largest bbox span perpendicular
        # to the axis
        bb = _face_bbox(faces[fi])
        mn, mx = bb
        spans = (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])
        if abs(az) >= 0.9:
            r = 0.5 * max(spans[0], spans[1])
        elif abs(ax) >= 0.9:
            r = 0.5 * max(spans[1], spans[2])
        elif abs(ay) >= 0.9:
            r = 0.5 * max(spans[0], spans[2])
        else:
            r = 0.5 * max(spans)
        floor_radii.append(r)

    # de-dup floor levels (tolerance 0.5mm)
    distinct_levels: list[float] = []
    distinct_radii: list[float] = []
    for lv, rr in sorted(zip(floor_levels, floor_radii), key=lambda t: t[0]):
        if not distinct_levels or abs(lv - distinct_levels[-1]) > 0.5:
            distinct_levels.append(lv)
            distinct_radii.append(rr)

    # ── Cylinder radii along axis ──────────────────────────────────────────
    cyl_endpoints: list[tuple[float, float]] = []  # (axis_proj, radius)
    for fi in cyl_idx:
        info = _cylinder_info(faces[fi])
        if info is None:
            continue
        _, _, r = info
        bb = _face_bbox(faces[fi])
        mn, mx = bb
        for p in (mn, mx):
            proj = (p[0] - axis_origin[0]) * ax + (p[1] - axis_origin[1]) * ay + (p[2] - axis_origin[2]) * az
            cyl_endpoints.append((proj, r))

    # ── Diameter / depth ───────────────────────────────────────────────────
    if cyl_endpoints:
        cyl_endpoints.sort(key=lambda t: t[0])
        # depth: total span along axis covered by cylinder faces
        depth = abs(cyl_endpoints[-1][0] - cyl_endpoints[0][0])
        top_r = cyl_endpoints[-1][1]
        bot_r = cyl_endpoints[0][1]
        top_d = 2.0 * top_r
        bot_d = 2.0 * bot_r
    elif distinct_levels:
        # Rectangular pocket: depth = max-min floor projection. Top diameter
        # estimated from the upper-most floor's in-plane size.
        depth = abs(distinct_levels[-1] - distinct_levels[0])
        top_d = 2.0 * distinct_radii[-1] if distinct_radii else 0.0
        bot_d = 2.0 * distinct_radii[0] if distinct_radii else 0.0
    else:
        depth = 0.0
        top_d = bot_d = 0.0

    # ── Type tag ───────────────────────────────────────────────────────────
    if sphere_idx:
        ptype = "spherical"
    elif cone_idx:
        ptype = "conical"
    elif len(distinct_levels) >= 2:
        ptype = "stepped"
    elif len(distinct_levels) == 1:
        ptype = "blind"
    else:
        ptype = "through"

    if ptype == "through" and depth < 1e-6:
        # fallback: use body extent along axis
        mn, mx = body_bbox
        depth = max(mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])

    return {
        "type": ptype,
        "axis_origin": [round(c, 4) for c in axis_origin],
        "axis_dir": [round(c, 4) for c in axis_dir],
        "top_d_mm": round(top_d, 4),
        "bottom_d_mm": round(bot_d, 4),
        "depth_mm": round(depth, 4),
        "face_indices": sorted(comp),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="classify_pockets",
    category="inspect",
    level="atomic",
    summary="Detect and classify every pocket / hole / cavity on the body — "
            "blind, through, stepped, conical, or spherical. Connects "
            "cylindrical, conical, spherical and interior-planar faces into "
            "pocket clusters via the face adjacency graph, then inspects "
            "surface types and planar floor levels for the type tag. "
            "Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["pocket_inventory"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="body_present")],
)
class ClassifyPockets(SkillBase):
    class Args(BaseModel):
        min_depth_mm: float = Field(
            default=0.1, ge=0.0,
            description="Pockets whose measured depth is below this threshold "
                        "are filtered out (noise / chamfer-like creases).",
        )
        max_face_count: int | None = Field(
            default=_DEFAULT_MAX_FACE_COUNT,
            description="If the body has more than this many faces, skip "
                        "pocket classification and return extras['pockets']="
                        "{'skipped': True, 'face_count': N, 'reason': "
                        "'too_big'}. Set to None to disable the guard. Default "
                        f"{_DEFAULT_MAX_FACE_COUNT} keeps the analysis under "
                        "~30 s on raw mesh-to-brep shells.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_edges, _all_faces

        shape = _occt_shape(body)
        faces = _all_faces(shape)

        # ── face-count guard — bail on raw mesh-to-brep shells ─────────────
        # The per-face surface-type / centre / area / bbox probes below are
        # not the O(N²) hotspot any more (edge→face adjacency now uses
        # OCCT's indexed map), but a 16 k-face shell still spends ~30 s in
        # OCP just walking faces. Caller should decimate first.
        if args.max_face_count is not None and len(faces) > args.max_face_count:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"pockets": {
                    "skipped": True,
                    "face_count": len(faces),
                    "reason": "too_big",
                    "limit": args.max_face_count,
                    "advice": "decimate the input mesh (mesh_decimate skill) "
                              "or simplify_to_canonical the BREP before "
                              "calling classify_pockets.",
                }},
            )

        edges = _all_edges(shape)
        body_bbox = _body_bbox(shape)

        if not faces or not edges:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"pockets": []},
            )

        pairs = _shared_face_pairs(shape, faces)
        seeds = _seed_pocket_faces(faces, body_bbox)
        comps = _components(seeds, pairs)

        pockets: list[dict[str, Any]] = []
        for comp in comps:
            desc = _classify_one(faces, comp, body_bbox)
            if desc["depth_mm"] < args.min_depth_mm:
                continue
            desc["id"] = len(pockets)
            pockets.append({
                "id": desc["id"],
                "type": desc["type"],
                "axis_origin": desc["axis_origin"],
                "axis_dir": desc["axis_dir"],
                "top_d_mm": desc["top_d_mm"],
                "bottom_d_mm": desc["bottom_d_mm"],
                "depth_mm": desc["depth_mm"],
                "face_indices": desc["face_indices"],
            })

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"pockets": pockets},
        )
