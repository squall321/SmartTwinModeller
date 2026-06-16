"""extract_feature_catalog — atomic, read-only.

Run every detector / classifier in sequence on the current body and merge
their outputs into a single ``extras["feature_catalog"]`` dict::

    {
      "holes":             [...],   # classify_holes
      "pockets":           [...],   # classify_pockets
      "bosses":            [...],   # detect_bosses
      "ribs":              [...],   # detect_ribs
      "lugs":              [...],   # detect_lugs
      "symmetries":        [...],   # detect_mirror_symmetry
      "patterns":          [...],   # detect_*_array (linear + circular)
      "standard_matches":  [...],   # match_standard_hole on every hole
      "sweep_features":    [...],   # surface-of-extrusion / b-spline sweep tubes
      "loft_features":     [...],   # cone / lofted-bspline boss/pocket pairs
      "revolve_features":  [...],   # revolved bspline pockets / annular grooves
      "edge_blends":       [...],   # classify_edge_blends (fillets / chamfers)
      "base_thickness_mm": float,   # dominant slab thickness for base-box step
      "_timings_sec":      {...},   # per-detector wall-clock in seconds
      "_skipped_detectors":[...],   # detectors that hit their 'too_big' guard
                                    # (only present when at least one was skipped)
    }

Body is unchanged (post body_present). Catches individual detector failures
so a single broken detector cannot poison the whole catalog.
"""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _safe(fn, *args, **kwargs):
    """Run a detector — swallow its exception and return ``[]``-like default.

    Each detector is a "best effort" inspector; if one fails we still want
    the rest of the catalog to be populated.
    """
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Sweep / revolve / loft detector
#
# These features leave characteristic surface-type fingerprints on the body:
#
#   sweep   — BRepOffsetAPI_MakePipe produces ``GeomAbs_SurfaceOfExtrusion``
#             (straight segments) and BSpline surfaces (curved segments).
#   loft    — BRepOffsetAPI_ThruSections between two non-coincident wires
#             produces conical faces (circle→circle of different radii) or
#             BSpline patch surfaces (general profiles).
#   revolve — BRepPrimAPI_MakeRevol of a non-trivial profile gives BSpline
#             surfaces of revolution.
#
# We do *not* try to recover the exact path / sketch / axis from B-rep; that
# is an over-constrained inverse problem in v0. Instead we capture:
#   - feature bbox  → used as a plausible reconstruction envelope.
#   - centroid path → sampled along the surface bbox diagonal for sweeps.
#   - cone radii    → top/bottom diameters for loft frustums.
# Downstream the planner emits a *placeholder* sweep/loft/revolve step using
# these parameters so reconstruction volume tracks the original within ~30%.


def _occt_shape(body):
    return body.wrapped if hasattr(body, "wrapped") else body


def _face_centroid(face):
    """(cx, cy, cz) — area-weighted face centroid (falls back to bbox center)."""
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        c = props.CentreOfMass()
        return (float(c.X()), float(c.Y()), float(c.Z()))
    except Exception:
        try:
            from OCP.Bnd import Bnd_Box
            from OCP.BRepBndLib import BRepBndLib
            bb = Bnd_Box()
            BRepBndLib.AddOptimal_s(face, bb)
            xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
            return (0.5 * (xmin + xmax), 0.5 * (ymin + ymax),
                    0.5 * (zmin + zmax))
        except Exception:
            return (0.0, 0.0, 0.0)


def _face_bbox_local(face):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(face, bb)
    except Exception:
        BRepBndLib.Add_s(face, bb)
    if bb.IsVoid():
        return None
    return bb.Get()  # (xmin,ymin,zmin,xmax,ymax,zmax)


def _surface_kind_int(face) -> int:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    return int(BRepAdaptor_Surface(face).GetType())


def _boss_face_indices_in_pocket(boss: dict, pockets: list) -> bool:
    """COMPLEX-CAD fix (2026-06-08): True if the boss shares ANY face_index
    with any pocket. Both detectors index into the same _all_faces(shape)
    list so indices are directly comparable. Catches the common case
    where a cylindrical pocket wall is also tagged as a cylindrical-boss
    cluster — on as1_pe_203 this removes 3 phantom bosses.
    """
    bf = set(boss.get("face_indices") or [])
    if not bf:
        return False
    for p in pockets or []:
        if not isinstance(p, dict):
            continue
        pf = p.get("face_indices") or []
        if bf.intersection(pf):
            return True
    return False


def _boss_xy_in_any_pocket(boss: dict, pockets: list) -> bool:
    """COMPLEX-CAD fix (2026-06-08): True if the boss centre XY lies inside
    any pocket's circular footprint (axis_origin XY + top_d_mm/2 radius).
    Mirrors _cone_in_any_hole so cylindrical residue inside a pocket cap
    is filtered even when face indexing diverges between detector runs.
    """
    c = boss.get("center") or [0.0, 0.0, 0.0]
    try:
        bx, by = float(c[0]), float(c[1])
    except Exception:
        return False
    for p in pockets or []:
        if not isinstance(p, dict):
            continue
        origin = p.get("axis_origin") or [0.0, 0.0, 0.0]
        td = float(p.get("top_d_mm") or 0.0)
        if td <= 0.0:
            continue
        try:
            ox, oy = float(origin[0]), float(origin[1])
        except Exception:
            continue
        r = 0.5 * td + 0.5
        dx, dy = bx - ox, by - oy
        if (dx * dx + dy * dy) ** 0.5 <= r:
            return True
    return False


def _cone_in_any_hole(cx: float, cy: float, holes: list) -> bool:
    """COMPLEX-CAD fix (2026-06-07): True if a cone's XY centroid lies inside
    the radius of any detected hole. Used to suppress countersink cones from
    being mis-labelled as loft bosses.
    """
    for h in holes or []:
        if not isinstance(h, dict):
            continue
        origin = h.get("axis_origin") or [0.0, 0.0, 0.0]
        diams = h.get("diameters_mm") or []
        try:
            ox = float(origin[0]); oy = float(origin[1])
            r = (float(max(diams)) * 0.5) if diams else float(h.get("diameter_mm") or 0.0) * 0.5
        except Exception:
            continue
        if r <= 0.0:
            continue
        dx = cx - ox; dy = cy - oy
        if (dx * dx + dy * dy) ** 0.5 <= r + 0.5:  # 0.5 mm slop
            return True
    return False


