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

False-positive filters
----------------------
On mesh-derived shells (mesh_to_brep on a decimated STL) the face graph
contains hundreds of small concave clusters that look like pockets to the
seed/component pass but are really tessellation noise. Four args reject
each class of artefact:

    min_depth_mm                 — drop sub-noise pockets (default 0.1).
    min_top_d_mm                 — drop sub-mm openings.
    min_depth_to_width_ratio     — drop "flat patch" pockets (depth ≪ top_d).
    min_face_count_per_pocket    — drop 1-2 face creases.

Default values are 0/1 (backward-compatible — no filtering).

iPhone-tuned defaults
~~~~~~~~~~~~~~~~~~~~~
For raw iPhone-class teardown meshes decimated to ~7 k faces, the
following set drops the unfiltered count of ~17 mesh-artefact pockets to
≤8 real pockets while keeping every physically meaningful cavity:

    {
        "min_top_d_mm": 2.0,
        "min_face_count_per_pocket": 3,
        "min_depth_to_width_ratio": 0.05,
    }

Pass these via ``extract_feature_catalog(classify_pockets_extra_args=...)``
or directly to ``ClassifyPockets().apply(body, {...})``.

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
# COMPLEX-CAD pass-6 dehardcode (2026-06-09): single source of truth in
# _face_count_guard so the cap doesn't drift between modules; override
# with PHONE_DESIGNER_MAX_FACE_COUNT.
from phone_designer.skills._face_count_guard import DEFAULT_MAX_FACE_COUNT as _DEFAULT_MAX_FACE_COUNT


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


def _components(seeds, pairs, faces=None, area_ratio_floor=0.0):
    """Connected components of `seeds` linked via shared edges.

    COMPLEX-CAD fix (2026-06-08): when ``faces`` is supplied AND
    ``area_ratio_floor`` > 0, skip adjacencies where the two faces' areas
    differ by more than 1/``area_ratio_floor``. Intended to prevent the
    connected-component pass from glueing tiny pockets onto adjacent
    huge pockets.

    Default is 0.0 (no split, backward-compatible). Verified that on
    as1-oc-214 the silhouette-guard fix in plan_from_feature_catalog
    already prevents the spurious big cuts that caused the coalescence,
    so this defensive backstop is opt-in only. Tests on linkrods showed
    the floor=0.05/0.01 variants over-fragmented small-body pockets.
    """
    areas: dict[int, float] | None = None
    if faces is not None:
        from phone_designer.skills._resolvers import _face_area
        areas = {s: max(_face_area(faces[s]), 1e-9) for s in seeds}

    adj: dict[int, set[int]] = {s: set() for s in seeds}
    for a, b in pairs:
        if a in seeds and b in seeds:
            if areas is not None:
                aa, ab = areas[a], areas[b]
                if min(aa, ab) / max(aa, ab) < area_ratio_floor:
                    continue
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
# Footprint classification + entry anchor (pass-26, 2026-06-11, A10/P9)
#
# SIBLING-FIELD CONTRACT (pass-23 pattern, pass-18/22 lesson): everything in
# this section emits NEW fields next to the existing ones. ``axis_origin``
# (floor centroid) is the IMMUTABLE round-trip identity that preserve_brep
# self-match depends on — never read-modify-write it here.

#: anti-parallel / parallel wall pairing gate — cos(2°).
_FP_PAIR_COS = 0.99939
#: a planar wall's normal must be ⊥ pocket axis within ~3°.
_FP_WALL_AXIS_DOT_MAX = 0.05
#: a cylindrical wall's axis must be ∥ pocket axis (cos ≈ 11° guard).
_FP_CYL_AXIS_DOT_MIN = 0.98


def _fp_canon_axis(n: tuple[float, float, float]) -> tuple[float, float, float]:
    """Flip ``n`` so its largest-|component| is positive. Sign-stable
    canonical hemisphere — plan_from_feature_catalog applies the SAME rule
    so ``footprint_angle_deg`` (measured about this axis) round-trips even
    when the emission-side axis points the other way."""
    comps = (abs(n[0]), abs(n[1]), abs(n[2]))
    k = comps.index(max(comps))
    if n[k] < 0.0:
        return (-n[0], -n[1], -n[2])
    return (n[0], n[1], n[2])


