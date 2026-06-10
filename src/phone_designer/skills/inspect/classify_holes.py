"""classify_holes — atomic, read-only.

Detect every cylindrical hole on the body and classify it as:

    simple        — single straight cylinder (blind/through).
    counterbore   — large diameter on top + smaller diameter below joined
                    by a planar annular shoulder.
    countersink   — conical face on top + cylinder below.
    counterdrill  — countersink + counterbore + cylinder (3-stage).
    spotface      — very shallow counterbore (cb depth < 1mm).
    threaded      — non-cylindrical helical surface(s) detected inside an
                    otherwise cylindrical bore. Approximated by checking for
                    bspline / surface-of-revolution faces sharing the cylinder
                    axis.

If ``match_standards=True`` we look the hole up against
``catalogs/standards/threads_metric.yaml`` (and ``threads_imperial.yaml`` when
available) by diameter, returning the closest entry.

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


# ──────────────────────────────────────────────────────────────────────────────
# Catalog loader (inline, per pack rules)


def _load(family, name):
    import yaml, pathlib
    # parents: [0]=inspect, [1]=skills, [2]=phone_designer, [3]=src, [4]=repo root
    root = pathlib.Path(__file__).resolve().parents[4]
    path = root / "catalogs" / family / f"{name}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())


# ──────────────────────────────────────────────────────────────────────────────
# Geometry helpers


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _point_inside_solid(shape, x: float, y: float, z: float, tol: float = 1e-6) -> bool:
    """COMPLEX-CAD pass-7 (2026-06-09): True if (x, y, z) is INSIDE the
    solid body. Uses BRepClass3d_SolidClassifier — same API the OCCT
    documentation lists for the IN/OUT/ON predicate. Used by the catalog
    axis_origin standardiser below.
    """
    try:
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.gp import gp_Pnt
        from OCP.TopAbs import TopAbs_State
        clf = BRepClass3d_SolidClassifier(shape)
        clf.Perform(gp_Pnt(x, y, z), tol)
        return clf.State() == TopAbs_State.TopAbs_IN
    except Exception:
        return False


def _body_entry_along_axis(shape, axis_origin, axis_dir, depth_mm: float):
    """COMPLEX-CAD pass-23 (2026-06-10): given a cylinder's axis_origin
    + axis_dir + depth, compute the body's INNER ENTRY point where the
    cylinder first crosses the body bbox along ±axis_dir.

    Used by the planner to emit a cut at the actual body face instead
    of at a cylinder parametric endpoint that may sit outside the body
    (as1_pe_203 hole 0 has axis_origin at world y=-1016, BELOW body
    bottom at y=-685.8; the cylinder enters the body at y=-685.8).

    Does NOT mutate the catalog's axis_origin — fidelity diff and the
    preserve_brep self-match path keep the natural convention.

    Returns ``(origin, dir, body_relative_depth_mm, intersects)`` —
    ``intersects`` is False when the cylinder segment [0, depth] has
    zero overlap with the body bbox (signals a phantom multi-body
    fastener belonging to another solid in an assembly STEP). In that
    case the first three values fall back to the input unchanged.
    """
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        bb = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, bb)
        if bb.IsVoid():
            return axis_origin, axis_dir, depth_mm, True
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    except Exception:
        return axis_origin, axis_dir, depth_mm, True

    ox, oy, oz = float(axis_origin[0]), float(axis_origin[1]), float(axis_origin[2])
    dx, dy, dz = float(axis_dir[0]), float(axis_dir[1]), float(axis_dir[2])

    # Pass-23d (2026-06-10) tolerance: expand body bbox by a small
    # margin so cylinders sitting RIGHT AT the body boundary survive
    # the in-bbox test after preserve_brep round-trip introduces sub-mm
    # numerical drift. Without this, Crystal_SMD_3225-4Pin regressed
    # 1.0 → 0.85 because 4 of its 8 pin cylinders flipped from "inside"
    # to "outside" by 0.001 mm between orig and regen detection.
    _margin = max(2.0, 0.02 * max(xmax - xmin, ymax - ymin, zmax - zmin))
    xmin -= _margin; ymin -= _margin; zmin -= _margin
    xmax += _margin; ymax += _margin; zmax += _margin

    def _axis_range(coord, dcomp, lo, hi):
        if abs(dcomp) < 1e-9:
            if lo <= coord <= hi:
                return -float("inf"), float("inf")
            return None
        t1 = (lo - coord) / dcomp
        t2 = (hi - coord) / dcomp
        return (min(t1, t2), max(t1, t2))

    rx = _axis_range(ox, dx, xmin, xmax)
    ry = _axis_range(oy, dy, ymin, ymax)
    rz = _axis_range(oz, dz, zmin, zmax)
    if rx is None or ry is None or rz is None:
        return axis_origin, axis_dir, depth_mm, False

    t_lo = max(rx[0], ry[0], rz[0])
    t_hi = min(rx[1], ry[1], rz[1])
    if t_hi <= t_lo:
        return axis_origin, axis_dir, depth_mm, False

    # Confine to the cylinder's actual segment [0, depth_mm].
    t_lo_c = max(t_lo, 0.0)
    t_hi_c = min(t_hi, depth_mm)
    if t_hi_c <= t_lo_c:
        return axis_origin, axis_dir, depth_mm, False

    new_origin = (ox + dx * t_lo_c, oy + dy * t_lo_c, oz + dz * t_lo_c)
    new_depth = t_hi_c - t_lo_c
    return new_origin, axis_dir, new_depth, True


def _rescue_band_anchored_entry(shape, p_lo, p_hi):
    """COMPLEX-CAD pass-24 (2026-06-10): second-chance entry probe for a
    hole the pass-23 phantom filter is about to drop.

    Why it exists: OCCT's analytic ``Cylinder().Location()`` is an
    ARBITRARY point on the axis line. For STEP-imported faces it usually
    coincides with a face end, but for faces created by our own cut
    operations it can sit MID-BAND — Ventilator's regen tap-drill walls
    report z=7.85 while the combined bore (Ø1.6 cut + Ø1.0 remnant)
    spans z=-1.25..10.70. ``_standardize_entry``'s stage-1 flip computes
    "the opposite end" as ``origin ∓ axis_dir·depth`` — garbage when the
    origin is mid-band (z=19.81, 9 mm ABOVE the real bore top) — so the
    standardized entry lands outside the body bbox and the pass-23
    phantom filter then kills a REAL hole. 12 of Ventilator's 13 Ø1 mm
    holes vanished from the regen catalog this way (preserve_brep 0.381).

    Rescue: re-anchor to the bore's TRUE band endpoints (face-bbox
    projections recorded by ``_classify_one`` — radius-independent, so
    Ø0.6 mm and Ø12 mm bores behave identically), pick the endpoint
    nearest a body bbox face as the entry (the same stage-2 heuristic
    ``_standardize_entry`` already uses), point axis_dir at the other
    endpoint, and re-run the bbox-overlap test. True phantoms
    (as1_pe_203's multi-body fastener pins — their actual faces sit
    fully outside the body bbox) still fail the re-test and stay
    dropped exactly as in pass-23.

    Gating: only reached when ``_body_entry_along_axis`` reported no
    intersect, so every hole that survives today is bit-identical.

    Returns ``(origin, axis_dir, entry_origin, entry_depth_mm)`` or
    ``None`` when the bore really has no overlap with the body.
    """
    if not p_lo or not p_hi:
        return None
    try:
        lo = (float(p_lo[0]), float(p_lo[1]), float(p_lo[2]))
        hi = (float(p_hi[0]), float(p_hi[1]), float(p_hi[2]))
    except Exception:
        return None
    span = math.dist(lo, hi)
    if span <= 1e-9:
        return None
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        bb = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, bb)
        if bb.IsVoid():
            return None
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    except Exception:
        return None

    def _dist_to_nearest_face(p) -> float:
        return min(
            abs(p[0] - xmin), abs(p[0] - xmax),
            abs(p[1] - ymin), abs(p[1] - ymax),
            abs(p[2] - zmin), abs(p[2] - zmax),
        )

    if _dist_to_nearest_face(lo) <= _dist_to_nearest_face(hi):
        origin, other = lo, hi
    else:
        origin, other = hi, lo
    dirv = (
        (other[0] - origin[0]) / span,
        (other[1] - origin[1]) / span,
        (other[2] - origin[2]) / span,
    )
    eo, _ed, edepth, intersects = _body_entry_along_axis(
        shape, origin, dirv, span
    )
    if not intersects:
        return None
    return origin, dirv, eo, edepth


def _standardize_entry(shape, axis_origin, axis_dir, depth_mm: float):
    """COMPLEX-CAD pass-17 (2026-06-09): rewrite axis_origin/axis_dir so
    axis_origin sits at the EXIT (open) face of the cylinder — the body
    surface where you would put the drill bit — and axis_dir points
    INTO the body.

    Two-stage probe:
      1. BRepClass3d sample on each side. If exactly one is inside the
         SOLID material, the OPPOSITE end is the entry.
      2. Fallback for through-holes (both ends in void): pick the
         endpoint whose distance to the nearest body bbox face is the
         SMALLEST — that endpoint sits on a body outer surface, which
         is the entry. This catches the convention mismatch where orig
         classify_holes stored the closed cap (inside the body) while
         regen classify_holes stored the open end (on the body surface)
         — different ends of the same cylinder.

    Without stage 2, Ventilator's regen holes paired 0/14 because orig
    had axis_origin at the closed cap (z=6.93) and regen at the open
    end (z=-0.91) — a depth-sized offset the fidelity diff couldn't
    bridge.
    """
    if depth_mm <= 0.0:
        return axis_origin, axis_dir
    ox, oy, oz = float(axis_origin[0]), float(axis_origin[1]), float(axis_origin[2])
    dx, dy, dz = float(axis_dir[0]), float(axis_dir[1]), float(axis_dir[2])
    step = min(0.5, 0.1 * depth_mm)
    in_plus = _point_inside_solid(shape, ox + step * dx, oy + step * dy, oz + step * dz)
    in_minus = _point_inside_solid(shape, ox - step * dx, oy - step * dy, oz - step * dz)
    if in_plus and not in_minus:
        return axis_origin, axis_dir
    if in_minus and not in_plus:
        new_origin = (ox - dx * depth_mm, oy - dy * depth_mm, oz - dz * depth_mm)
        return new_origin, (-dx, -dy, -dz)

    # Stage 2 — bbox-face proximity. Both endpoints are in the void or
    # both on the surface; the ENTRY is the endpoint closer to a bbox
    # face (it sits ON a body outer surface). Compute distances of both
    # endpoints to the nearest face along their dominant axis.
    # (Pass-22 tried to advance outside-bbox endpoints to body face
    # along axis_dir — regressed preserve_brep on as1_pe_203 because
    # the orig catalog's stored axis_origin changed too, breaking the
    # round-trip identity. Reverted.)
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        bb = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, bb)
        if bb.IsVoid():
            return axis_origin, axis_dir
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()

        def _dist_to_nearest_face(x, y, z) -> float:
            return min(
                abs(x - xmin), abs(x - xmax),
                abs(y - ymin), abs(y - ymax),
                abs(z - zmin), abs(z - zmax),
            )

        far_x = ox + dx * depth_mm
        far_y = oy + dy * depth_mm
        far_z = oz + dz * depth_mm
        d_origin = _dist_to_nearest_face(ox, oy, oz)
        d_far = _dist_to_nearest_face(far_x, far_y, far_z)
        if d_origin <= d_far:
            # axis_origin already on (or closer to) a body face → it's the entry.
            return axis_origin, axis_dir
        # Far endpoint is closer to a face → it's the entry; flip.
        return (far_x, far_y, far_z), (-dx, -dy, -dz)
    except Exception:
        return axis_origin, axis_dir


def _surface_kind(face) -> str:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import (
        GeomAbs_BSplineSurface,
        GeomAbs_Cone,
        GeomAbs_Cylinder,
        GeomAbs_Plane,
        GeomAbs_Sphere,
        GeomAbs_SurfaceOfRevolution,
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
    if t == GeomAbs_BSplineSurface:
        return "bspline"
    if t == GeomAbs_SurfaceOfRevolution:
        return "revolution"
    return "other"


def _cylinder_info(face):
    """Analytic cylinder (origin, axis_dir_unit, radius) for a cylindrical face,
    else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cylinder:
        return None
    cyl = surf.Cylinder()
    loc = cyl.Location()
    d = cyl.Axis().Direction()
    dx, dy, dz = d.X(), d.Y(), d.Z()
    mag = math.sqrt(dx * dx + dy * dy + dz * dz)
    if mag > 1e-12:
        dx, dy, dz = dx / mag, dy / mag, dz / mag
    return (
        (loc.X(), loc.Y(), loc.Z()),
        (dx, dy, dz),
        float(cyl.Radius()),
    )