def _loop_span2d(sketch: dict) -> tuple[float, float] | None:
    """Max 2D extent (span_u, span_v) of an executable loop sketch dict.

    Handles the three loop kinds ``recover_section_loop`` emits (polygon /
    bspline_closed / composite). Used to gate a recovered loft cross-section
    against the loft's circle-diameter estimate so the body's whole-slab
    outline (a far larger loop) is rejected and the feature stays on its
    circle fallback (byte-identity preserved).
    """
    pts: list[tuple[float, float]] = []
    kind = sketch.get("kind")
    if kind == "polygon":
        pts = [tuple(p) for p in sketch.get("vertices", [])]
    elif kind == "bspline_closed":
        pts = [tuple(p) for p in sketch.get("fit_points", [])]
    elif kind == "composite":
        for seg in sketch.get("segments", []):
            if "start" in seg and "end" in seg:
                pts.append(tuple(seg["start"]))
                pts.append(tuple(seg["end"]))
            elif "points" in seg:
                pts.extend(tuple(p) for p in seg["points"])
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (max(xs) - min(xs), max(ys) - min(ys))


def _recover_loft_profile(shape, cx, cy, z, diam_estimate):
    """PILLAR FREEFORM (phase-2, 2026-06-14): recover ONE real loft cross-section.

    Cut the body at world plane ``(cx, cy, z)`` with +Z normal via the shared
    ``_section_curve.recover_section_loop`` so the returned loop's 2D coords are
    RELATIVE to the loft centre ``(cx, cy)`` (``to2d`` subtracts the plane
    origin). The loft emitter then re-centres it with the SAME ``center_xy`` it
    already passes for the circle estimate.

    Returns the executable sketch dict ONLY when the recovered loop's max 2D
    span is within ±50 % of the loft's circle-diameter estimate — this rejects
    the body's whole-slab outline (a much larger loop that the global
    largest-by-area picker would otherwise return). On any miss / skip / span
    mismatch / failure it returns ``None`` so the feature carries NO profile key
    and the emitter uses the circle EXACTLY as today (catalog byte-identical).
    """
    try:
        from phone_designer.skills.inspect._section_curve import (
            recover_section_loop,
        )

        rec = recover_section_loop(shape, (cx, cy, z), (0.0, 0.0, 1.0))
        if rec.get("skipped") or rec.get("empty"):
            return None
        sketch = rec.get("sketch")
        if not sketch:
            return None
        span = _loop_span2d(sketch)
        if span is None:
            return None
        max_span = max(span)
        d = float(diam_estimate or 0.0)
        if d <= 1e-6 or max_span <= 1e-6:
            return None
        # Span-vs-diameter gate: a true loft profile section is comparable to
        # the cone's bbox-extent diameter estimate; the slab outline is far
        # larger and falls outside the band → reject (circle fallback kept).
        if abs(max_span - d) / d > 0.5:
            return None
        return sketch
    except Exception:
        return None


def _extrusion_direction(extrusion_faces) -> tuple[float, float, float] | None:
    """PILLAR FREEFORM (phase-4, 2026-06-15, ITEM 2): area-weighted mean of the
    ``GeomAbs_SurfaceOfExtrusion`` BasisCurve directions.

    Each surface-of-extrusion face is one straight LEG of the swept tube; its
    ``BRepAdaptor_Surface.Direction()`` is the extrusion (sweep) direction of
    that leg. For a straight (single-leg) sweep all legs share one direction; we
    average them (area-weighted) and re-unit so a slightly tilted leg does not
    dominate. Returns the unit direction, or ``None`` when no face yields a
    usable direction (caller keeps the fabricated bbox-diagonal path).
    """
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
    except Exception:
        return None
    sx = sy = sz = 0.0
    wsum = 0.0
    for _fi, f in extrusion_faces:
        try:
            d = BRepAdaptor_Surface(f).Direction()
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(f, props)
            w = float(props.Mass())
        except Exception:
            continue
        if w <= 0.0:
            continue
        dx, dy, dz = float(d.X()), float(d.Y()), float(d.Z())
        # Flip to a consistent hemisphere (dominant component positive) so
        # opposing-wall legs do not cancel.
        comps = (abs(dx), abs(dy), abs(dz))
        k = comps.index(max(comps))
        ref = (dx, dy, dz)[k]
        if ref < 0.0:
            dx, dy, dz = -dx, -dy, -dz
        sx += dx * w
        sy += dy * w
        sz += dz * w
        wsum += w
    if wsum <= 0.0:
        return None
    mx, my, mz = sx / wsum, sy / wsum, sz / wsum
    mag = (mx * mx + my * my + mz * mz) ** 0.5
    if mag < 1e-9:
        return None
    return (mx / mag, my / mag, mz / mag)


