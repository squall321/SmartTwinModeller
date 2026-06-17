"""classify_generating_op — atomic, read-only (PILLAR FREEFORM, 2026-06-16).

Classify a freeform body's DOMINANT generating operation and recover the
EDITABLE inputs of that operation so a downstream planner can emit a real
parametric build step (revolve / loft / sweep) — NOT a re-solidified shell.

This is the inspect-side counterpart of the unsolved problem: ``recover_freeform_
solid`` re-solidifies the imported boundary (near-lossless but NOT editable);
this skill instead asks "what single op GENERATES this body?" and recovers its
tunable parameters (the revolve meridian profile, the N loft sections, the sweep
section + path) so the body can be rebuilt parametrically and re-edited.

Decision (one dominant op, geometric ratios only — NEVER per-file):

  * REVOLVE — solids-of-revolution faces (cylinder + cone + surface-of-
    revolution) dominate the area AND share ONE axis (cos ~18°). The meridian
    PROFILE is recovered as a HALF-PLANE section through the axis: a plane whose
    normal is perpendicular to the axis, cutting one side, projected so the axis
    becomes local +Y and the radial direction local +X (the exact frame
    ``revolve_boss`` consumes). ``recover_section_loop`` walks the section into an
    executable sketch dict; we keep only the half on the +radial side of the
    axis (a meridian must not cross the axis).
  * LOFT — a stack of N planar sections (default 3: 15/50/85 % of the axis span)
    along the dominant axis, each recovered via ``recover_section_loop``. Fires
    when the sections are all valid closed loops but their areas/shapes VARY
    along the axis (a tapering / shape-transitioning body) — i.e. it is not a
    constant-section sweep and not a single solid of revolution.
  * SWEEP — a CONSTANT cross-section translated along a path. Fires when N
    sections perpendicular to the dominant axis are all closed loops of nearly
    equal area (within ``sweep_area_tol``) and the path is (for v0) the straight
    centroid line along the axis. The section + straight path are recovered.
  * NONE — no single op recovers cleanly (mixed/ generic freeform). Honest
    fallback: the caller keeps re-solidify / box and says so.

extras schema::

    {"generating_op": {
        "kind": "revolve" | "loft" | "sweep" | "none",
        "confidence": float,                     # 0..1
        "axis": {"origin": [x,y,z], "dir": [dx,dy,dz]} | None,
        "profile_sketch": <executable sketch dict> | None,   # revolve meridian
        "section_sketches": [                                 # loft stations
            {"sketch": <dict>, "z_along_axis_mm": float, "center_xy": [x,y]},
            ...
        ] | None,
        "path": {"type": "polyline", "points": [[x,y,z], ...]} | None,  # sweep
        "reason": str,                           # why this kind / why none
        "diagnostics": {...},                    # area ratios, station areas, …
     }}

The recovered fields are the EDITABLE inputs of revolve_boss /
loft_boss_between_sketches / swept_boss_along_curve — change a parameter (e.g.
scale the meridian radius) and the rebuilt body moves parametrically, which a
re-solidify CANNOT do.

body 는 변경하지 않는다 (post ``body_present``). Read-only — imports/reuses
``_section_curve.recover_section_loop`` + ``principal_axes`` + the SoR-axis
detector, mutates nothing.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.inspect._section_curve import (
    _inplane_frame,
    recover_section_loop,
)


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _bbox(shape) -> tuple[float, float, float, float, float, float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        BRepBndLib.Add_s(shape, bb)
    return bb.Get()


def _centroid(shape) -> tuple[float, float, float]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    c = props.CentreOfMass()
    return (float(c.X()), float(c.Y()), float(c.Z()))


def _revolution_axis(shape) -> tuple[
    float, tuple[float, float, float], tuple[float, float, float]
] | None:
    """Area-weighted single rotational axis, or None.

    Returns ``(rev_area_fraction, axis_dir, axis_point)`` when the solids-of-
    revolution faces dominate the area AND share one axis (all cos >= 0.95 with
    the largest-area axis). ``axis_point`` is a point ON the largest face's axis
    (so the meridian half-plane can be anchored exactly on it). Pure geometric
    ratios — mirrors ``_classify_base_topology``'s revolve test.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import (
        GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_SurfaceOfRevolution,
    )
    from OCP.GProp import GProp_GProps

    from phone_designer.skills._resolvers import _all_faces

    faces = _all_faces(shape)
    if not faces:
        return None
    total_area = 0.0
    rev_area = 0.0
    # (area, axis_dir, axis_point)
    axes: list[tuple[float, tuple[float, float, float], tuple[float, float, float]]] = []
    for f in faces:
        try:
            surf = BRepAdaptor_Surface(f)
            kind_int = surf.GetType()
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(f, props)
            a = float(props.Mass())
            total_area += a
            ax = None
            loc = None
            if kind_int == GeomAbs_Cylinder:
                cyl = surf.Cylinder()
                ax = cyl.Axis().Direction()
                loc = cyl.Axis().Location()
            elif kind_int == GeomAbs_Cone:
                con = surf.Cone()
                ax = con.Axis().Direction()
                loc = con.Axis().Location()
            elif kind_int == GeomAbs_SurfaceOfRevolution:
                axe = surf.AxeOfRevolution()
                ax = axe.Direction()
                loc = axe.Location()
            if ax is not None and loc is not None:
                rev_area += a
                axes.append((
                    a,
                    (ax.X(), ax.Y(), ax.Z()),
                    (loc.X(), loc.Y(), loc.Z()),
                ))
        except Exception:
            continue

    if total_area <= 0.0 or not axes:
        return None
    frac = rev_area / total_area
    if frac <= 0.5:
        return None
    axes.sort(key=lambda t: -t[0])
    ref_dir = axes[0][1]
    ref_pt = axes[0][2]
    for _a, d, _p in axes:
        dot = abs(d[0] * ref_dir[0] + d[1] * ref_dir[1] + d[2] * ref_dir[2])
        if dot < 0.95:
            return None
    # Normalise the axis direction.
    m = math.sqrt(ref_dir[0] ** 2 + ref_dir[1] ** 2 + ref_dir[2] ** 2)
    if m < 1e-12:
        return None
    ref_dir = (ref_dir[0] / m, ref_dir[1] / m, ref_dir[2] / m)
    # CANONICAL orientation: point the axis so its DOMINANT component is positive.
    # Revolving about an axis and about its negation yields the same solid, but
    # the recovered meridian's "along-axis" coordinate (local +Y, which
    # _build_revolved_solid maps to world +Z) must grow in the SAME sense the
    # body extends, or the rebuilt solid mirrors through the origin. Flipping to
    # the canonical +dominant axis keeps the meridian Y positive so the revolve
    # grows the right way.
    comps = (abs(ref_dir[0]), abs(ref_dir[1]), abs(ref_dir[2]))
    k = comps.index(max(comps))
    if ref_dir[k] < 0.0:
        ref_dir = (-ref_dir[0], -ref_dir[1], -ref_dir[2])
    return (frac, ref_dir, ref_pt)