def _cone_info(face):
    """Analytic cone (apex, axis_dir_unit, half_angle_rad, ref_radius) or None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cone

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cone:
        return None
    cone = surf.Cone()
    apex = cone.Apex()
    d = cone.Axis().Direction()
    dx, dy, dz = d.X(), d.Y(), d.Z()
    mag = math.sqrt(dx * dx + dy * dy + dz * dz)
    if mag > 1e-12:
        dx, dy, dz = dx / mag, dy / mag, dz / mag
    return (
        (apex.X(), apex.Y(), apex.Z()),
        (dx, dy, dz),
        float(cone.SemiAngle()),
        float(cone.RefRadius()),
    )


def _face_bbox(face):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(face, bb)
    except Exception:
        try:
            BRepBndLib.Add_s(face, bb)
        except Exception:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    if bb.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    return tuple(bb.Get()[:3]), tuple(bb.Get()[3:])


def _axes_collinear(
    o1, d1, o2, d2,
    angle_tol_deg: float = 5.0,
    line_tol_mm: float = 0.2,
) -> bool:
    """Two axes share the same line (within tolerance)?"""
    dot = d1[0] * d2[0] + d1[1] * d2[1] + d1[2] * d2[2]
    if abs(abs(dot) - 1.0) > math.sin(math.radians(angle_tol_deg)):
        return False
    # Perpendicular distance from o2 to line(o1, d1)
    vx = o2[0] - o1[0]
    vy = o2[1] - o1[1]
    vz = o2[2] - o1[2]
    # cross(v, d1)
    cx = vy * d1[2] - vz * d1[1]
    cy = vz * d1[0] - vx * d1[2]
    cz = vx * d1[1] - vy * d1[0]
    dist = math.sqrt(cx * cx + cy * cy + cz * cz)
    return dist <= line_tol_mm


def _project_along_axis(point, origin, axis) -> float:
    return (
        (point[0] - origin[0]) * axis[0]
        + (point[1] - origin[1]) * axis[1]
        + (point[2] - origin[2]) * axis[2]
    )


def _planar_face_axis_distance(face, axis_origin, axis_dir) -> float | None:
    """If face is a plane whose normal is parallel to axis_dir, return its
    axis projection; else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Plane:
        return None
    pln = surf.Plane()
    n = pln.Axis().Direction()
    nx, ny, nz = n.X(), n.Y(), n.Z()
    dot = nx * axis_dir[0] + ny * axis_dir[1] + nz * axis_dir[2]
    if abs(abs(dot) - 1.0) > 0.05:  # ~18° tol — generous
        return None
    loc = pln.Location()
    return _project_along_axis((loc.X(), loc.Y(), loc.Z()), axis_origin, axis_dir)