def _recover_sweep_path_and_profile(shape, extrusion_faces, all_bb):
    """PILLAR FREEFORM (phase-4, 2026-06-15, ITEM 2): recover a REAL straight
    sweep path + one real perpendicular profile section.

    Replaces the fabricated 3-point bbox-diagonal path with honest recovery:

      * extrusion direction = area-weighted mean of the surface-of-extrusion
        BasisCurve directions (``_extrusion_direction``);
      * path = the two endpoints of the merged-bbox span PROJECTED onto that
        direction through the bbox centre (a straight 2-point polyline — the
        surface-of-extrusion fingerprint is by construction a STRAIGHT sweep);
      * profile = one real perpendicular ``recover_section_loop`` taken at the
        mid-span plane (normal = extrusion direction).

    Returns ``(path_points, profile_sketch, profile_diameter_mm)`` on success,
    where ``profile_sketch`` is an executable sketch dict (or ``None`` when no
    consistent section was recovered — the emitter then falls back to the
    circle). Returns ``None`` entirely when even the path can't be recovered, so
    the caller keeps the historic fabricated path (byte-identical fallback).
    """
    try:
        direction = _extrusion_direction(extrusion_faces)
        if direction is None:
            return None
        xmin, ymin, zmin, xmax, ymax, zmax = all_bb
        cx = 0.5 * (xmin + xmax)
        cy = 0.5 * (ymin + ymax)
        cz = 0.5 * (zmin + zmax)
        dx, dy, dz = direction
        # Project each of the 8 bbox corners onto the direction line through the
        # centre; the min/max projections bracket the sweep extent.
        tmin = tmax = None
        for px in (xmin, xmax):
            for py in (ymin, ymax):
                for pz in (zmin, zmax):
                    t = (px - cx) * dx + (py - cy) * dy + (pz - cz) * dz
                    tmin = t if tmin is None else min(tmin, t)
                    tmax = t if tmax is None else max(tmax, t)
        if tmin is None or (tmax - tmin) < 1e-6:
            return None
        # Anchor the start at the END of the span nearer the base plane
        # (anchor_z) so a +Z-ish sweep still starts low, matching the historic
        # fabricated path's intent.
        p_lo = (cx + dx * tmin, cy + dy * tmin, cz + dz * tmin)
        p_hi = (cx + dx * tmax, cy + dy * tmax, cz + dz * tmax)
        start, end = (p_lo, p_hi) if p_lo[2] <= p_hi[2] else (p_hi, p_lo)
        path_points = [
            [round(start[0], 4), round(start[1], 4), round(start[2], 4)],
            [round(end[0], 4), round(end[1], 4), round(end[2], 4)],
        ]

        # Profile: one real perpendicular section at the mid-span plane.
        profile_sketch = None
        profile_d = None
        try:
            from phone_designer.skills.inspect._section_curve import (
                recover_section_loop,
            )
            rec = recover_section_loop(shape, (cx, cy, cz), direction)
            if not (rec.get("skipped") or rec.get("empty")):
                sk = rec.get("sketch")
                area = float(rec.get("area_mm2") or 0.0)
                if sk and area > 1e-6:
                    # equivalent-circle diameter from the section area, used as
                    # the circle fallback's diameter when the sketch is dropped.
                    profile_d = 2.0 * (area / 3.141592653589793) ** 0.5
                    # BRepCheck.IsValid gate (ITEM 2): build the swept solid from
                    # the recovered profile + path and KEEP the profile_sketch
                    # ONLY when the resulting solid is valid. An invalid sweep
                    # (self-intersection, non-manifold) drops the profile so the
                    # emitter falls back to the circle (byte-identical).
                    if _swept_profile_is_valid(sk, path_points):
                        profile_sketch = sk
        except Exception:
            profile_sketch = None
        return (path_points, profile_sketch, profile_d)
    except Exception:
        return None


def _swept_profile_is_valid(sketch, path_points) -> bool:
    """PILLAR FREEFORM (phase-4, 2026-06-15, ITEM 2): build the swept solid from
    a recovered ``sketch`` + ``path_points`` and return ``BRepCheck.IsValid``.

    Mirrors ``swept_boss_along_curve``'s build path (planar profile face →
    orient +Z to the initial tangent → ``BRepOffsetAPI_MakePipe``) so the
    validity verdict matches what the executor would build. Returns ``False`` on
    any exception so the caller drops the profile and the emitter uses the
    circle fallback (byte-identical). Read-only — discards the trial solid.
    """
    try:
        from pydantic import TypeAdapter

        from OCP.BRepCheck import BRepCheck_Analyzer
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe

        from phone_designer.skills.modify_pocket._sketch import SketchSpec
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _build_planar_face,
        )
        from phone_designer.skills.modify_boss.swept_boss_along_curve import (
            _build_path_wire,
            _initial_tangent,
            _transform_face_to_frame,
        )

        pts = [(float(p[0]), float(p[1]), float(p[2])) for p in path_points]
        if len(pts) < 2:
            return False
        spec = TypeAdapter(SketchSpec).validate_python(sketch)
        profile_face = _build_planar_face(spec)
        tangent = _initial_tangent(pts, "polyline")
        oriented = _transform_face_to_frame(profile_face, pts[0], tangent)
        wire = _build_path_wire(pts, "polyline")
        pipe = BRepOffsetAPI_MakePipe(wire, oriented)
        pipe.Build()
        if not pipe.IsDone():
            return False
        solid = pipe.Shape()
        return bool(BRepCheck_Analyzer(solid).IsValid())
    except Exception:
        return False