def _meridian_profile(
    shape,
    axis_dir: tuple[float, float, float],
    axis_pt: tuple[float, float, float],
    simplify_tol_mm: float,
) -> dict | None:
    """Recover the revolve MERIDIAN as the half-plane section through the axis.

    The cutting plane contains the axis; its normal is a unit vector
    perpendicular to the axis. ``recover_section_loop`` returns the full section
    (both meridian halves, joined across the axis), planar in the plane's (u, v)
    frame. We RE-EXPRESS the loop in the meridian frame where ``+Y`` is the axis
    direction and ``+X`` is the radial direction, keep only the ``x >= 0`` half
    (a meridian must not cross the axis — that is exactly what ``revolve_boss``
    requires), and emit it as a polygon sketch.

    Returns the executable profile sketch dict, or None.
    """
    # In-plane frame of the cutting plane: normal ⟂ axis. _inplane_frame gives
    # (u, v) spanning the plane; we want the plane to CONTAIN the axis, so pick
    # a plane normal perpendicular to the axis. Build a radial reference r ⟂ axis.
    ax = axis_dir
    # pick a world ref not parallel to the axis
    ref = (1.0, 0.0, 0.0) if abs(ax[0]) < 0.9 else (0.0, 1.0, 0.0)
    # radial r = ref - (ref·ax) ax  (in the plane, ⟂ axis)
    d = ref[0] * ax[0] + ref[1] * ax[1] + ref[2] * ax[2]
    r = (ref[0] - d * ax[0], ref[1] - d * ax[1], ref[2] - d * ax[2])
    rmag = math.sqrt(r[0] ** 2 + r[1] ** 2 + r[2] ** 2)
    if rmag < 1e-9:
        return None
    r = (r[0] / rmag, r[1] / rmag, r[2] / rmag)
    # plane normal n = axis × r  (⟂ both axis and the radial → plane contains axis)
    n = (
        ax[1] * r[2] - ax[2] * r[1],
        ax[2] * r[0] - ax[0] * r[2],
        ax[0] * r[1] - ax[1] * r[0],
    )
    nmag = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
    if nmag < 1e-9:
        return None
    n = (n[0] / nmag, n[1] / nmag, n[2] / nmag)

    rec = recover_section_loop(
        shape, axis_pt, n, simplify_tol_mm=simplify_tol_mm,
    )
    if rec.get("sketch") is None or rec.get("skipped") or rec.get("empty"):
        return None

    # recover_section_loop returns 2D coords in ITS plane frame (u', v') from
    # _inplane_frame(n). Re-project each emitted vertex onto (r=+X, axis=+Y) so
    # the meridian lands in the revolve_boss frame, then keep the x>=0 half.
    u2, v2 = _inplane_frame(n)
    # The recovered sketch coords c=(cu, cv) correspond to the 3D point
    #   P = axis_pt + cu*u2 + cv*v2.
    # We want meridian coords (X=radial, Y=along axis):
    #   X = (P - axis_pt)·r ,  Y = (P - axis_pt)·axis.
    def remap(cu: float, cv: float) -> tuple[float, float]:
        px = cu * u2[0] + cv * v2[0]
        py = cu * u2[1] + cv * v2[1]
        pz = cu * u2[2] + cv * v2[2]
        x = px * r[0] + py * r[1] + pz * r[2]
        y = px * ax[0] + py * ax[1] + pz * ax[2]
        return (x, y)

    sk = rec["sketch"]
    pts = _sketch_loop_points(sk)
    if not pts:
        return None
    mer = [remap(cu, cv) for (cu, cv) in pts]
    # CLIP to the +X (radial) half-plane (x >= 0). The full meridian section
    # spans -R..+R across the axis; the GENERATING meridian is the half on one
    # side of the axis (a meridian must NOT cross the axis — exactly what
    # revolve_boss requires). Sutherland-Hodgman clipping against x>=0 keeps the
    # +X sub-loop intact and inserts the two on-axis (x=0) crossing points, so a
    # symmetric revolved section yields its true generating profile.
    cleaned = _clip_half_plane_x(mer)
    if len(cleaned) < 3:
        return None
    return {
        "kind": "polygon",
        "vertices": [(round(x, 4), round(y, 4)) for (x, y) in cleaned],
        "corner_fillet_r": 0.0,
        "center_x_mm": 0.0,
        "center_y_mm": 0.0,
    }