def _fp_inplane_frame(
    n: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """(u, v) orthonormal in-plane frame for axis ``n`` — the SAME
    Gram-Schmidt rule as extrude_pocket_world._build_world_prism (project
    world +X, or +Y when n is X-dominant) so the angle convention matches
    the cutting tool's local frame exactly."""
    if abs(n[0]) < 0.9:
        ref = (1.0, 0.0, 0.0)
    else:
        ref = (0.0, 1.0, 0.0)
    dot = ref[0] * n[0] + ref[1] * n[1] + ref[2] * n[2]
    ux = ref[0] - dot * n[0]
    uy = ref[1] - dot * n[1]
    uz = ref[2] - dot * n[2]
    umag = math.sqrt(ux * ux + uy * uy + uz * uz)
    if umag < 1e-9:
        ux, uy, uz, umag = 0.0, 1.0, 0.0, 1.0
    u = (ux / umag, uy / umag, uz / umag)
    v = (
        n[1] * u[2] - n[2] * u[1],
        n[2] * u[0] - n[0] * u[2],
        n[0] * u[1] - n[1] * u[0],
    )
    return u, v


def _fp_norm_angle_deg(deg: float) -> float:
    """Normalize to (-90, 90] — a rectangle/slot footprint has 180° symmetry."""
    a = math.fmod(deg, 180.0)
    if a > 90.0:
        a -= 180.0
    elif a <= -90.0:
        a += 180.0
    return a


def _footprint_entry_fields(faces, comp, axis_origin, axis_dir, top_d):
    """Classify the pocket's in-plane footprint from its WALL faces and
    compute the entry anchor. Returns a dict of SIBLING fields only:

        footprint_kind      "circular" | "rectangular" | "slot" | "freeform"
        footprint_width_mm  float | None   (circular: == footprint_length_mm)
        footprint_length_mm float | None   (>= width by convention)
        footprint_angle_deg float | None   in-plane rotation of the LENGTH
                                           direction about the CANONICAL
                                           pocket axis, in (-90, 90]
        pocket_entry_origin [x, y, z] | None — top-plane entry centroid
                                           (pass-23 sibling-field pattern)
        pocket_entry_depth_mm float | None — axial span entry→floor; body-
                                           clipped by construction (faces
                                           live on the body)

    NAMING (pass-26 hard lesson, 2026-06-11): the fields are pocket-prefixed
    — NOT the holes' literal ``entry_origin`` / ``entry_depth_mm`` —
    because feature_fidelity_diff._xyz_of PREFERS the exact key
    ``entry_origin`` for spatial pairing. First spot-check round with the
    unprefixed name: as1_pe_203 preserve_brep 1.0 → 0.774 (freeform
    span-derived entries don't round-trip on a cut-modified body) and
    as1_pe_203 box pockets 12 → 5 paired (orig-side freeform pockets have
    floor-anchored coords while regen-side clean rect cuts carry top-plane
    entries — asymmetric values for the same cavity). The prefixed name is
    invisible to the diff's exact-key lookup, so pairing keeps using the
    immutable floor-centroid ``axis_origin`` on both sides, while the
    box-mode planner still gets its entry anchor.

    Wall taxonomy:
      circular     — exactly one coaxial cylindrical wall group, no planar
                     walls.
      rectangular  — 4 planar wall sides in 2 anti-parallel pairs (within
                     ~2°), pairs mutually perpendicular, no axis-parallel
                     cylinder walls.
      slot         — 2 anti-parallel planar walls + 2 cylinder cap groups
                     whose radius ≈ gap/2 placed along the wall direction.
      freeform     — everything else (planner keeps the legacy square
                     proxy, behaviour unchanged).

    Fillet residue (torus faces / cylinders whose axis is ⊥ the pocket
    axis) is tolerated — it decorates the walls without changing the
    footprint. Cones / spheres / generic surfaces force freeform.
    """
    out: dict[str, Any] = {
        "footprint_kind": "freeform",
        "footprint_width_mm": None,
        "footprint_length_mm": None,
        "footprint_angle_deg": None,
        "pocket_entry_origin": None,
        "pocket_entry_depth_mm": None,
    }
    o = (float(axis_origin[0]), float(axis_origin[1]), float(axis_origin[2]))
    nx, ny, nz = (float(axis_dir[0]), float(axis_dir[1]), float(axis_dir[2]))
    nmag = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nmag < 1e-9:
        return out
    n = _fp_canon_axis((nx / nmag, ny / nmag, nz / nmag))
    u, v = _fp_inplane_frame(n)

    def _proj(p) -> float:
        return (
            (p[0] - o[0]) * n[0] + (p[1] - o[1]) * n[1] + (p[2] - o[2]) * n[2]
        )

    def _inplane(p) -> tuple[float, float]:
        dx, dy, dz = p[0] - o[0], p[1] - o[1], p[2] - o[2]
        return (
            dx * u[0] + dy * u[1] + dz * u[2],
            dx * v[0] + dy * v[1] + dz * v[2],
        )

    from phone_designer.skills._resolvers import (
        _face_area,
        _face_center,
        _face_normal_at_center,
    )

    wall_planes: list[tuple[tuple[float, float], tuple[float, float], float]] = []
    # each: (unit 2D normal, 2D rep point, area)
    wall_cyls: list[tuple[tuple[float, float], float]] = []  # (2D center, radius)
    floor_projs: list[float] = []
    span_lo = float("inf")
    span_hi = float("-inf")
    blocking = False  # cone / sphere / generic surface → freeform
    n_cyl_faces = 0  # any cylinder ⇒ _classify_one derived axis from cyls

    for fi in comp:
        f = faces[fi]
        kind = _surface_kind(f)
        if kind == "plane":
            fn = _face_normal_at_center(f)
            if fn == (0.0, 0.0, 0.0):
                blocking = True
                continue
            d = fn[0] * n[0] + fn[1] * n[1] + fn[2] * n[2]
            mn, mx = _face_bbox(f)
            for corner in (mn, mx):
                pr = _proj(corner)
                span_lo = min(span_lo, pr)
                span_hi = max(span_hi, pr)
            if abs(d) >= 0.85:
                floor_projs.append(_proj(_face_center(f)))
                continue
            if abs(d) > _FP_WALL_AXIS_DOT_MAX:
                # tilted plane (chamfer ring, draft) — tolerated, no wall.
                continue
            n2 = (
                fn[0] * u[0] + fn[1] * u[1] + fn[2] * u[2],
                fn[0] * v[0] + fn[1] * v[1] + fn[2] * v[2],
            )
            m2 = math.sqrt(n2[0] * n2[0] + n2[1] * n2[1])
            if m2 < 1e-9:
                continue
            wall_planes.append((
                (n2[0] / m2, n2[1] / m2),
                _inplane(_face_center(f)),
                max(_face_area(f), 1e-9),
            ))
        elif kind == "cylinder":
            n_cyl_faces += 1
            info = _cylinder_info(f)
            if info is None:
                blocking = True
                continue
            loc, cdir, r = info
            cmag = math.sqrt(cdir[0] ** 2 + cdir[1] ** 2 + cdir[2] ** 2)
            if cmag < 1e-9:
                continue
            adot = abs(
                (cdir[0] * n[0] + cdir[1] * n[1] + cdir[2] * n[2]) / cmag
            )
            mn, mx = _face_bbox(f)
            for corner in (mn, mx):
                pr = _proj(corner)
                span_lo = min(span_lo, pr)
                span_hi = max(span_hi, pr)
            if adot >= _FP_CYL_AXIS_DOT_MIN:
                wall_cyls.append((_inplane(loc), float(r)))
            # else: floor-edge fillet residue — tolerated.
        elif kind == "torus":
            # fillet ring residue — tolerated; contributes to the span only.
            mn, mx = _face_bbox(f)
            for corner in (mn, mx):
                pr = _proj(corner)
                span_lo = min(span_lo, pr)
                span_hi = max(span_hi, pr)
        else:
            # cone / sphere / generic — footprint is not prismatic.
            blocking = True

    # ── entry anchor (computed for EVERY pocket, freeform included) ────────
    cx = cy = 0.0  # in-plane footprint centre, default = axis_origin
    have_span = span_hi > span_lo and math.isfinite(span_lo)
    entry_e: float | None = None
    if have_span:
        if n_cyl_faces == 0 and floor_projs:
            # Planar-floor pocket: axis_origin IS the largest floor's
            # centroid, i.e. proj 0 by construction. The entry is the span
            # end FARTHEST from it. (pass-26 fix: stepped pockets have
            # floor rings at BOTH span ends — the previous nearest-floor
            # rule tied and returned the floor end itself, collapsing the
            # entry onto axis_origin and disabling footprint-true emission
            # on every as1-oc-214 rectangular pocket.)
            entry_e = span_hi if abs(span_hi) >= abs(span_lo) else span_lo
        elif floor_projs:
            # Cylinder-derived axis (axis_origin's axial position is the
            # OCCT parametric location, not the floor): entry is the span
            # end AWAY from the nearest floor.
            d_lo = min(abs(fp - span_lo) for fp in floor_projs)
            d_hi = min(abs(fp - span_hi) for fp in floor_projs)
            entry_e = span_hi if d_lo <= d_hi else span_lo
        else:
            entry_e = span_lo  # through pocket — deterministic end pick

    # ── side grouping (merge co-planar wall fragments) ─────────────────────
    sides: list[dict[str, Any]] = []  # {dir:(x,y), offset_sum, area_sum}
    for d2, p2, area in wall_planes:
        merged = False
        for s in sides:
            if d2[0] * s["dir"][0] + d2[1] * s["dir"][1] >= _FP_PAIR_COS:
                s["offset_sum"] += (
                    (p2[0] * s["dir"][0] + p2[1] * s["dir"][1]) * area
                )
                s["area_sum"] += area
                merged = True
                break
        if not merged:
            sides.append({
                "dir": d2,
                "offset_sum": (p2[0] * d2[0] + p2[1] * d2[1]) * area,
                "area_sum": area,
            })
    for s in sides:
        s["offset"] = s["offset_sum"] / s["area_sum"]

    # ── coaxial cylinder-fragment merging (classify_edge_blends idea) ──────
    pos_tol = max(0.1, 0.02 * max(float(top_d or 0.0), 1.0))
    cyl_groups: list[dict[str, Any]] = []  # {cx, cy, r, count}
    for c2, r in wall_cyls:
        merged = False
        for g in cyl_groups:
            if (
                abs(r - g["r"]) <= max(0.05, 0.02 * max(g["r"], 1e-9))
                and math.hypot(c2[0] - g["cx"], c2[1] - g["cy"]) <= pos_tol
            ):
                k = g["count"]
                g["cx"] = (g["cx"] * k + c2[0]) / (k + 1)
                g["cy"] = (g["cy"] * k + c2[1]) / (k + 1)
                g["r"] = (g["r"] * k + r) / (k + 1)
                g["count"] = k + 1
                merged = True
                break
        if not merged:
            cyl_groups.append({"cx": c2[0], "cy": c2[1], "r": r, "count": 1})

    # ── anti-parallel side pairing ─────────────────────────────────────────
    pairs: list[dict[str, float]] = []  # {dir, sep, center} along dir
    used: set[int] = set()
    paired_ok = True
    for i in range(len(sides)):
        if i in used:
            continue
        partner = -1
        for j in range(i + 1, len(sides)):
            if j in used:
                continue
            dd = (
                sides[i]["dir"][0] * sides[j]["dir"][0]
                + sides[i]["dir"][1] * sides[j]["dir"][1]
            )
            if dd <= -_FP_PAIR_COS:
                partner = j
                break
        if partner < 0:
            paired_ok = False
            break
        used.add(i)
        used.add(partner)
        # plane i: dot(p, di) = oi; plane j in i's frame: dot(p, di) = -oj.
        oi = sides[i]["offset"]
        oj = sides[partner]["offset"]
        pairs.append({
            "dx": sides[i]["dir"][0],
            "dy": sides[i]["dir"][1],
            "sep": abs(oi + oj),
            "center": (oi - oj) / 2.0,
        })

    # ── decision tree ──────────────────────────────────────────────────────
    if not blocking:
        if not sides and len(cyl_groups) == 1 and wall_cyls:
            g = cyl_groups[0]
            d_fp = float(top_d) if float(top_d or 0.0) > 0.0 else 2.0 * g["r"]
            out["footprint_kind"] = "circular"
            out["footprint_width_mm"] = round(d_fp, 4)
            out["footprint_length_mm"] = round(d_fp, 4)
            out["footprint_angle_deg"] = 0.0
            cx, cy = g["cx"], g["cy"]
        elif paired_ok and len(pairs) == 2 and len(sides) == 4 and not cyl_groups:
            p1, p2 = pairs
            perp = abs(p1["dx"] * p2["dx"] + p1["dy"] * p2["dy"])
            if perp <= _FP_WALL_AXIS_DOT_MAX and p1["sep"] > 0 and p2["sep"] > 0:
                if p1["sep"] >= p2["sep"]:
                    big, small = p1, p2
                else:
                    big, small = p2, p1
                out["footprint_kind"] = "rectangular"
                out["footprint_length_mm"] = round(big["sep"], 4)
                out["footprint_width_mm"] = round(small["sep"], 4)
                out["footprint_angle_deg"] = round(
                    _fp_norm_angle_deg(
                        math.degrees(math.atan2(big["dy"], big["dx"]))
                    ), 4,
                )
                cx = p1["center"] * p1["dx"] + p2["center"] * p2["dx"]
                cy = p1["center"] * p1["dy"] + p2["center"] * p2["dy"]
        elif paired_ok and len(pairs) == 1 and len(sides) == 2 and len(cyl_groups) == 2:
            gap = pairs[0]["sep"]
            g1, g2 = cyl_groups
            r_tol = max(0.15, 0.12 * gap * 0.5)
            cap_dx = g2["cx"] - g1["cx"]
            cap_dy = g2["cy"] - g1["cy"]
            cap_dist = math.hypot(cap_dx, cap_dy)
            if (
                gap > 0.0
                and abs(g1["r"] - gap * 0.5) <= r_tol
                and abs(g2["r"] - gap * 0.5) <= r_tol
                and cap_dist > max(0.1, 0.25 * gap)
            ):
                slot_dir = (cap_dx / cap_dist, cap_dy / cap_dist)
                # slot axis must run ALONG the walls (⊥ the pair normal).
                along = abs(
                    slot_dir[0] * pairs[0]["dx"] + slot_dir[1] * pairs[0]["dy"]
                )
                if along <= 0.10:
                    out["footprint_kind"] = "slot"
                    out["footprint_width_mm"] = round(gap, 4)
                    out["footprint_length_mm"] = round(
                        cap_dist + g1["r"] + g2["r"], 4,
                    )
                    out["footprint_angle_deg"] = round(
                        _fp_norm_angle_deg(
                            math.degrees(math.atan2(slot_dir[1], slot_dir[0]))
                        ), 4,
                    )
                    cx = (g1["cx"] + g2["cx"]) / 2.0
                    cy = (g1["cy"] + g2["cy"]) / 2.0

    # pass-26 (2026-06-11): the pocket-prefixed key names keep these values
    # OUT of feature_fidelity_diff._xyz_of's exact-key preference list (see
    # the NAMING note in the docstring) — pairing keeps using axis_origin
    # on both sides, so emitting for every kind (freeform included) is
    # safe and gives the planner maximal anchor coverage.
    if entry_e is not None:
        out["pocket_entry_origin"] = [
            round(o[0] + cx * u[0] + cy * v[0] + entry_e * n[0], 4),
            round(o[1] + cx * u[1] + cy * v[1] + entry_e * n[1], 4),
            round(o[2] + cx * u[2] + cy * v[2] + entry_e * n[2], 4),
        ]
        out["pocket_entry_depth_mm"] = round(span_hi - span_lo, 4)
    return out


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

    # ── Side wall axial spans (for rectangular extrude_pocket) ─────────────
    # COMPLEX-CAD pass-9 (2026-06-09): pure-rectangular pockets (cut by
    # extrude_pocket, no cylindrical surface) have 4 side walls whose
    # normals are PERPENDICULAR to the pocket axis (dot < 0.85). The
    # original depth pipeline skipped them and fell through to depth=0
    # when there was only 1 floor level — so every box-mode extrude_pocket
    # regen-detected pocket had depth=0 and got filtered out, even though
    # the geometry was perfect. Collect each side-wall's axis-span as a
    # fallback depth signal.
    side_wall_axis_spans: list[tuple[float, float]] = []
    for fi in plane_idx:
        n = _face_normal_at_center(faces[fi])
        dot = n[0] * ax + n[1] * ay + n[2] * az
        if abs(dot) >= 0.85:
            continue  # already counted as floor
        bb = _face_bbox(faces[fi])
        mn, mx = bb
        lo = (mn[0] - axis_origin[0]) * ax + (mn[1] - axis_origin[1]) * ay + (mn[2] - axis_origin[2]) * az
        hi = (mx[0] - axis_origin[0]) * ax + (mx[1] - axis_origin[1]) * ay + (mx[2] - axis_origin[2]) * az
        side_wall_axis_spans.append((min(lo, hi), max(lo, hi)))

    # ── Diameter / depth ───────────────────────────────────────────────────
    if cyl_endpoints:
        cyl_endpoints.sort(key=lambda t: t[0])
        # depth: total span along axis covered by cylinder faces
        depth = abs(cyl_endpoints[-1][0] - cyl_endpoints[0][0])
        top_r = cyl_endpoints[-1][1]
        bot_r = cyl_endpoints[0][1]
        top_d = 2.0 * top_r
        bot_d = 2.0 * bot_r
    elif side_wall_axis_spans:
        # COMPLEX-CAD pass-9: rectangular pocket — use the side wall axial
        # extent as depth. The opening and floor are on the same XY footprint
        # so the side-wall span captures the pocket depth.
        wall_lows = [s[0] for s in side_wall_axis_spans]
        wall_highs = [s[1] for s in side_wall_axis_spans]
        depth = abs(max(wall_highs) - min(wall_lows))
        # Use the floor level's in-plane radius if available, else 0.
        if distinct_radii:
            top_r = max(distinct_radii)
            bot_r = max(distinct_radii)
        else:
            top_r = bot_r = 0.0
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

    desc = {
        "type": ptype,
        "axis_origin": [round(c, 4) for c in axis_origin],
        "axis_dir": [round(c, 4) for c in axis_dir],
        "top_d_mm": round(top_d, 4),
        "bottom_d_mm": round(bot_d, 4),
        "depth_mm": round(depth, 4),
        "face_indices": sorted(comp),
    }
    # COMPLEX-CAD pass-26 (2026-06-11, A10/P9): footprint + entry SIBLING
    # fields. axis_origin above stays the untouched floor-centroid identity
    # (pass-18 revert / pass-23 lesson). Defensive try/except: an OCCT probe
    # failure on a degenerate corpus shell must never break pocket detection
    # itself — the sibling fields just degrade to freeform/None.
    try:
        desc.update(
            _footprint_entry_fields(faces, comp, axis_origin, axis_dir, top_d)
        )
    except Exception:
        desc.update({
            "footprint_kind": "freeform",
            "footprint_width_mm": None,
            "footprint_length_mm": None,
            "footprint_angle_deg": None,
            "pocket_entry_origin": None,
            "pocket_entry_depth_mm": None,
        })
    return desc


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
        min_top_d_mm: float = Field(
            default=0.0, ge=0.0,
            description="Reject pockets whose top opening diameter < this. "
                        "Useful on mesh-derived shells where decimation creates "
                        "sub-mm concave clusters that aren't real pockets.",
        )
        min_depth_to_width_ratio: float = Field(
            default=0.0, ge=0.0,
            description="Reject pockets whose depth/top_d ratio < this. A flat "
                        "patch (depth=0.1, top_d=20) would have ratio 0.005 — "
                        "set this to 0.05 or higher to filter such artefacts.",
        )
        min_face_count_per_pocket: int = Field(
            default=1, ge=1,
            description="Reject pockets whose face_indices count < this. A real "
                        "pocket usually has ≥3 faces (opening, bottom, sides); "
                        "raise this to 3 on mesh shells to drop 2-face creases.",
        )
        min_top_d_frac: float = Field(
            default=0.0, ge=0.0,
            description="COMPLEX-CAD fix (2026-06-07): reject pockets whose "
                        "top opening diameter < frac × bbox diagonal. Scales "
                        "with part size so iPhone-tuned absolute mm thresholds "
                        "don't underfire on 200-1000 mm industrial parts. "
                        "Final threshold is max(min_top_d_mm, min_top_d_frac "
                        "× bbox_diag). Default 0.0 = backward-compatible.",
        )
        min_depth_frac: float = Field(
            default=0.0, ge=0.0,
            description="COMPLEX-CAD fix (2026-06-07): reject pockets whose "
                        "depth < frac × bbox diagonal. Final threshold is "
                        "max(min_depth_mm, min_depth_frac × bbox_diag). "
                        "Default 0.0 = backward-compatible.",
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
        # COMPLEX-CAD fix (2026-06-08): pass faces so _components can split
        # heterogeneous-scale neighbours (prevents small pockets glueing
        # onto adjacent huge pocket walls — see as1-oc-214 analysis).
        comps = _components(seeds, pairs, faces=faces)

        # COMPLEX-CAD fix (2026-06-07): bbox-relative filter thresholds.
        # The fixed-mm filters (min_top_d_mm=2.0 etc) were iPhone-tuned and
        # underfire on 200-1000 mm industrial parts whose noise floor is
        # proportionally larger. Final threshold = max(absolute, frac × diag).
        (_mn, _mx) = body_bbox
        _diag = math.sqrt(
            (_mx[0] - _mn[0]) ** 2 + (_mx[1] - _mn[1]) ** 2 + (_mx[2] - _mn[2]) ** 2
        )
        _eff_top_d_min = max(float(args.min_top_d_mm), float(args.min_top_d_frac) * _diag)
        _eff_depth_min = max(float(args.min_depth_mm), float(args.min_depth_frac) * _diag)

        pockets: list[dict[str, Any]] = []
        filtered = 0
        for comp in comps:
            desc = _classify_one(faces, comp, body_bbox)
            # 4 filters — each one rejects a different class of artefact.
            top_d = float(desc.get("top_d_mm") or 0.0)
            depth = float(desc.get("depth_mm") or 0.0)
            fc = len(desc.get("face_indices") or [])
            if depth < _eff_depth_min:
                filtered += 1; continue
            if top_d < _eff_top_d_min:
                filtered += 1; continue
            if fc < args.min_face_count_per_pocket:
                filtered += 1; continue
            if args.min_depth_to_width_ratio > 0.0:
                width = max(top_d, 1e-9)
                if (depth / width) < args.min_depth_to_width_ratio:
                    filtered += 1; continue
            # COMPLEX-CAD pass-18 REVERTED (2026-06-09): tried importing
            # classify_holes._standardize_entry and applying it to
            # pockets to align the axis_origin convention. Result mixed:
            # linkrods +0.08 (good) but as1_pe_203 preserve_brep 1.0 →
            # 0.97 (regression). The pocket's natural axis_origin (floor
            # centroid) is what downstream consumers EXPECT for the
            # round-trip identity case; standardising it to the entry
            # breaks that. Holes are different because the planner
            # emits cylinder cuts FROM the entry; pockets get an
            # extrude_pocket whose sketch references the floor centroid.
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
                # COMPLEX-CAD pass-26 (2026-06-11, A10/P9): footprint +
                # entry sibling fields (pass-23 pattern — axis_origin above
                # is untouched). Consumed by plan_from_feature_catalog's
                # BOX-MODE footprint-true emission only. Entry keys are
                # pocket-prefixed ON PURPOSE — see the NAMING note on
                # _footprint_entry_fields (the holes' literal entry_origin
                # key is preferred by feature_fidelity_diff._xyz_of and
                # regressed both pb self-match and box pairing).
                "footprint_kind": desc.get("footprint_kind", "freeform"),
                "footprint_width_mm": desc.get("footprint_width_mm"),
                "footprint_length_mm": desc.get("footprint_length_mm"),
                "footprint_angle_deg": desc.get("footprint_angle_deg"),
                "pocket_entry_origin": desc.get("pocket_entry_origin"),
                "pocket_entry_depth_mm": desc.get("pocket_entry_depth_mm"),
            })

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"pockets": pockets, "pockets_filtered_count": filtered},
        )
