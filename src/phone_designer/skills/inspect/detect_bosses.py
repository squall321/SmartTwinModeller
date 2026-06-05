"""detect_bosses — atomic, read-only.

Find boss-like protrusions on the body. A boss is an outward, localized
piece of material that sticks above (or below, etc.) the dominant base
surface — typically a screw seat, snap-feature mount, or stiffener pad.

Detection strategy
------------------
Rather than rely on edge concavity (which a clean ``BRepAlgoAPI_Fuse`` of
a cylinder onto a flat plate does not produce — the resulting top-face
ring is geometrically convex), we use the following heuristic:

1. Find the *base plane* — the most prominent planar face whose outward
   normal is axis-aligned (±X / ±Y / ±Z). This is the slab top (or
   bottom) on which the bosses sit.
2. Walk every other face on the body and group those that lie on the
   "outside" of the base plane (i.e. the side of the base plane that
   does *not* contain the body interior). Faces are grouped by 2D
   footprint clustering — a face is in the same cluster as another if
   their bbox footprints (projected to the base plane) overlap.
3. Each cluster is a boss candidate. Classify by surface type:
       any cone face            → ``conical``
       any cylinder face        → ``cylindrical``
       all-planar               → ``prismatic``
4. Compute the boss height as the cluster bbox extent along the base-
   plane normal direction; reject clusters shorter than
   ``min_height_mm`` or whose footprint covers most of the base plane
   (these are the slab's own top face, not a boss).

extras schema::

    extras["bosses"] = [
        {
            "id": int,
            "type": "cylindrical" | "prismatic" | "conical",
            "center": [x, y, z],
            "height_mm": float,
            "diameter_or_size_mm": float,
            "face_indices": [int, ...],
        },
        ...
    ]
    extras["boss_count"] = int

Body is unchanged — ``post_conditions=[body_present]``.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# ──────────────────────────────────────────────────────────────────────────────
# Tunables
#
# MAX_FACES_BOSSES — face-count guard for detect_bosses (and indirectly
# detect_ribs / detect_lugs which wrap it). The boss detector performs
# per-base-plane candidate scans + spatial-hash overlap clustering and gets
# expensive on dense meshes (~16k+ faces from imported CAD).
MAX_FACES_BOSSES = 8000


# ──────────────────────────────────────────────────────────────────────────────
# OCCT helpers


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


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
    return {
        int(GeomAbs_Plane): "plane",
        int(GeomAbs_Cylinder): "cylinder",
        int(GeomAbs_Cone): "cone",
        int(GeomAbs_Sphere): "sphere",
        int(GeomAbs_Torus): "torus",
    }.get(t, "other")


def _face_bbox(face) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
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


def _faces_bbox(faces) -> tuple[tuple[float, float, float],
                                  tuple[float, float, float]]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    for f in faces:
        try:
            BRepBndLib.AddOptimal_s(f, bb)
        except Exception:
            BRepBndLib.Add_s(f, bb)
    if bb.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


# Alias kept for backward compat with detect_ribs.py
def _cluster_bbox(faces):
    return _faces_bbox(faces)


def _cylinder_info(face) -> tuple[float, tuple[float, float, float]] | None:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cylinder:
        return None
    cyl = surf.Cylinder()
    d = cyl.Axis().Direction()
    return (float(cyl.Radius()), (d.X(), d.Y(), d.Z()))


def _cone_info(face) -> tuple[float, tuple[float, float, float]] | None:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cone

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cone:
        return None
    cone = surf.Cone()
    d = cone.Axis().Direction()
    return (float(cone.RefRadius()), (d.X(), d.Y(), d.Z()))


# ──────────────────────────────────────────────────────────────────────────────
# Edge-concavity infrastructure (kept for detect_ribs reuse / future heuristics)


def _classify_edges(shape):
    """Return (labels, edge_owners, edges, faces) where ``labels`` maps
    edge_idx → 'convex' | 'concave' | 'tangent'. Used by detect_ribs."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_IN
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Pnt

    from phone_designer.skills._resolvers import _all_edges, _all_faces
    from phone_designer.skills.inspect.edge_concavity_classify import (
        _edge_midpoint_and_tangent,
        _face_normal_at_xyz,
        _PROBE_EPS_MM,
        _TANGENT_DEG,
    )

    edges = _all_edges(shape)
    faces = _all_faces(shape)
    classifier = BRepClass3d_SolidClassifier(shape)

    edge_owners: dict[int, list[int]] = {}
    for fi, face in enumerate(faces):
        fit = TopExp_Explorer(face, TopAbs_EDGE)
        while fit.More():
            fe = TopoDS.Edge_s(fit.Current())
            for ei, e in enumerate(edges):
                if e.IsSame(fe):
                    owners = edge_owners.setdefault(ei, [])
                    if fi not in owners:
                        owners.append(fi)
                    break
            fit.Next()

    labels: dict[int, str] = {}
    for idx, edge in enumerate(edges):
        owners = edge_owners.get(idx, [])
        if len(owners) < 2:
            continue
        f1, f2 = faces[owners[0]], faces[owners[1]]
        try:
            mid, _ = _edge_midpoint_and_tangent(edge)
        except Exception:
            continue
        n1 = _face_normal_at_xyz(f1, mid)
        n2 = _face_normal_at_xyz(f2, mid)
        if n1 is None or n2 is None:
            continue
        dot = max(-1.0, min(1.0, n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]))
        angle = math.degrees(math.acos(dot))
        if angle < _TANGENT_DEG:
            labels[idx] = "tangent"
            continue
        sx, sy, sz = n1[0] + n2[0], n1[1] + n2[1], n1[2] + n2[2]
        mag = math.sqrt(sx * sx + sy * sy + sz * sz)
        if mag < 1e-9:
            labels[idx] = "convex"
            continue
        ux, uy, uz = sx / mag, sy / mag, sz / mag
        probe = gp_Pnt(
            mid[0] + _PROBE_EPS_MM * ux,
            mid[1] + _PROBE_EPS_MM * uy,
            mid[2] + _PROBE_EPS_MM * uz,
        )
        classifier.Perform(probe, 1e-6)
        labels[idx] = "concave" if classifier.State() == TopAbs_IN else "convex"

    return labels, edge_owners, edges, faces