def _clip_half_plane_x(loop: list[tuple[float, float]], tol: float = 1e-4) -> list[tuple[float, float]]:
    """Sutherland-Hodgman clip a closed 2D loop against the x >= 0 half-plane.

    Keeps the portion of the meridian on the +X (radial) side of the revolution
    axis (x=0). Edges crossing the axis get a vertex inserted at their x=0
    intersection, so the returned sub-loop is a valid closed meridian that does
    NOT cross the axis. Consecutive coincident points are collapsed.
    """
    if len(loop) < 3:
        return []
    out: list[tuple[float, float]] = []
    n = len(loop)
    for i in range(n):
        cur = loop[i]
        prv = loop[i - 1]
        cur_in = cur[0] >= -tol
        prv_in = prv[0] >= -tol
        if cur_in:
            if not prv_in:
                # entering: insert the x=0 crossing
                t = prv[0] / (prv[0] - cur[0]) if (prv[0] - cur[0]) != 0 else 0.0
                out.append((0.0, prv[1] + t * (cur[1] - prv[1])))
            out.append((max(cur[0], 0.0), cur[1]))
        elif prv_in:
            # leaving: insert the x=0 crossing
            t = prv[0] / (prv[0] - cur[0]) if (prv[0] - cur[0]) != 0 else 0.0
            out.append((0.0, prv[1] + t * (cur[1] - prv[1])))
    # collapse consecutive duplicates + wrap duplicate
    cleaned: list[tuple[float, float]] = []
    for p in out:
        if not cleaned or math.hypot(p[0] - cleaned[-1][0], p[1] - cleaned[-1][1]) > tol:
            cleaned.append(p)
    if len(cleaned) >= 2 and math.hypot(
        cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]
    ) <= tol:
        cleaned.pop()
    return cleaned