def _detect_swept_loft_revolve(body, bosses, base_z_max, holes=None):
    """Heuristic detection of sweep / loft / revolve features by surface type.

    Returns (sweep_features, loft_features, revolve_features). Each entry::

        {
            "id": int,
            "bbox": (xmin, ymin, zmin, xmax, ymax, zmax),
            "anchor_z": float,                       # face_z of host plane
            "height_mm": float,                      # bbox extent along Z
            ...feature-specific keys...
        }
    """
    from OCP.GeomAbs import (
        GeomAbs_BSplineSurface,
        GeomAbs_Cone,
        GeomAbs_SurfaceOfExtrusion,
        GeomAbs_SurfaceOfRevolution,
    )

    from phone_designer.skills._resolvers import _all_faces

    shape = _occt_shape(body)
    faces = _all_faces(shape)

    # ── Outer-envelope guard ─────────────────────────────────────────────────
    # Cone / surface-of-extrusion / surface-of-revolution faces that span
    # most of the body's Z extent are NOT loft / sweep / revolve features
    # added on top of the slab — they ARE the slab's outer envelope (e.g.
    # the threaded-shaft cone of a screw, the body of a bottle). Emitting
    # them as boss steps duplicates the body height (FALSE-PASS-DRIFT —
    # the executor reports PASS while the regen bbox grows to 2x original).
    #
    # Body bbox once, then for each candidate face: if its z-extent is more
    # than 50 % of the body z-extent OR it extends below ``base_z_max - tol``,
    # treat it as outer envelope and skip.
    body_bb = None
    try:
        from OCP.Bnd import Bnd_Box as _BB
        from OCP.BRepBndLib import BRepBndLib as _BL
        _bb = _BB()
        try:
            _BL.AddOptimal_s(shape, _bb)
        except Exception:
            _BL.Add_s(shape, _bb)
        if not _bb.IsVoid():
            body_bb = _bb.Get()
    except Exception:
        body_bb = None
    body_z_extent = (body_bb[5] - body_bb[2]) if body_bb is not None else None

    def _is_outer_envelope(fbb) -> bool:
        if fbb is None or body_z_extent is None or body_z_extent <= 1e-6:
            return False
        face_z_extent = fbb[5] - fbb[2]
        # (a) spans most of the body height ⇒ outer wall, not on-top feature.
        if face_z_extent / body_z_extent >= 0.5:
            return True
        # (b) sits below the slab top ⇒ part of the base body, not added on
        #     top of it.
        if base_z_max is not None and fbb[2] < float(base_z_max) - 0.5:
            return True
        return False

    extrusion_faces = []
    bspline_faces = []
    cone_faces = []
    revol_faces = []

    for fi, f in enumerate(faces):
        try:
            t = _surface_kind_int(f)
        except Exception:
            continue
        if t == int(GeomAbs_SurfaceOfExtrusion):
            if _is_outer_envelope(_face_bbox_local(f)):
                continue
            extrusion_faces.append((fi, f))
        elif t == int(GeomAbs_BSplineSurface):
            bspline_faces.append((fi, f))
        elif t == int(GeomAbs_Cone):
            if _is_outer_envelope(_face_bbox_local(f)):
                continue
            cone_faces.append((fi, f))
        elif t == int(GeomAbs_SurfaceOfRevolution):
            if _is_outer_envelope(_face_bbox_local(f)):
                continue
            revol_faces.append((fi, f))

    sweep_features: list[dict] = []
    loft_features: list[dict] = []
    revolve_features: list[dict] = []

    # ── sweep features: surface-of-extrusion (straight sweep segments) ─────
    # Each extrusion face is one straight leg of the polyline sweep — we
    # collapse them into a single feature by bbox-merge.
    if extrusion_faces:
        all_bb = None
        for _fi, f in extrusion_faces:
            bb = _face_bbox_local(f)
            if bb is None:
                continue
            if all_bb is None:
                all_bb = list(bb)
            else:
                all_bb[0] = min(all_bb[0], bb[0])
                all_bb[1] = min(all_bb[1], bb[1])
                all_bb[2] = min(all_bb[2], bb[2])
                all_bb[3] = max(all_bb[3], bb[3])
                all_bb[4] = max(all_bb[4], bb[4])
                all_bb[5] = max(all_bb[5], bb[5])
        if all_bb is not None:
            xmin, ymin, zmin, xmax, ymax, zmax = all_bb
            anchor_z = base_z_max if base_z_max is not None else zmin
            # Estimated profile diameter = min of cross-section extents
            # (the circle fallback when no real section is recovered).
            extent_x = xmax - xmin
            extent_y = ymax - ymin
            profile_d = max(1.0, min(extent_x, extent_y) * 0.5)
            # PILLAR FREEFORM (phase-4, 2026-06-15, ITEM 2): try REAL recovery
            # of the straight sweep path (extrusion-direction-projected bbox
            # span) + one real perpendicular profile section. On any failure we
            # fall back to the historic fabricated 3-point bbox-diagonal path so
            # files that can't recover stay byte-identical.
            profile_sketch = None
            recovered = _recover_sweep_path_and_profile(
                shape, extrusion_faces, all_bb,
            )
            if recovered is not None:
                path_points, profile_sketch, prof_d_real = recovered
                if prof_d_real and prof_d_real > 1e-6:
                    profile_d = max(1.0, float(prof_d_real))
            else:
                # Historic fabricated 3-point bbox-diagonal path (fallback).
                mid_x = 0.5 * (xmin + xmax)
                mid_y = 0.5 * (ymin + ymax)
                path_points = [
                    [round(mid_x, 4), round(ymin, 4), round(anchor_z, 4)],
                    [round(mid_x, 4), round(mid_y, 4), round(zmax, 4)],
                    [round(mid_x, 4), round(ymax, 4), round(zmax, 4)],
                ]
            feat = {
                "id": 0,
                "bbox": [round(c, 4) for c in all_bb],
                "anchor_z": round(anchor_z, 4),
                "height_mm": round(zmax - anchor_z, 4),
                "profile_diameter_mm": round(profile_d, 4),
                "path_points": path_points,
                "kind": "boss",   # surface-of-extrusion ⇒ positive feature
            }
            # Store the recovered real profile ONLY when present; the emitter
            # prefers it and falls back to the circle (byte-identical) otherwise.
            if profile_sketch is not None:
                feat["profile_sketch"] = profile_sketch
            sweep_features.append(feat)

    # ── loft features: cone faces are the lofted frustum walls ─────────────
    # Cone radius + half-angle give us top/bottom radii at the bbox z range.
    for ci, (fi, f) in enumerate(cone_faces):
        bb = _face_bbox_local(f)
        if bb is None:
            continue
        try:
            from OCP.BRepAdaptor import BRepAdaptor_Surface
            cone = BRepAdaptor_Surface(f).Cone()
            ref_r = float(cone.RefRadius())
            half = float(cone.SemiAngle())
        except Exception:
            continue
        xmin, ymin, zmin, xmax, ymax, zmax = bb
        # Cone radius at height z = ref_r + (z - apex_z) * tan(half).
        # We approximate top/bottom radii via bbox extent perpendicular to
        # the dominant axis (assumed Z for the v0 contract).
        extent_xy = max(xmax - xmin, ymax - ymin)
        top_d = max(0.5, extent_xy)
        # Use ref radius as the smaller diameter estimate.
        bot_d = max(0.5, min(2.0 * ref_r, extent_xy))
        # If cone is a hole-side (radius shrinking inward), swap.
        if bot_d > top_d:
            top_d, bot_d = bot_d, top_d
        anchor_z = base_z_max if base_z_max is not None else zmin
        # PACK-C false-pass-drift guard: if the implied loft height is
        # comparable to the body's own Z extent, this cone face is part of
        # the outer envelope (screw shaft/head, bottle body) — emitting it
        # as a loft_boss DUPLICATES the body height. Skip.
        _loft_h = max(zmax - anchor_z, 0.5)
        if body_z_extent is not None and body_z_extent > 1e-6:
            if _loft_h / body_z_extent >= 0.5:
                continue
        cx, cy = 0.5 * (xmin + xmax), 0.5 * (ymin + ymax)
        # COMPLEX-CAD fix (2026-06-07): countersink cones sit inside a hole's
        # radius — appending them as kind:"boss" produces phantom bosses in
        # the regen catalog. Skip when the cone centroid lies inside any
        # detected hole on the body.
        if _cone_in_any_hole(cx, cy, holes):
            continue
        loft_feat = {
            "id": ci,
            "bbox": [round(c, 4) for c in bb],
            "anchor_z": round(anchor_z, 4),
            "height_mm": round(max(zmax - anchor_z, 0.5), 4),
            "lower_diameter_mm": round(top_d, 4),
            "upper_diameter_mm": round(bot_d, 4),
            "center_xy": [round(cx, 4), round(cy, 4)],
            "kind": "boss",   # cone above base plane ⇒ lofted boss
            "_half_angle_rad": round(half, 6),
            "_ref_radius_mm": round(ref_r, 4),
        }
        # PILLAR FREEFORM (phase-2, 2026-06-14): recover the TWO real loft
        # cross-sections at the anchor (lower) and top (upper) z and store them
        # ALONGSIDE the circle estimates. The emitter PREFERS these when present
        # and falls back to the circle otherwise. The keys are added ONLY when a
        # consistent section is recovered (span ≈ circle diameter) — a miss / a
        # slab-sized loop leaves the feature on its circle so the catalog (and
        # every corpus baseline) stays byte-identical.
        _lower_prof = _recover_loft_profile(shape, cx, cy, anchor_z, top_d)
        if _lower_prof is not None:
            loft_feat["lower_profile"] = _lower_prof
        _upper_prof = _recover_loft_profile(shape, cx, cy, zmax, bot_d)
        if _upper_prof is not None:
            loft_feat["upper_profile"] = _upper_prof
        loft_features.append(loft_feat)

    # ── revolve features: surface-of-revolution (annular grooves) ───────────
    for ri, (fi, f) in enumerate(revol_faces):
        bb = _face_bbox_local(f)
        if bb is None:
            continue
        revolve_features.append({
            "id": ri,
            "bbox": [round(c, 4) for c in bb],
            "axis_origin": [
                round(0.5 * (bb[0] + bb[3]), 4),
                round(0.5 * (bb[1] + bb[4]), 4),
                round(bb[2], 4),
            ],
            "axis_direction": [0.0, 0.0, 1.0],
            "angle_deg": 360.0,
            "kind": "pocket",  # default: revolved grooves are subtractive
        })

    return sweep_features, loft_features, revolve_features