def _face_axis_distance_to_line(face, axis_origin, axis_dir) -> float:
    """Min distance from face bbox center to the axis line."""
    bb_min, bb_max = _face_bbox(face)
    cx = 0.5 * (bb_min[0] + bb_max[0])
    cy = 0.5 * (bb_min[1] + bb_max[1])
    cz = 0.5 * (bb_min[2] + bb_max[2])
    vx, vy, vz = cx - axis_origin[0], cy - axis_origin[1], cz - axis_origin[2]
    cx_ = vy * axis_dir[2] - vz * axis_dir[1]
    cy_ = vz * axis_dir[0] - vx * axis_dir[2]
    cz_ = vx * axis_dir[1] - vy * axis_dir[0]
    return math.sqrt(cx_ * cx_ + cy_ * cy_ + cz_ * cz_)


# ──────────────────────────────────────────────────────────────────────────────
# Hole grouping


def _group_cylinders_by_axis(cyl_records):
    """cyl_records: list of (face_index, origin, axis_dir, radius). Group those
    whose axes are collinear into shared bores."""
    groups: list[list[int]] = []
    group_axes: list[tuple] = []  # (origin, axis_dir) per group
    for idx, rec in enumerate(cyl_records):
        fi, o, d, r = rec
        placed = False
        for gi, (go, gd) in enumerate(group_axes):
            if _axes_collinear(go, gd, o, d):
                groups[gi].append(idx)
                placed = True
                break
        if not placed:
            groups.append([idx])
            group_axes.append((o, d))
    return groups, group_axes