def _sketch_loop_points(sketch: dict) -> list[tuple[float, float]]:
    """Flatten an executable sketch dict (polygon | bspline_closed | composite)
    back to its ordered 2D loop points (for re-projection)."""
    kind = sketch.get("kind")
    if kind == "polygon":
        return [(float(x), float(y)) for (x, y) in sketch.get("vertices", [])]
    if kind == "bspline_closed":
        return [(float(x), float(y)) for (x, y) in sketch.get("fit_points", [])]
    if kind == "composite":
        pts: list[tuple[float, float]] = []
        for seg in sketch.get("segments", []):
            if "start" in seg and "end" in seg:
                for key in ("start", "end"):
                    p = seg[key]
                    pt = (float(p[0]), float(p[1]))
                    if not pts or math.hypot(pt[0] - pts[-1][0], pt[1] - pts[-1][1]) > 1e-6:
                        pts.append(pt)
            elif "points" in seg:
                for p in seg["points"]:
                    pt = (float(p[0]), float(p[1]))
                    if not pts or math.hypot(pt[0] - pts[-1][0], pt[1] - pts[-1][1]) > 1e-6:
                        pts.append(pt)
        return pts
    return []


def _section_at(
    shape,
    axis_dir: tuple[float, float, float],
    origin: tuple[float, float, float],
    simplify_tol_mm: float,
) -> dict | None:
    """recover_section_loop with the axis as the plane normal (a perpendicular
    cross-section). Returns the full recovery dict or None."""
    rec = recover_section_loop(
        shape, origin, axis_dir, simplify_tol_mm=simplify_tol_mm,
    )
    if rec.get("sketch") is None or rec.get("skipped") or rec.get("empty"):
        return None
    return rec


def _section_center_xy(
    sketch: dict,
) -> tuple[float, float]:
    """Centroid of a section loop in its own (u, v) plane coords (for loft
    re-centring). Simple vertex average — adequate for a section anchor."""
    pts = _sketch_loop_points(sketch)
    if not pts:
        return (0.0, 0.0)
    sx = sum(p[0] for p in pts) / len(pts)
    sy = sum(p[1] for p in pts) / len(pts)
    return (sx, sy)