def _estimate_base_thickness(body) -> float | None:
    """Find the dominant slab thickness — distance between the two largest
    parallel planar faces (top + bottom).

    Returns None if no obvious slab is detected. This is used to size the
    base ``box`` step so that bosses *above* the base plane do not inflate
    the placeholder box height.
    """
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Plane

        from phone_designer.skills._resolvers import (
            _all_faces, _face_area, _face_normal_at_center,
        )

        shape = _occt_shape(body)
        faces = _all_faces(shape)
        # Collect (axis_dir, position_along_axis, area) triples for axis-
        # aligned planar faces.
        axis_groups: dict[tuple[int, int, int], list[tuple[float, float]]] = {}
        for f in faces:
            try:
                if BRepAdaptor_Surface(f).GetType() != GeomAbs_Plane:
                    continue
            except Exception:
                continue
            n = _face_normal_at_center(f)
            if n == (0.0, 0.0, 0.0):
                continue
            # Snap to nearest axis (within 5°).
            import math
            tol = math.cos(math.radians(5.0))
            axis_key = None
            for ax in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
                       (0, 0, 1), (0, 0, -1)):
                if (n[0] * ax[0] + n[1] * ax[1] + n[2] * ax[2]) >= tol:
                    # Collapse +/- to the absolute axis (we want pairs of
                    # parallel faces).
                    axis_key = (abs(ax[0]), abs(ax[1]), abs(ax[2]))
                    break
            if axis_key is None:
                continue
            try:
                from phone_designer.skills._resolvers import _face_center
                c = _face_center(f)
                area = _face_area(f)
            except Exception:
                continue
            # Position along the chosen axis.
            if axis_key == (1, 0, 0):
                pos = c[0]
            elif axis_key == (0, 1, 0):
                pos = c[1]
            else:
                pos = c[2]
            axis_groups.setdefault(axis_key, []).append((pos, area))

        # For each axis, pick the two largest-area faces and use their
        # position spread as the slab thickness.
        best_thickness: float | None = None
        for key, items in axis_groups.items():
            if len(items) < 2:
                continue
            items.sort(key=lambda t: -t[1])  # by descending area
            top = items[:2]
            thick = abs(top[0][0] - top[1][0])
            if thick < 1e-3:
                continue
            if best_thickness is None or thick < best_thickness:
                # Prefer the smallest slab (most likely the genuine base
                # plate; a boss-top vs base-bottom pair would be thicker).
                best_thickness = thick
        return best_thickness
    except Exception:
        return None


