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
      "base_thickness_mm": float,   # dominant slab thickness for base-box step
    }

Body is unchanged (post body_present). Catches individual detector failures
so a single broken detector cannot poison the whole catalog.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

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


def _detect_swept_loft_revolve(body, bosses, base_z_max):
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
            extrusion_faces.append((fi, f))
        elif t == int(GeomAbs_BSplineSurface):
            bspline_faces.append((fi, f))
        elif t == int(GeomAbs_Cone):
            cone_faces.append((fi, f))
        elif t == int(GeomAbs_SurfaceOfRevolution):
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
            # Build a 3-point polyline path along the bbox diagonal in XY,
            # starting at the base plane and rising to bbox top.
            anchor_z = base_z_max if base_z_max is not None else zmin
            mid_x = 0.5 * (xmin + xmax)
            mid_y = 0.5 * (ymin + ymax)
            path_points = [
                [round(mid_x, 4), round(ymin, 4), round(anchor_z, 4)],
                [round(mid_x, 4), round(mid_y, 4), round(zmax, 4)],
                [round(mid_x, 4), round(ymax, 4), round(zmax, 4)],
            ]
            # Estimated profile diameter = min of cross-section extents.
            extent_x = xmax - xmin
            extent_y = ymax - ymin
            profile_d = max(1.0, min(extent_x, extent_y) * 0.5)
            sweep_features.append({
                "id": 0,
                "bbox": [round(c, 4) for c in all_bb],
                "anchor_z": round(anchor_z, 4),
                "height_mm": round(zmax - anchor_z, 4),
                "profile_diameter_mm": round(profile_d, 4),
                "path_points": path_points,
                "kind": "boss",   # surface-of-extrusion ⇒ positive feature
            })

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
        cx, cy = 0.5 * (xmin + xmax), 0.5 * (ymin + ymax)
        loft_features.append({
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
        })

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
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
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
        from phone_designer.skills.inspect.match_standard_hole import (
            MatchStandardHole,
        )

        # ── classify_holes (already does its own standard match per hole) ──
        holes_res = _safe(ClassifyHoles().apply, body, {"match_standards": True})
        holes = holes_res.extras.get("holes", []) if holes_res else []

        # ── classify_pockets ───────────────────────────────────────────────
        pockets_res = _safe(ClassifyPockets().apply, body, {})
        pockets = pockets_res.extras.get("pockets", []) if pockets_res else []

        # ── detect_bosses ──────────────────────────────────────────────────
        bosses_res = _safe(DetectBosses().apply, body, {})
        bosses = bosses_res.extras.get("bosses", []) if bosses_res else []

        # ── detect_ribs ────────────────────────────────────────────────────
        ribs_res = _safe(DetectRibs().apply, body, {})
        ribs = ribs_res.extras.get("ribs", []) if ribs_res else []

        # ── detect_lugs ────────────────────────────────────────────────────
        lugs_res = _safe(DetectLugs().apply, body, {})
        lugs = lugs_res.extras.get("lugs", []) if lugs_res else []

        # ── detect_mirror_symmetry ─────────────────────────────────────────
        sym_res = _safe(DetectMirrorSymmetry().apply, body, {})
        symmetries = sym_res.extras.get("mirror_planes", []) if sym_res else []

        # ── detect_*_array (linear + circular) ─────────────────────────────
        patterns: list[dict] = []
        for kind in ("hole", "pocket", "boss"):
            lin_res = _safe(
                DetectLinearArray().apply, body,
                {"feature_kind": kind, "min_count": 3},
            )
            if lin_res:
                for run in lin_res.extras.get("linear_arrays", []) or []:
                    p = dict(run)
                    p["pattern_kind"] = "linear"
                    p["feature_kind"] = kind
                    patterns.append(p)

            circ_res = _safe(
                DetectCircularArray().apply, body,
                {"feature_kind": kind, "min_count": 4},
            )
            if circ_res:
                for ring in circ_res.extras.get("circular_arrays", []) or []:
                    p = dict(ring)
                    p["pattern_kind"] = "circular"
                    p["feature_kind"] = kind
                    patterns.append(p)

        # ── match_standard_hole — one call per hole's primary diameter ─────
        standard_matches: list[dict] = []
        for h in holes:
            diams = h.get("diameters_mm") or []
            if not diams:
                continue
            primary_d = min(diams)  # shaft (clearance) diameter
            mres = _safe(
                MatchStandardHole().apply,
                body,
                {"hole_diameter_mm": float(primary_d), "fit_kind": "auto"},
            )
            top = None
            if mres:
                matches = mres.extras.get("matches", []) or []
                top = matches[0] if matches else None
            standard_matches.append({
                "hole_id": h.get("id"),
                "diameter_mm": float(primary_d),
                "best_match": top,
            })

        # ── sweep / revolve / loft surface-type detection ──────────────────
        # Compute slab thickness first; used both for base box sizing and as
        # the anchor_z of sweep/loft features sitting on top of the slab.
        base_thickness = _safe(_estimate_base_thickness, body)
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

        swr = _safe(_detect_swept_loft_revolve, body, bosses, base_z_max)
        if swr is not None:
            sweep_features, loft_features, revolve_features = swr
        else:
            sweep_features, loft_features, revolve_features = [], [], []

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
            "base_thickness_mm": (
                float(base_thickness) if base_thickness is not None else None
            ),
        }

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
            extras={"feature_catalog": feature_catalog},
        )