@skill(
    name="classify_generating_op",
    category="inspect",
    level="atomic",
    summary="Classify a freeform body's dominant generating operation "
            "(REVOLVE / LOFT / SWEEP / NONE) and recover its EDITABLE inputs — "
            "the revolve meridian profile (half-plane section through the "
            "axis), the N loft sections, or the constant sweep section + path. "
            "The recovered op is a parametric rebuild target, not a "
            "re-solidified shell. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["generating_op"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.section_recovery_failed"],
    cost_hint=0.5,
    post_conditions=[PostCondition(kind="body_present")],
)
class ClassifyGeneratingOp(SkillBase):
    class Args(BaseModel):
        n_sections: int = Field(
            default=3, ge=2, le=12,
            description="Number of cross-section stations along the dominant "
                        "axis used for the loft/sweep tests.",
        )
        sweep_area_tol: float = Field(
            default=0.08, gt=0.0, le=1.0,
            description="Max relative spread of station areas for a CONSTANT "
                        "section (sweep). Above it the stations vary → loft.",
        )
        simplify_tol_mm: float = Field(
            default=0.05, ge=0.0,
            description="Douglas-Peucker tolerance passed to "
                        "recover_section_loop for every recovered loop.",
        )
        min_confidence: float = Field(
            default=0.5, ge=0.0, le=1.0,
            description="Below this the op is reported as 'none' (honest "
                        "fallback) regardless of which test fired.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("classify_generating_op: body is None")
        shape = _occt_shape(body)
        if shape is None:
            raise ValueError("classify_generating_op: body.wrapped is None")

        diagnostics: dict[str, Any] = {}
        op: dict[str, Any] = {
            "kind": "none",
            "confidence": 0.0,
            "axis": None,
            "profile_sketch": None,
            "section_sketches": None,
            "path": None,
            "reason": "",
            "diagnostics": diagnostics,
        }

        bb = _bbox(shape)
        xmin, ymin, zmin, xmax, ymax, zmax = (float(v) for v in bb)
        centroid = _centroid(shape)

        # ── 1. REVOLVE: single dominant rotational axis → recover the meridian.
        rev = _revolution_axis(shape)
        if rev is not None:
            frac, axis_dir, axis_pt = rev
            diagnostics["revolve_area_fraction"] = round(frac, 4)
            profile = _meridian_profile(
                shape, axis_dir, axis_pt, args.simplify_tol_mm,
            )
            if profile is not None:
                # confidence keyed on how much of the area is rotational.
                conf = round(min(1.0, max(0.5, frac)), 4)
                if conf >= args.min_confidence:
                    op.update({
                        "kind": "revolve",
                        "confidence": conf,
                        "axis": {
                            "origin": [round(c, 6) for c in axis_pt],
                            "dir": [round(c, 6) for c in axis_dir],
                        },
                        "profile_sketch": profile,
                        "reason": (
                            f"solids-of-revolution faces cover {frac:.0%} of "
                            "the area about one axis; meridian recovered."
                        ),
                    })
                    return self._result(body, op)
            diagnostics["revolve_meridian"] = "recovery_failed"

        # ── 2/3. LOFT vs SWEEP: cross-section stations along the stacking axis.
        # 2026-06-17 ROBUSTNESS: try ALL THREE bbox axes, not just the longest.
        # The longest-axis-only heuristic mis-detected loft_boss (LOFTED along Z
        # but WIDEST in X → sectioning along X read 'constant' and mislabeled it
        # a sweep-along-X). A genuine loft/sweep along axis K gives sections
        # perpendicular to K that are CLEAN single closed loops; a wrong axis
        # tends to fragment them (n_loops > 1) or fail to recover. So we score
        # each axis by section cleanliness (all single-loop) + section count,
        # recover stations along each viable axis, and pick the axis with the
        # most decisive, cleanest signal. The planner's hausdorff A/B
        # revert-guard still geometrically verifies the chosen op downstream.
        all_spans = [
            ("X", xmax - xmin, (1.0, 0.0, 0.0), 0, xmin),
            ("Y", ymax - ymin, (0.0, 1.0, 0.0), 1, ymin),
            ("Z", zmax - zmin, (0.0, 0.0, 1.0), 2, zmin),
        ]
        n = int(args.n_sections)
        fracs = [(i + 1) / (n + 1) for i in range(n)]

        def _stations_along(axis_dir, idx, axis_lo, span):
            """Recover the N perpendicular sections along one axis; returns
            (stations, all_single_loop)."""
            sts: list[dict[str, Any]] = []
            single = True
            for fr in fracs:
                along = axis_lo + fr * span
                origin = list(centroid)
                origin[idx] = along
                rec = _section_at(
                    shape, axis_dir, tuple(origin), args.simplify_tol_mm,
                )
                if rec is None:
                    continue
                if int(rec.get("n_loops") or 1) != 1:
                    single = False
                cxy = _section_center_xy(rec["sketch"])
                sts.append({
                    "sketch": rec["sketch"],
                    "z_along_axis_mm": round(along, 6),
                    "area_mm2": float(rec.get("area_mm2") or 0.0),
                    "center_xy": [round(cxy[0], 6), round(cxy[1], 6)],
                })
            return sts, single

        # Evaluate every axis with a meaningful span; keep the candidates.
        axis_candidates: list[dict[str, Any]] = []
        for axis_label, span, axis_dir, idx, axis_lo in all_spans:
            if span <= 1e-6:
                continue
            sts, single = _stations_along(axis_dir, idx, axis_lo, span)
            if len(sts) < 2:
                continue
            areas = [s["area_mm2"] for s in sts]
            a_mean = sum(areas) / len(areas)
            if a_mean <= 1e-9:
                continue
            sp = (max(areas) - min(areas)) / a_mean
            axis_candidates.append({
                "axis_label": axis_label, "axis_dir": axis_dir,
                "idx": idx, "axis_lo": axis_lo, "span": span,
                "stations": sts, "all_single_loop": single, "spread": sp,
            })

        diagnostics["axes_evaluated"] = [
            {"axis": c["axis_label"], "spread": round(c["spread"], 4),
             "single_loop": c["all_single_loop"], "n": len(c["stations"])}
            for c in axis_candidates
        ]

        if not axis_candidates:
            op["reason"] = "no axis yielded >=2 clean cross-sections — no loft/sweep."
            return self._result(body, op)

        # Pick the best axis: prefer CLEAN single-loop axes; among those, the
        # most DECISIVE signal — a near-constant sweep (low spread) or a clearly
        # varying loft (high spread). The "ambiguous middle" (spread near the
        # sweep_area_tol boundary) is the least trustworthy, so rank by distance
        # from that boundary. Single-loop axes always beat fragmented ones.
        tol = args.sweep_area_tol

        def _axis_key(c):
            decisiveness = abs(c["spread"] - tol)
            return (1 if c["all_single_loop"] else 0, decisiveness)

        best = max(axis_candidates, key=_axis_key)
        axis_label = best["axis_label"]
        axis_dir = best["axis_dir"]
        idx = best["idx"]
        axis_lo = best["axis_lo"]
        span = best["span"]
        stations = best["stations"]
        spread = best["spread"]
        diagnostics["stack_axis"] = axis_label
        diagnostics["stack_span_mm"] = round(span, 4)
        diagnostics["n_stations_recovered"] = len(stations)
        diagnostics["station_areas"] = [round(s["area_mm2"], 4) for s in stations]
        diagnostics["area_spread"] = round(spread, 4)

        # SWEEP: constant section + straight centroid path.
        if spread <= args.sweep_area_tol:
            conf = round(min(1.0, 1.0 - spread), 4)
            if conf >= args.min_confidence:
                p0 = list(centroid)
                p0[idx] = axis_lo
                p1 = list(centroid)
                p1[idx] = axis_lo + span
                op.update({
                    "kind": "sweep",
                    "confidence": conf,
                    "axis": {
                        "origin": [round(c, 6) for c in centroid],
                        "dir": [round(c, 6) for c in axis_dir],
                    },
                    "profile_sketch": stations[len(stations) // 2]["sketch"],
                    "path": {
                        "type": "polyline",
                        "points": [
                            [round(c, 6) for c in p0],
                            [round(c, 6) for c in p1],
                        ],
                    },
                    "reason": (
                        f"{len(stations)} sections along {axis_label} are "
                        f"constant (area spread {spread:.1%} ≤ "
                        f"{args.sweep_area_tol:.0%}) — constant-section sweep."
                    ),
                })
                return self._result(body, op)

        # LOFT: varying stacked sections.
        conf = round(min(1.0, 0.5 + min(spread, 0.5)), 4)
        if conf >= args.min_confidence:
            op.update({
                "kind": "loft",
                "confidence": conf,
                "axis": {
                    "origin": [round(c, 6) for c in centroid],
                    "dir": [round(c, 6) for c in axis_dir],
                },
                "section_sketches": [
                    {
                        "sketch": s["sketch"],
                        "z_along_axis_mm": s["z_along_axis_mm"],
                        "center_xy": s["center_xy"],
                    }
                    for s in stations
                ],
                "reason": (
                    f"{len(stations)} sections along {axis_label} VARY "
                    f"(area spread {spread:.1%} > {args.sweep_area_tol:.0%}) "
                    "— stacked loft."
                ),
            })
            return self._result(body, op)

        op["reason"] = (
            "no single generating op recovered with sufficient confidence — "
            "honest fallback (re-solidify / box)."
        )
        return self._result(body, op)

    def _result(self, body: Any, op: dict[str, Any]) -> SkillResult:
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"generating_op": op},
        )