# ──────────────────────────────────────────────────────────────────────────────
# Standard match


def _best_standard_match(
    primary_d_mm: float,
    cb_d_mm: float | None,
    cs_d_mm: float | None,
):
    """Return (thread_spec, fit, confidence) for the closest standard, or None.

    Strategy:
      - Iterate every (spec, fit_key) in threads_metric.yaml.
      - Distance = |primary_d - clearance_d|. Lower is better.
      - Confidence ~ 1 / (1 + distance_per_mm).
      - If cb_d_mm given, prefer specs whose counterbore_d matches.
      - If cs_d_mm given, prefer specs whose countersink_d matches.
    """
    data = _load("standards", "threads_metric")
    if data is None:
        return None
    threads = data.get("threads", {})

    best = None
    best_score = float("inf")
    best_fit = "medium"
    for spec, entry in threads.items():
        for fit_key, fit_label in (
            ("clearance_close_mm", "close"),
            ("clearance_medium_mm", "medium"),
            ("clearance_coarse_mm", "coarse"),
            ("tap_drill_mm", "tap"),
            ("outer_d_mm", "outer"),
        ):
            d = entry.get(fit_key)
            if d is None:
                continue
            score = abs(primary_d_mm - float(d))
            if cb_d_mm is not None and "counterbore_d_iso_mm" in entry:
                score += 0.5 * abs(cb_d_mm - float(entry["counterbore_d_iso_mm"]))
            if cs_d_mm is not None and "countersink_d_iso10642_mm" in entry:
                score += 0.5 * abs(cs_d_mm - float(entry["countersink_d_iso10642_mm"]))
            if score < best_score:
                best_score = score
                best = spec
                best_fit = fit_label

    if best is None:
        return None
    # confidence: 1mm slop → ~0.5; 0.05mm → ~0.95
    confidence = round(1.0 / (1.0 + best_score), 3)
    # Imperial catalog optional
    imp = _load("standards", "threads_imperial")
    if imp is not None:
        for spec, entry in imp.get("threads", {}).items():
            d = entry.get("outer_d_mm") or entry.get("clearance_medium_mm")
            if d is None:
                continue
            score = abs(primary_d_mm - float(d))
            if score < best_score:
                best_score = score
                best = spec
                best_fit = "imperial"
                confidence = round(1.0 / (1.0 + best_score), 3)

    return {"thread_spec": best, "fit": best_fit, "confidence": confidence}