def _cluster_faces_by_non_concave_seams(
    edges, faces, edge_owners, labels
) -> list[list[int]]:
    """Union-find: faces are merged when joined by a convex or tangent
    edge. Concave edges act as cluster boundaries."""
    parent = list(range(len(faces)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for ei, owners in edge_owners.items():
        if len(owners) < 2:
            continue
        if labels.get(ei, "concave") == "concave":
            continue
        union(owners[0], owners[1])

    groups: dict[int, list[int]] = {}
    for fi in range(len(faces)):
        groups.setdefault(find(fi), []).append(fi)
    return list(groups.values())


def _body_total_area(faces) -> float:
    from phone_designer.skills._resolvers import _face_area

    total = 0.0
    for f in faces:
        try:
            total += _face_area(f)
        except Exception:
            continue
    return total


# ──────────────────────────────────────────────────────────────────────────────
# Base plane + projection clustering


_AXES = (
    (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0), (0.0, -1.0, 0.0),
    (0.0, 0.0, 1.0), (0.0, 0.0, -1.0),
)


def _planar_normal(face) -> tuple[float, float, float] | None:
    """Axis-aligned planar outward normal or None."""
    from phone_designer.skills._resolvers import _face_normal_at_center

    n = _face_normal_at_center(face)
    if n == (0.0, 0.0, 0.0):
        return None
    # snap to axis when within 5°
    tol = math.cos(math.radians(5.0))
    for ax in _AXES:
        if n[0] * ax[0] + n[1] * ax[1] + n[2] * ax[2] >= tol:
            return ax
    return None


def _largest_axis_planar_face(faces):
    """Return (face_index, axis) of the largest axis-aligned planar face."""
    from phone_designer.skills._resolvers import _face_area

    best_idx = -1
    best_axis = None
    best_area = -1.0
    for i, f in enumerate(faces):
        ax = _planar_normal(f)
        if ax is None:
            continue
        try:
            a = _face_area(f)
        except Exception:
            continue
        if a > best_area:
            best_area = a
            best_idx = i
            best_axis = ax
    return best_idx, best_axis, best_area


def _candidate_base_planes(faces, top_k: int = 4):
    """Largest few axis-aligned planar faces — each is a base-plane
    candidate. Bosses are detected on *each* candidate plane and the
    union of results is returned (deduplicated by face-index set)."""
    from phone_designer.skills._resolvers import _face_area

    candidates: list[tuple[float, int, tuple[float, float, float]]] = []
    for i, f in enumerate(faces):
        ax = _planar_normal(f)
        if ax is None:
            continue
        try:
            a = _face_area(f)
        except Exception:
            continue
        candidates.append((a, i, ax))
    candidates.sort(reverse=True)
    return candidates[:top_k]


def _project_bbox(face_bbox, axis):
    """Project face bbox to the plane perpendicular to ``axis`` —
    return (min_u, min_v, max_u, max_v, h_min, h_max) where (u,v) are the
    two coordinates not along axis and (h_min,h_max) are along axis."""
    (mn, mx) = face_bbox
    if abs(axis[2]) > 0.5:
        return (mn[0], mn[1], mx[0], mx[1], mn[2], mx[2])
    if abs(axis[1]) > 0.5:
        return (mn[0], mn[2], mx[0], mx[2], mn[1], mx[1])
    return (mn[1], mn[2], mx[1], mx[2], mn[0], mx[0])


def _2d_overlaps(a, b) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _classify_cluster(cluster_faces, faces) -> str:
    """Classify cluster by the *dominant* surface type (by area).

    A small cylindrical hole inside an otherwise planar block does not
    make the block a cylindrical boss — only a cluster whose cylinder
    area is >= the planar area is reported as cylindrical.
    """
    from phone_designer.skills._resolvers import _face_area

    cyl_area = 0.0
    cone_area = 0.0
    planar_area = 0.0
    for i in cluster_faces:
        f = faces[i]
        try:
            a = _face_area(f)
        except Exception:
            continue
        t = _surface_type(f)
        if t == "cylinder":
            cyl_area += a
        elif t == "cone":
            cone_area += a
        elif t == "plane":
            planar_area += a

    if cone_area > 0 and cone_area >= planar_area and cone_area >= cyl_area:
        return "conical"
    if cyl_area > 0 and cyl_area >= planar_area:
        return "cylindrical"
    return "prismatic"


def _boss_metrics(cluster_faces, faces, axis):
    """Return (center, height, diameter_or_size) for a cluster, where
    height is the extent perpendicular to ``axis`` away from the base
    plane (along axis direction), and diameter_or_size is the dominant
    cross-section size in the base plane."""
    cluster_objs = [faces[i] for i in cluster_faces]
    (mn, mx) = _faces_bbox(cluster_objs)
    size = (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])
    center = (
        0.5 * (mn[0] + mx[0]),
        0.5 * (mn[1] + mx[1]),
        0.5 * (mn[2] + mx[2]),
    )

    # axis-direction extent = height
    if abs(axis[2]) > 0.5:
        height = float(size[2])
        cross_extents = (size[0], size[1])
    elif abs(axis[1]) > 0.5:
        height = float(size[1])
        cross_extents = (size[0], size[2])
    else:
        height = float(size[0])
        cross_extents = (size[1], size[2])

    # If the cluster's dominant surface is a cylinder / cone, pick the
    # *largest* such face (by area) as the diameter source — small
    # cylinders inside (e.g. a pin hole through a prismatic lug) should
    # not become the reported boss diameter.
    from phone_designer.skills._resolvers import _face_area

    best_cyl: tuple[float, float] | None = None  # (area, diameter)
    best_cone: tuple[float, float] | None = None
    for f in cluster_objs:
        cyl = _cylinder_info(f)
        if cyl is not None:
            r, _ax = cyl
            try:
                a = _face_area(f)
            except Exception:
                a = 0.0
            if best_cyl is None or a > best_cyl[0]:
                best_cyl = (a, 2.0 * r)
            continue
        cone = _cone_info(f)
        if cone is not None:
            r, _ax = cone
            try:
                a = _face_area(f)
            except Exception:
                a = 0.0
            if best_cone is None or a > best_cone[0]:
                best_cone = (a, 2.0 * r)

    # decide which to report — prefer cone over cylinder over prismatic
    # *only if* the picked surface area is comparable to the prismatic
    # cross-section. cross_extents[*] in mm × height gives the rough
    # planar-side area; if a small cylinder is hugely smaller than the
    # in-plane extent, fall back to prismatic sizing.
    planar_cross_area = min(cross_extents) * height
    if (best_cone is not None
            and best_cone[0] >= 0.5 * planar_cross_area):
        return center, height, float(best_cone[1])
    if (best_cyl is not None
            and best_cyl[0] >= 0.5 * planar_cross_area):
        return center, height, float(best_cyl[1])

    cross = float(min(cross_extents))
    return center, height, cross


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="detect_bosses",
    category="inspect",
    level="atomic",
    summary="Detect boss-like outward protrusions on the body by finding the "
            "dominant base plane and grouping the faces that stand above it. "
            "Each cluster is classified cylindrical / prismatic / conical and "
            "reported with center, height, and cross-section. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["boss_inventory"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class DetectBosses(SkillBase):
    class Args(BaseModel):
        min_height_mm: float = Field(
            default=0.5, gt=0, le=200,
            description="Reject clusters whose extent perpendicular to the "
                        "base plane is shorter than this.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            _all_faces, _face_center,
        )

        shape = _occt_shape(body)
        faces = _all_faces(shape)
        if not faces:
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={"bosses": [], "boss_count": 0},
            )

        # Face-count guard — skip on extremely dense meshes where the
        # O(N) per-base-plane candidate scan + spatial cluster query become
        # too slow to run interactively (e.g. 16k-face imported geometry).
        if len(faces) > MAX_FACES_BOSSES:
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={
                    "bosses": [], "boss_count": 0,
                    "skipped_reason": f"face_count {len(faces)} > {MAX_FACES_BOSSES}",
                },
            )

        # We try every reasonably-large axis-aligned planar face as a base
        # plane candidate. A boss is something that protrudes *outward*
        # from the body interior across that plane.
        candidates = _candidate_base_planes(faces, top_k=6)
        if not candidates:
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={"bosses": [], "boss_count": 0},
            )

        # Cache per-face bbox + projection once — _face_bbox calls into
        # OCCT (BRepBndLib) which is expensive. We need to project a face
        # bbox onto each of up to 6 base-plane axes, but the underlying
        # 3D bbox only needs computing once.
        face_bboxes: list[tuple[tuple[float, float, float],
                                tuple[float, float, float]]] = [
            _face_bbox(f) for f in faces
        ]

        # face_set signature → boss dict (dedupes clusters detected on
        # multiple base planes).
        all_bosses: dict[tuple[int, ...], dict] = {}

        for _area, base_idx, base_axis in candidates:
            bcenter = _face_center(faces[base_idx])
            base_level = (
                bcenter[0] * base_axis[0]
                + bcenter[1] * base_axis[1]
                + bcenter[2] * base_axis[2]
            )

            margin = 0.05  # mm
            candidate_indices: list[int] = []
            candidate_proj: dict[int, tuple[float, float, float, float, float, float]] = {}
            for i in range(len(faces)):
                if i == base_idx:
                    continue
                proj = _project_bbox(face_bboxes[i], base_axis)
                # face is "above" base plane along +axis if its max-along-
                # axis coordinate exceeds the base level by at least margin
                if proj[5] > base_level + margin:
                    candidate_indices.append(i)
                    candidate_proj[i] = proj

            if not candidate_indices:
                continue

            parent = {i: i for i in candidate_indices}

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a, b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

            # Spatial hash on the 2D footprint: O(N) bucketization, then
            # for each face only test overlap against neighbours in the
            # same / adjacent buckets. This replaces an O(N²) all-pairs
            # scan that dominated runtime on dense meshes. For small N the
            # all-pairs path stays since hash overhead exceeds its cost.
            n = len(candidate_indices)
            if n < 200:
                # Small candidate set — fall back to original O(N²) loop.
                for a_i in range(n):
                    ia = candidate_indices[a_i]
                    for b_i in range(a_i + 1, n):
                        ib = candidate_indices[b_i]
                        if _2d_overlaps(candidate_proj[ia], candidate_proj[ib]):
                            union(ia, ib)
            else:
                # Bucket size = mean face extent. Then resize so the
                # *largest* face still fits in a bounded number of buckets
                # (prevents single-bucket fallback that breaks correctness
                # — two huge overlapping faces snapped to different
                # centre-buckets would otherwise miss each other).
                MAX_BUCKETS_PER_FACE = 256
                mean_extent = 0.0
                max_extent = 0.0
                for i in candidate_indices:
                    p = candidate_proj[i]
                    ext = max(p[2] - p[0], p[3] - p[1])
                    mean_extent += ext
                    if ext > max_extent:
                        max_extent = ext
                mean_extent /= n
                cell = max(0.5, mean_extent)
                # Ensure no face spans more than sqrt(MAX_BUCKETS_PER_FACE)
                # buckets in either axis — keeps insert work bounded.
                max_side = int(math.sqrt(MAX_BUCKETS_PER_FACE))
                if max_extent / cell > max_side:
                    cell = max_extent / max_side

                buckets: dict[tuple[int, int], list[int]] = {}
                for i in candidate_indices:
                    p = candidate_proj[i]
                    u0 = int(p[0] // cell)
                    v0 = int(p[1] // cell)
                    u1 = int(p[2] // cell)
                    v1 = int(p[3] // cell)
                    for bu in range(u0, u1 + 1):
                        for bv in range(v0, v1 + 1):
                            buckets.setdefault((bu, bv), []).append(i)

                # For each face, query its buckets; union with overlapping
                # candidates only (no all-pairs). Symmetric (ib > ia)
                # dedup keeps each pair tested at most once.
                for ia in candidate_indices:
                    pa = candidate_proj[ia]
                    u0 = int(pa[0] // cell)
                    v0 = int(pa[1] // cell)
                    u1 = int(pa[2] // cell)
                    v1 = int(pa[3] // cell)
                    seen_neighbors: set[int] = set()
                    for bu in range(u0, u1 + 1):
                        for bv in range(v0, v1 + 1):
                            bucket = buckets.get((bu, bv))
                            if bucket is None:
                                continue
                            for ib in bucket:
                                if ib <= ia or ib in seen_neighbors:
                                    continue
                                seen_neighbors.add(ib)
                                if _2d_overlaps(pa, candidate_proj[ib]):
                                    union(ia, ib)

            clusters: dict[int, list[int]] = {}
            for i in candidate_indices:
                clusters.setdefault(find(i), []).append(i)

            base_proj = _project_bbox(_face_bbox(faces[base_idx]), base_axis)
            base_u = base_proj[2] - base_proj[0]
            base_v = base_proj[3] - base_proj[1]
            base_footprint_area = base_u * base_v

            for cluster in clusters.values():
                if not cluster:
                    continue

                us = [candidate_proj[i][0] for i in cluster]
                vs = [candidate_proj[i][1] for i in cluster]
                uxs = [candidate_proj[i][2] for i in cluster]
                vxs = [candidate_proj[i][3] for i in cluster]
                fu = max(uxs) - min(us)
                fv = max(vxs) - min(vs)
                footprint_area = fu * fv
                if (base_footprint_area > 0
                        and footprint_area > 0.85 * base_footprint_area):
                    continue

                kind = _classify_cluster(cluster, faces)
                center, height, cross = _boss_metrics(
                    cluster, faces, base_axis,
                )
                if height < args.min_height_mm:
                    continue

                sig = tuple(sorted(cluster))
                if sig in all_bosses:
                    # already found via another base candidate — keep the
                    # entry with the larger height (more aligned axis).
                    if all_bosses[sig]["height_mm"] < round(height, 4):
                        all_bosses[sig] = {
                            "type": kind,
                            "center": [round(c, 4) for c in center],
                            "height_mm": round(height, 4),
                            "diameter_or_size_mm": round(cross, 4),
                            "face_indices": list(sig),
                        }
                    continue
                all_bosses[sig] = {
                    "type": kind,
                    "center": [round(c, 4) for c in center],
                    "height_mm": round(height, 4),
                    "diameter_or_size_mm": round(cross, 4),
                    "face_indices": list(sig),
                }

        # Assign IDs in a deterministic order (by first face index)
        ordered = sorted(all_bosses.values(),
                          key=lambda d: d["face_indices"][0])
        bosses: list[dict] = []
        for i, b in enumerate(ordered):
            bosses.append({
                "id": i,
                "type": b["type"],
                "center": b["center"],
                "height_mm": b["height_mm"],
                "diameter_or_size_mm": b["diameter_or_size_mm"],
                "face_indices": b["face_indices"],
            })

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"bosses": bosses, "boss_count": len(bosses)},
        )