@skill(
    name="extract_feature_catalog",
    category="reverse_engineer",
    level="atomic",
    summary="Aggregate every feature detector (classify_holes / "
            "classify_pockets / detect_bosses / detect_ribs / detect_lugs / "
            "detect_mirror_symmetry / detect_linear_array / "
            "detect_circular_array / match_standard_hole) into a single "
            "feature_catalog dict on result.extras. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["feature_catalog"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.6,
    post_conditions=[PostCondition(kind="body_present")],
)
class ExtractFeatureCatalog(SkillBase):
    class Args(BaseModel):
        max_face_count: int | None = Field(
            default_factory=lambda: __import__(
                "phone_designer.skills._face_count_guard",
                fromlist=["DEFAULT_MAX_FACE_COUNT"],
            ).DEFAULT_MAX_FACE_COUNT,
            description="If the body has more than this many faces, skip the "
                        "feature catalog (returns extras['feature_catalog']="
                        "{'skipped': True, 'face_count': N, 'reason': 'too_big'})"
                        ". Set to None to disable the guard. Default comes from "
                        "phone_designer.skills._face_count_guard so the cap "
                        "stays in sync with classify_pockets. Override at "
                        "host level with PHONE_DESIGNER_MAX_FACE_COUNT.",
        )
        per_component: bool = Field(
            default=False,
            description="If True, run split_into_components first and execute "
                        "every detector against each component (closed shell) "
                        "individually. Per-component results land in "
                        "extras['per_component_catalogs'] as "
                        "[{'component_idx': i, 'catalog': {...}}, ...]. The "
                        "top-level extras['feature_catalog'] still holds the "
                        "global (whole-body) catalog. Useful for multi-shell "
                        "teardown bodies where features on different shells "
                        "would otherwise be co-mingled (or skipped by the "
                        "macro face-count guard).",
        )
        parallel: bool = Field(
            default=True,
            description="Run independent detectors concurrently via "
                        "ThreadPoolExecutor (max 4 workers). OCCT calls release "
                        "the GIL for most heavy work, so 1.5-2x speedup is "
                        "typical on multi-core boxes. Set False for "
                        "deterministic single-threaded execution.",
        )
        classify_pockets_extra_args: dict = Field(
            default_factory=dict,
            description="Extra kwargs merged into the ClassifyPockets call. "
                        "Use this on mesh-derived shells to enable the four "
                        "false-positive filters (min_depth_mm, min_top_d_mm, "
                        "min_depth_to_width_ratio, min_face_count_per_pocket). "
                        "iPhone-tuned defaults: "
                        "{'min_top_d_mm': 2.0, 'min_face_count_per_pocket': 3, "
                        "'min_depth_to_width_ratio': 0.05}.",
        )
        include_edge_blends: bool = Field(
            default=True,
            description="A3 (2026-06-10): run classify_edge_blends and "
                        "populate catalog['edge_blends'] (fillet / chamfer "
                        "strip inventory). A3-FIX (2026-06-11): DEFAULT-ON — "
                        "the detector is now preserve_brep round-trip stable "
                        "via same-surface fragment aggregation + union "
                        "arc-extent gate (Ventilator 31/54 → 15/15 matched "
                        "15, score 0.9091 → 0.9459) and the sub-radius stub "
                        "gate (Crystal_SMD castellation walls dropped both "
                        "sides, 8/4 → 0/0, stays 1.0). 6-file spot check "
                        "(linkrods, as1-oc-214, as1_pe_203, Crystal_SMD, "
                        "C_0603, Ventilator): every previously-PERFECT file "
                        "still 1.0 with blends in the union. Set False to "
                        "skip the detector (catalog carries []).",
        )

    def _build_catalog_for(
        self,
        body: Any,
        max_face_count: int | None,
    ) -> dict:
        """Run every detector on ``body`` and assemble the catalog dict.

        Extracted from ``_apply`` so it can also be invoked per-component
        when ``args.per_component`` is True. Returns the same shape that
        ``extras["feature_catalog"]`` carries (including the ``skipped``
        too_big sentinel on oversize inputs).
        """
        from phone_designer.skills._resolvers import _all_faces
        from phone_designer.skills.inspect.classify_holes import ClassifyHoles
        from phone_designer.skills.inspect.classify_pockets import ClassifyPockets
        from phone_designer.skills.inspect.detect_bosses import DetectBosses
        from phone_designer.skills.inspect.detect_circular_array import (
            DetectCircularArray,
        )
        from phone_designer.skills.inspect.detect_linear_array import (
            DetectLinearArray,
        )
        from phone_designer.skills.inspect.detect_lugs import DetectLugs
        from phone_designer.skills.inspect.detect_mirror_symmetry import (
            DetectMirrorSymmetry,
        )
        from phone_designer.skills.inspect.detect_ribs import DetectRibs
        # match_standard_hole's PURE matcher (_gather_candidates) is imported
        # at the per-hole loop below — the full skill apply() is intentionally
        # NOT used there (it would re-measure the whole body per hole; see the
        # 2026-06-16 PERF note at the match loop).

        # ── face-count guard — bail on raw mesh-to-brep shells ─────────────
        if max_face_count is not None:
            try:
                shape = _occt_shape(body)
                face_count = len(_all_faces(shape))
            except Exception:
                face_count = -1
            if face_count > max_face_count:
                return {
                    "skipped": True,
                    "face_count": face_count,
                    "reason": "too_big",
                    "limit": max_face_count,
                    "advice": "decimate the input mesh (mesh_decimate skill) "
                              "or simplify_to_canonical the BREP before "
                              "calling extract_feature_catalog.",
                }

        # ── bbox snapshot (PACK B drift fix) ───────────────────────────────
        # Detector pipelines below mutate the OCCT shape's optimal-bbox
        # cache (BRepBndLib.AddOptimal_s gets ~0.5-1.0 mm wider after the
        # detectors traverse all faces). Snapshot the bbox NOW so downstream
        # consumers (plan_from_feature_catalog) can size the placeholder
        # base box to the *original* extents instead of the inflated post-
        # detector cache.
        _initial_bbox: tuple[float, float, float, float, float, float] | None
        try:
            from OCP.Bnd import Bnd_Box
            from OCP.BRepBndLib import BRepBndLib
            _shape_for_bbox = _occt_shape(body)
            _bb = Bnd_Box()
            try:
                BRepBndLib.AddOptimal_s(_shape_for_bbox, _bb)
            except Exception:
                BRepBndLib.Add_s(_shape_for_bbox, _bb)
            if not _bb.IsVoid():
                _initial_bbox = tuple(float(c) for c in _bb.Get())  # type: ignore[assignment]
            else:
                _initial_bbox = None
        except Exception:
            _initial_bbox = None

        # ── per-detector timings + skipped-due-to-too-big tracking ─────────
        timings_sec: dict[str, float] = {}
        skipped_detectors: list[str] = []

        def _is_too_big(exc: BaseException) -> bool:
            msg = str(exc).lower()
            return "too_big" in msg or "too big" in msg

        def _timed(name: str, fn, *fa, **fk):
            t0 = time.perf_counter()
            try:
                return fn(*fa, **fk)
            except Exception as exc:
                if _is_too_big(exc):
                    skipped_detectors.append(name)
                return None
            finally:
                timings_sec[name] = round(time.perf_counter() - t0, 4)

        # ── Independent detectors — run in parallel if requested ───────────
        # All six release the GIL inside OCP so threading helps.
        cp_extra = dict(getattr(self, "_classify_pockets_extra_args", {}) or {})
        independent = [
            ("classify_holes",         ClassifyHoles().apply,        body, {"match_standards": True}),
            ("classify_pockets",       ClassifyPockets().apply,      body, cp_extra),
            ("detect_bosses",          DetectBosses().apply,         body, {}),
            ("detect_ribs",            DetectRibs().apply,           body, {}),
            ("detect_lugs",            DetectLugs().apply,           body, {}),
            ("detect_mirror_symmetry", DetectMirrorSymmetry().apply, body, {}),
        ]
        results: dict[str, Any] = {}
        if getattr(self, "_parallel_mode", True):
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=4) as ex:
                futs = {ex.submit(_timed, n, fn, b, a): n
                        for (n, fn, b, a) in independent}
                for fut in futs:
                    results[futs[fut]] = fut.result()
        else:
            for n, fn, b, a in independent:
                results[n] = _timed(n, fn, b, a)

        holes_res   = results.get("classify_holes")
        pockets_res = results.get("classify_pockets")
        bosses_res  = results.get("detect_bosses")
        ribs_res    = results.get("detect_ribs")
        lugs_res    = results.get("detect_lugs")
        sym_res     = results.get("detect_mirror_symmetry")
        holes      = holes_res.extras.get("holes", [])           if holes_res   else []
        pockets    = pockets_res.extras.get("pockets", [])       if pockets_res else []
        bosses     = bosses_res.extras.get("bosses", [])         if bosses_res  else []
        ribs       = ribs_res.extras.get("ribs", [])             if ribs_res    else []
        lugs       = lugs_res.extras.get("lugs", [])             if lugs_res    else []
        symmetries = sym_res.extras.get("mirror_planes", [])     if sym_res     else []

        # COMPLEX-CAD fix (2026-06-08): drop bosses whose face_indices
        # overlap any pocket, or whose XY centroid lies inside any pocket's
        # circular footprint. detect_bosses' "above base plane" heuristic
        # picks up cylindrical POCKET walls as cylindrical-boss candidates,
        # and the cluster classifier weighs cylinder area enough to commit.
        # Mirrors the existing _cone_in_any_hole pattern (loft features).
        if isinstance(pockets, list) and pockets and isinstance(bosses, list):
            _bosses_filtered: list[dict] = []
            for _b in bosses:
                if _boss_face_indices_in_pocket(_b, pockets):
                    continue
                if _boss_xy_in_any_pocket(_b, pockets):
                    continue
                _bosses_filtered.append(_b)
            bosses = _bosses_filtered

        # ── detect_*_array (linear + circular) ─────────────────────────────
        # phase-0 (2026-06-14) SPEED: the linear + circular array detectors
        # each re-run ClassifyHoles / ClassifyPockets / DetectBosses internally
        # (via their _feature_positions helper) to recover feature centres —
        # 2 detectors × 3 kinds = 6 re-detections per catalog, on top of the
        # ones already run above. They all extract from the SAME `body` at the
        # SAME point in the pipeline, so the result is identical across the six
        # calls. We extract each kind's positions ONCE here (using the
        # detectors' own _feature_positions, at the exact mutation point the
        # first re-extraction happens today) and FEED them to every array call
        # via the detector's `_precomputed_positions` attr. When the attr is
        # absent/None the detector re-extracts exactly as before — so standalone
        # behavior is byte-identical, and the catalog output stays byte-identical
        # too (we reproduce the detector's own extraction, not the early
        # pre-mutation classify pass, which can differ by OCCT sub-mm bbox-cache
        # drift). NOTE: we deliberately do NOT reuse the `holes`/`pockets`/
        # `bosses` lists detected earlier — those were classified before the
        # other detectors widened OCCT's optimal-bbox cache, which shifts
        # classify_holes' _standardize_entry result by ~0.01 mm; the array
        # detectors today see the post-mutation value, and we must match it.
        from phone_designer.skills.inspect.detect_linear_array import (
            _feature_positions as _lin_feature_positions,
        )
        _precomp_positions: dict[str, list] = {}
        for _kind in ("hole", "pocket", "boss"):
            try:
                _precomp_positions[_kind] = _lin_feature_positions(body, _kind)
            except Exception:
                # Mirror the detector's own behavior on extraction failure:
                # leave the kind unset so the detector re-extracts (and hits
                # the same exception / too_big path) standalone.
                pass

        patterns: list[dict] = []
        lin_total = 0.0
        circ_total = 0.0
        lin_skipped = False
        circ_skipped = False
        for kind in ("hole", "pocket", "boss"):
            t0 = time.perf_counter()
            try:
                _lin_det = DetectLinearArray()
                _lin_det._precomputed_positions = _precomp_positions
                lin_res = _lin_det.apply(
                    body, {"feature_kind": kind, "min_count": 3},
                )
            except Exception as exc:
                lin_res = None
                if _is_too_big(exc):
                    lin_skipped = True
            lin_total += time.perf_counter() - t0
            if lin_res:
                for run in lin_res.extras.get("linear_arrays", []) or []:
                    p = dict(run)
                    p["pattern_kind"] = "linear"
                    p["feature_kind"] = kind
                    patterns.append(p)

            t0 = time.perf_counter()
            try:
                _circ_det = DetectCircularArray()
                _circ_det._precomputed_positions = _precomp_positions
                circ_res = _circ_det.apply(
                    body, {"feature_kind": kind, "min_count": 4},
                )
            except Exception as exc:
                circ_res = None
                if _is_too_big(exc):
                    circ_skipped = True
            circ_total += time.perf_counter() - t0
            if circ_res:
                for ring in circ_res.extras.get("circular_arrays", []) or []:
                    p = dict(ring)
                    p["pattern_kind"] = "circular"
                    p["feature_kind"] = kind
                    patterns.append(p)
        timings_sec["detect_linear_array"] = round(lin_total, 4)
        timings_sec["detect_circular_array"] = round(circ_total, 4)
        if lin_skipped:
            skipped_detectors.append("detect_linear_array")
        if circ_skipped:
            skipped_detectors.append("detect_circular_array")

        # ── match_standard_hole — one match per hole's primary diameter ────
        # 2026-06-16 PERF: call the PURE matching function directly instead of
        # MatchStandardHole().apply(body, ...) per hole. The full skill apply()
        # wrapper runs _measure(body) BEFORE and AFTER every call (volume +
        # face-count via BRepGProp on the WHOLE body) — on a 493-face assembly
        # leaf that body-measurement, times N holes, was 81.6s of the catalog's
        # 182s (the single biggest cost), while the diameter→catalog match
        # itself is microseconds. _gather_candidates + sort reproduces the
        # skill's matches[0] EXACTLY (byte-identical best_match) with zero body
        # measurement. Output unchanged; only the wrapper overhead removed.
        from phone_designer.skills.inspect.match_standard_hole import (
            _gather_candidates as _msh_gather,
        )
        standard_matches: list[dict] = []
        t_msh0 = time.perf_counter()
        for h in holes:
            diams = h.get("diameters_mm") or []
            if not diams:
                continue
            primary_d = min(diams)  # shaft (clearance) diameter
            top = None
            try:
                cands = _msh_gather(float(primary_d), "auto")
                cands.sort(key=lambda m: m["deviation_mm"])
                top = cands[0] if cands else None
            except Exception:
                top = None
            standard_matches.append({
                "hole_id": h.get("id"),
                "diameter_mm": float(primary_d),
                "best_match": top,
            })
        timings_sec["match_standard_hole"] = round(
            time.perf_counter() - t_msh0, 4)

        # ── sweep / revolve / loft surface-type detection ──────────────────
        t0 = time.perf_counter()
        base_thickness = _safe(_estimate_base_thickness, body)
        timings_sec["estimate_base_thickness"] = round(time.perf_counter() - t0, 4)
        base_z_max: float | None = None
        try:
            from OCP.Bnd import Bnd_Box
            from OCP.BRepBndLib import BRepBndLib
            shape = _occt_shape(body)
            bb = Bnd_Box()
            BRepBndLib.AddOptimal_s(shape, bb)
            if not bb.IsVoid():
                _, _, zmin, _, _, _ = bb.Get()
                if base_thickness is not None:
                    base_z_max = float(zmin) + float(base_thickness)
        except Exception:
            base_z_max = None

        swr = _timed(
            "detect_swept_loft_revolve",
            _detect_swept_loft_revolve, body, bosses, base_z_max, holes,
        )
        if swr is not None:
            sweep_features, loft_features, revolve_features = swr
        else:
            sweep_features, loft_features, revolve_features = [], [], []

        # ── edge blends (fillets / chamfers) — A3 (2026-06-10) ─────────────
        # Run SERIALLY after the independent pool (not inside it) so the new
        # detector cannot perturb the proven 6-detector thread interleaving.
        # Same _timed per-detector isolation: a failure leaves [] and the
        # rest of the catalog intact; a too_big guard hit returns the
        # sentinel dict (mirrors classify_pockets) which _counts() in
        # feature_fidelity_diff already treats as 0 entries.
        edge_blends: Any = []
        if getattr(self, "_include_edge_blends", False):
            from phone_designer.skills.inspect.classify_edge_blends import (
                ClassifyEdgeBlends,
            )
            eb_res = _timed(
                "classify_edge_blends", ClassifyEdgeBlends().apply, body, {},
            )
            if eb_res is not None:
                edge_blends = eb_res.extras.get("edge_blends", [])
                if isinstance(edge_blends, dict) and edge_blends.get("skipped"):
                    skipped_detectors.append("classify_edge_blends")

        feature_catalog = {
            "holes": holes,
            "pockets": pockets,
            "bosses": bosses,
            "ribs": ribs,
            "lugs": lugs,
            "symmetries": symmetries,
            "patterns": patterns,
            "standard_matches": standard_matches,
            "sweep_features": sweep_features,
            "loft_features": loft_features,
            "revolve_features": revolve_features,
            "edge_blends": edge_blends,
            "base_thickness_mm": (
                float(base_thickness) if base_thickness is not None else None
            ),
            # PACK B drift fix — pre-detector optimal bbox in world coords as
            # (xmin, ymin, zmin, xmax, ymax, zmax). Consumers should prefer
            # this over re-computing _body_bbox at plan time, which returns
            # the inflated post-detector AddOptimal_s cache.
            "initial_bbox_mm": list(_initial_bbox) if _initial_bbox is not None else None,
            "_timings_sec": timings_sec,
        }
        if skipped_detectors:
            feature_catalog["_skipped_detectors"] = skipped_detectors
        return feature_catalog

    def _apply(self, body: Any, args: Args) -> SkillResult:
        # Wire arg → instance attr so _build_catalog_for can read it.
        self._parallel_mode = bool(args.parallel)
        self._classify_pockets_extra_args = dict(args.classify_pockets_extra_args or {})
        self._include_edge_blends = bool(args.include_edge_blends)
        # ── Whole-body catalog (always computed) ───────────────────────────
        feature_catalog = self._build_catalog_for(body, args.max_face_count)

        extras: dict = {"feature_catalog": feature_catalog}

        # ── Per-component catalogs (optional) ──────────────────────────────
        # When the caller has a multi-shell body (e.g. a teardown mesh that
        # produced 65 disjoint shells), running every detector against the
        # whole compound co-mingles features from physically separate parts.
        # Splitting first and re-running the detector pipeline on each shell
        # gives a clean per-part catalog. We still keep the whole-body
        # catalog above for backwards-compat.
        if args.per_component:
            from phone_designer.skills.repair.split_into_components import (
                SplitIntoComponents,
            )
            try:
                split_res = SplitIntoComponents().apply(body, {})
                components = split_res.extras.get("components", []) or []
            except Exception:
                components = []

            per_component_catalogs: list[dict] = []
            for comp in components:
                comp_body = comp.get("body_ref")
                if comp_body is None:
                    continue
                try:
                    cat = self._build_catalog_for(comp_body, args.max_face_count)
                except Exception as exc:
                    cat = {
                        "skipped": True,
                        "reason": "exception",
                        "error": str(exc)[:200],
                    }
                per_component_catalogs.append({
                    "component_idx": comp.get("index"),
                    "catalog": cat,
                })
            extras["per_component_catalogs"] = per_component_catalogs
            extras["component_count"] = len(per_component_catalogs)

        # Share the catalog with plan_from_feature_catalog (which accepts
        # ``catalog=None`` meaning "use last extracted"). Import is local so
        # the two skill modules can be loaded in any order.
        try:
            from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
                PlanFromFeatureCatalog,
            )
            PlanFromFeatureCatalog._LAST_CATALOG = feature_catalog
        except Exception:
            pass

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