# ──────────────────────────────────────────────────────────────────────────────
# Classification of a single bore group


def _classify_one(
    group_face_indices: list[int],
    cyl_records,
    cone_records,
    other_face_records,
    faces,
    axis_origin,
    axis_dir,
    *,
    match_standards: bool,
):
    """group_face_indices indexes into ``cyl_records`` for cyl members.

    cone_records: [(face_index, apex, axis_dir, half_angle, ref_r), ...]
    other_face_records: [(face_index, surface_kind), ...] for bspline/revolution
        on the same axis (threaded indicator).
    """
    # Gather the cylinders in this group
    cyls = [cyl_records[i] for i in group_face_indices]
    # Per-cylinder: (axis_proj_low, axis_proj_high, radius, face_index)
    bands: list[tuple[float, float, float, int]] = []
    cyl_face_indices: list[int] = []
    for (fi, o, d, r) in cyls:
        bb_min, bb_max = _face_bbox(faces[fi])
        p1 = _project_along_axis(bb_min, axis_origin, axis_dir)
        p2 = _project_along_axis(bb_max, axis_origin, axis_dir)
        lo, hi = min(p1, p2), max(p1, p2)
        bands.append((lo, hi, r, fi))
        cyl_face_indices.append(fi)

    # Find conical faces whose axis matches this bore
    cones_on_axis: list[tuple[float, float, float, float, int]] = []
    # (axis_proj_low, axis_proj_high, r_at_low, r_at_high, face_index)
    for (fi, apex, cdir, half_angle, ref_r) in cone_records:
        # axis alignment?
        if not _axes_collinear(axis_origin, axis_dir, apex, cdir, angle_tol_deg=8.0,
                                line_tol_mm=0.5):
            continue
        bb_min, bb_max = _face_bbox(faces[fi])
        p1 = _project_along_axis(bb_min, axis_origin, axis_dir)
        p2 = _project_along_axis(bb_max, axis_origin, axis_dir)
        lo, hi = min(p1, p2), max(p1, p2)
        # apex projection
        apex_p = _project_along_axis(apex, axis_origin, axis_dir)
        # radius at lo / hi: r = |proj - apex_p| * |tan(half_angle)|.
        # OCCT semi-angle can be signed by axis convention; we want absolute
        # radius from the axis line.
        ta = abs(math.tan(half_angle))
        r_lo = abs(lo - apex_p) * ta
        r_hi = abs(hi - apex_p) * ta
        cones_on_axis.append((lo, hi, r_lo, r_hi, fi))

    # Helical / non-cylindrical surfaces on axis (threaded indicator)
    threaded_face_indices: list[int] = []
    for (fi, kind) in other_face_records:
        if kind in ("bspline", "revolution", "torus"):
            # check approximate distance to axis line is bounded
            d2axis = _face_axis_distance_to_line(faces[fi], axis_origin, axis_dir)
            # accept if face center is within some radius of axis (bound by max cylinder r * 2)
            max_r = max((b[2] for b in bands), default=0.0) * 2.5 + 1.0
            if d2axis <= max_r:
                threaded_face_indices.append(fi)

    # Sort bands along axis (top → bottom): higher proj = top (axis points out)
    bands.sort(key=lambda t: t[0])
    # depth: total span
    proj_lo = min(b[0] for b in bands)
    proj_hi = max(b[1] for b in bands)
    if cones_on_axis:
        proj_lo = min(proj_lo, min(c[0] for c in cones_on_axis))
        proj_hi = max(proj_hi, max(c[1] for c in cones_on_axis))
    depth = abs(proj_hi - proj_lo)
    # COMPLEX-CAD pass-8 (2026-06-09): when the cylinder bands span 0 mm
    # along axis_dir (degenerate face, super-short cylinder, or tilted
    # axis projection collapsing), fall back to a geometry-derived
    # estimate so the catalog never emits depth=0. The planner's
    # downstream `or 5.0` synthetic default exists as a last-line guard;
    # this fix prevents that path from firing for the 74 holes on
    # pythonocc__11752 that previously had depth=0 in the catalog.
    if depth <= 1e-9 and bands:
        # Use 2× the largest cylinder radius as a body-relative scale —
        # any hole worth detecting is at least one diameter deep.
        depth = 2.0 * max(b[2] for b in bands)

    # Unique radii: report MAJOR (largest) first, then descending — matches
    # the human convention "Ø8 counterbore / Ø4.5 clearance / ..." spoken
    # mouth-then-shaft on a drawing.
    diameters_sorted_top_to_bottom: list[float] = []
    seen: set[float] = set()
    for (_, _, r, _) in sorted(bands, key=lambda t: -t[2]):
        key = round(r, 3)
        if key in seen:
            continue
        seen.add(key)
        diameters_sorted_top_to_bottom.append(round(2.0 * r, 4))

    # Detect counterbore: ≥2 distinct radii AND a planar annular face between them
    has_cone = len(cones_on_axis) > 0
    distinct_radii = len({round(r, 3) for (_, _, r, _) in bands})

    # Counterbore depth = the depth of the largest-diameter (top) cylinder
    cb_depth_mm = None
    if distinct_radii >= 2 and bands:
        top_band = max(bands, key=lambda t: t[2])  # largest radius
        cb_depth_mm = abs(top_band[1] - top_band[0])

    # Classify
    if threaded_face_indices:
        ptype = "threaded"
    elif has_cone and distinct_radii >= 2:
        # Countersink (cone) + counterbore (≥2 cylinders w/ shoulder) + base shaft
        ptype = "counterdrill"
    elif has_cone:
        ptype = "countersink"
    elif distinct_radii >= 2:
        # spotface if the top (large) cylinder is very shallow
        if cb_depth_mm is not None and cb_depth_mm < 1.0:
            ptype = "spotface"
        else:
            ptype = "counterbore"
    else:
        ptype = "simple"

    # Primary (clearance shaft) diameter is the SMALLEST cylinder diameter —
    # that's the through-shank for fastener clearance.
    primary_d = round(2.0 * min(b[2] for b in bands), 4) if bands else 0.0
    cb_d = round(2.0 * max(b[2] for b in bands), 4) if (
        distinct_radii >= 2 and bands
    ) else None
    cs_d = None
    if cones_on_axis:
        # max conical radius (mouth diameter of the countersink)
        cs_d = round(2.0 * max(max(c[2], c[3]) for c in cones_on_axis), 4)

    descriptor: dict[str, Any] = {
        "type": ptype,
        "axis_origin": [round(v, 4) for v in axis_origin],
        "axis_dir": [round(v, 4) for v in axis_dir],
        "diameters_mm": diameters_sorted_top_to_bottom,
        "depth_mm": round(depth, 4),
        "face_indices": sorted(
            set(cyl_face_indices)
            | {c[4] for c in cones_on_axis}
            | set(threaded_face_indices)
        ),
    }
    # COMPLEX-CAD pass-24 (2026-06-10): stash the bore's TRUE band
    # endpoints on the (pre-standardization) group axis. proj_lo/proj_hi
    # come from the member faces' bboxes, so these points bound the
    # actual face geometry even when the analytic cylinder location
    # (axis_origin above) sits mid-band — which is exactly the case
    # _standardize_entry mishandles on regen tap-drill cuts. Private
    # keys (leading underscore) consumed by _rescue_band_anchored_entry
    # in ClassifyHoles._apply; never copied into the emitted hole dict.
    descriptor["_band_lo_point"] = [
        axis_origin[0] + axis_dir[0] * proj_lo,
        axis_origin[1] + axis_dir[1] * proj_lo,
        axis_origin[2] + axis_dir[2] * proj_lo,
    ]
    descriptor["_band_hi_point"] = [
        axis_origin[0] + axis_dir[0] * proj_hi,
        axis_origin[1] + axis_dir[1] * proj_hi,
        axis_origin[2] + axis_dir[2] * proj_hi,
    ]
    # COMPLEX-CAD pass-24b (2026-06-10): the rescue is ONLY legitimate
    # when the analytic cylinder location actually sat MID-BAND (the
    # regen-cut pathology that makes _standardize_entry's flip compute
    # garbage). The analytic location projects to 0.0 by construction,
    # so mid-band ⇔ 0 strictly inside [proj_lo, proj_hi] AND far from
    # both endpoints. Without this gate the rescue also resurrected
    # endpoint-anchored SMD pin cylinders that pass-23's phantom filter
    # had CORRECTLY dropped — V2 harness caught 4 regressions (SOT-223 /
    # SOIC-8 0.9167→0.8462, USB_A 0.9146→0.8444, USB_C −0.014).
    _span = proj_hi - proj_lo
    _end_dist = min(abs(0.0 - proj_lo), abs(proj_hi - 0.0))
    descriptor["_origin_mid_band"] = bool(
        proj_lo < 0.0 < proj_hi
        and _end_dist > max(0.1 * _span, 0.5)
    )

    if match_standards:
        descriptor["standard_match"] = _best_standard_match(primary_d, cb_d, cs_d)
    else:
        descriptor["standard_match"] = None

    return descriptor


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="classify_holes",
    category="inspect",
    level="atomic",
    summary="Detect and classify every cylindrical hole on the body — "
            "simple, counterbore, countersink, counterdrill, spotface, or "
            "threaded. Optionally match each against the metric/imperial thread "
            "catalogs and report the closest spec by diameter. Read-only — body "
            "unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["hole_inventory"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="body_present")],
)
class ClassifyHoles(SkillBase):
    class Args(BaseModel):
        match_standards: bool = Field(
            default=True,
            description="If True, look the hole's primary diameter up against "
                        "catalogs/standards/threads_metric.yaml (and "
                        "threads_imperial.yaml when present) and attach the "
                        "closest standard spec to each descriptor.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        shape = _occt_shape(body)
        faces = _all_faces(shape)

        if not faces:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"holes": []},
            )

        cyl_records: list[tuple[int, tuple, tuple, float]] = []
        cone_records: list[tuple[int, tuple, tuple, float, float]] = []
        other_records: list[tuple[int, str]] = []

        for i, f in enumerate(faces):
            kind = _surface_kind(f)
            if kind == "cylinder":
                info = _cylinder_info(f)
                if info is not None:
                    o, d, r = info
                    cyl_records.append((i, o, d, r))
            elif kind == "cone":
                info = _cone_info(f)
                if info is not None:
                    apex, d, ha, rr = info
                    cone_records.append((i, apex, d, ha, rr))
            elif kind in ("bspline", "revolution", "torus"):
                other_records.append((i, kind))

        if not cyl_records:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"holes": []},
            )

        groups, group_axes = _group_cylinders_by_axis(cyl_records)

        holes: list[dict[str, Any]] = []
        for gi, group in enumerate(groups):
            o, d = group_axes[gi]
            desc = _classify_one(
                group, cyl_records, cone_records, other_records, faces,
                o, d,
                match_standards=args.match_standards,
            )
            # COMPLEX-CAD pass-7 (2026-06-09): standardise axis_origin so
            # downstream RE always sees the entry point + the inward
            # axis_dir. Different cylinder detectors place axis_origin
            # at inconsistent ends (Ventilator stores entry, as1_pe_203
            # stores deep cap) which made the planner's entry-Z override
            # work for one and break the other. This probe picks the
            # entry endpoint by testing which side is INSIDE the solid.
            try:
                new_o, new_d = _standardize_entry(
                    shape, desc["axis_origin"], desc["axis_dir"], desc["depth_mm"]
                )
                desc["axis_origin"] = [round(float(v), 4) for v in new_o]
                desc["axis_dir"] = [round(float(v), 4) for v in new_d]
            except Exception:
                pass
            # COMPLEX-CAD pass-23 (2026-06-10): compute the body-relative
            # entry point + depth. This is the CUT-WORTHY position even
            # for cylinders whose parametric axis_origin sits outside the
            # body (poked-through through-holes). Stored as a separate
            # field so axis_origin keeps its natural convention for the
            # preserve_brep self-match path; the planner picks
            # entry_origin / entry_depth_mm when emitting box-mode cuts.
            #
            # ALSO drop phantom multi-body fastener cylinders: when the
            # cylinder segment [0, depth_mm] has zero overlap with the
            # body bbox, the face belongs to ANOTHER solid in the
            # assembly (as1_pe_203 has 7 such cylinders — long fastener
            # pins at y=-1016..-2032 below body bottom y=-685.8). They
            # waste planner emission AND pollute the fidelity-diff union
            # denominator. Both orig and regen catalogues apply the same
            # filter — preserve_brep self-match stays 1.0 by construction.
            try:
                eo, ed, edepth, intersects = _body_entry_along_axis(
                    shape,
                    desc["axis_origin"], desc["axis_dir"], desc["depth_mm"],
                )
                if not intersects:
                    # COMPLEX-CAD pass-24 (2026-06-10): before dropping,
                    # re-test against the bore's true band endpoints —
                    # the standardized axis_origin is garbage when the
                    # analytic cylinder location sat mid-band (see
                    # _rescue_band_anchored_entry). Real small bores
                    # survive with a band-anchored entry; true phantom
                    # multi-body fasteners still fail the re-test and
                    # drop exactly as in pass-23.
                    # pass-24b gate: ONLY when the analytic location
                    # actually sat mid-band. Endpoint-anchored cylinders
                    # (normal STEP faces, SMD pins) keep the pass-23
                    # verdict — rescuing them resurrected pins
                    # asymmetrically and regressed 4 SMD files.
                    if not desc.get("_origin_mid_band"):
                        continue
                    rescue = _rescue_band_anchored_entry(
                        shape,
                        desc.get("_band_lo_point"),
                        desc.get("_band_hi_point"),
                    )
                    if rescue is None:
                        continue
                    r_origin, r_dir, eo, edepth = rescue
                    desc["axis_origin"] = [
                        round(float(v), 4) for v in r_origin
                    ]
                    desc["axis_dir"] = [round(float(v), 4) for v in r_dir]
                desc["entry_origin"] = [round(float(v), 4) for v in eo]
                desc["entry_depth_mm"] = round(float(edepth), 4)
            except Exception:
                desc["entry_origin"] = desc["axis_origin"]
                desc["entry_depth_mm"] = desc["depth_mm"]
            desc["id"] = len(holes)
            holes.append({
                "id": desc["id"],
                "type": desc["type"],
                "axis_origin": desc["axis_origin"],
                "axis_dir": desc["axis_dir"],
                "entry_origin": desc["entry_origin"],
                "entry_depth_mm": desc["entry_depth_mm"],
                "diameters_mm": desc["diameters_mm"],
                "depth_mm": desc["depth_mm"],
                "face_indices": desc["face_indices"],
                "standard_match": desc["standard_match"],
            })

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"holes": holes},
        )
