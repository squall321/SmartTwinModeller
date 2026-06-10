"""measure_thread_pitch — atomic, read-only.

Measure the pitch + handedness of a (real, modeled) helical thread around a
given axis. Plan item A6 (2026-06-10).

Algorithm:
    1. Collect candidate faces — bspline / bezier / surface-of-revolution /
       extrusion / other (i.e. the non-analytic kinds a swept V-thread or a
       STEP-imported helical flank shows up as) whose bbox-center sits within
       ``search_radius_mm`` of the axis LINE. Analytic cylinders / planes /
       cones / tori are never thread flanks and are skipped.
    2. For every boundary edge of a candidate face, sample ~50 points along
       the curve (BRepAdaptor_Curve) and unwrap each point into cylinder
       coordinates around the axis:
           z     = (p - origin) · w            (w = unit axis_dir)
           theta = atan2((p-origin)·v, (p-origin)·u)   ((u,v,w) right-handed)
       theta is unwrapped sequentially across the ±pi jumps so a multi-turn
       helix edge becomes a monotonic theta ramp.
    3. Per edge, least-squares fit  z = slope·theta + c.  A true helix edge
       has slope = pitch/(2*pi) with tiny residual; a circle (cosmetic thread
       ring, knurl rim) has slope ≈ 0; an axial line (knurl groove wall) has
       near-zero theta span and is rejected outright.
    4. Aggregate over edges: pitch_mm = |median slope|·2*pi, handedness from
       the median slope's sign (positive in a right-handed frame = right-hand
       thread — chirality is frame-independent for ±axis), confidence from
       the residuals + slope consistency (robust MAD spread).

Knurl / spline / cosmetic-thread negative: near-zero median slope, wild
residual, or no usable edges at all -> ``is_thread: false``.

extras schema:
    {"thread_measurement": {
        "is_thread": bool,
        "pitch_mm": float | None,        # |median slope| * 2*pi
        "handedness": "right"|"left"|None,
        "confidence": float,             # 0..1
        "n_edges_used": int
     }}

body 는 변경하지 않는다 (post ``body_present``).
"""
from __future__ import annotations

import math
import statistics
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# Surface kinds a real (modeled) thread flank can present as. Analytic
# cylinder/plane/cone are never flanks; torus is excluded on purpose — torus
# faces near a hole rim are fillet blends whose circular edges would drag the
# median slope toward 0 on an otherwise genuine thread.
_CANDIDATE_KINDS = frozenset({"bspline", "bezier", "revolution", "extrusion", "other"})

#: max boundary edges sampled across all candidate faces (runtime bound).
_MAX_EDGES = 200

#: points sampled per edge curve.
_SAMPLES_PER_EDGE = 50

#: minimum unwrapped theta span (radians) for an edge to count as helical
#: evidence. Axial lines (knurl groove walls) and profile seams span ~0.
_MIN_THETA_SPAN_RAD = 0.8

#: below this |pitch| the "helix" is a circle — cosmetic ring, not a thread.
_MIN_PITCH_MM = 0.02


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _surface_kind(face) -> str:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import (
        GeomAbs_BezierSurface,
        GeomAbs_BSplineSurface,
        GeomAbs_Cone,
        GeomAbs_Cylinder,
        GeomAbs_Plane,
        GeomAbs_Sphere,
        GeomAbs_SurfaceOfExtrusion,
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
    if t == GeomAbs_BezierSurface:
        return "bezier"
    if t == GeomAbs_SurfaceOfRevolution:
        return "revolution"
    if t == GeomAbs_SurfaceOfExtrusion:
        return "extrusion"
    return "other"


def _frame_from_axis(axis_dir) -> tuple[tuple, tuple, tuple]:
    """Right-handed orthonormal frame (u, v, w) with w along axis_dir."""
    wx, wy, wz = float(axis_dir[0]), float(axis_dir[1]), float(axis_dir[2])
    mag = math.sqrt(wx * wx + wy * wy + wz * wz)
    if mag < 1e-12:
        raise ValueError("measure_thread_pitch: axis_dir is zero")
    w = (wx / mag, wy / mag, wz / mag)
    # Reference = global axis least aligned with w.
    aw = (abs(w[0]), abs(w[1]), abs(w[2]))
    if aw[0] <= aw[1] and aw[0] <= aw[2]:
        ref = (1.0, 0.0, 0.0)
    elif aw[1] <= aw[2]:
        ref = (0.0, 1.0, 0.0)
    else:
        ref = (0.0, 0.0, 1.0)
    # u = normalize(ref × w); v = w × u  →  u × v = w (right-handed).
    ux = ref[1] * w[2] - ref[2] * w[1]
    uy = ref[2] * w[0] - ref[0] * w[2]
    uz = ref[0] * w[1] - ref[1] * w[0]
    um = math.sqrt(ux * ux + uy * uy + uz * uz)
    u = (ux / um, uy / um, uz / um)
    v = (
        w[1] * u[2] - w[2] * u[1],
        w[2] * u[0] - w[0] * u[2],
        w[0] * u[1] - w[1] * u[0],
    )
    return u, v, w


def _face_bbox_center(face):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(face, bb)
    except Exception:
        try:
            BRepBndLib.Add_s(face, bb)
        except Exception:
            return None
    if bb.IsVoid():
        return None
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return (0.5 * (xmin + xmax), 0.5 * (ymin + ymax), 0.5 * (zmin + zmax))


def _dist_point_to_axis_line(p, origin, w) -> float:
    vx = p[0] - origin[0]
    vy = p[1] - origin[1]
    vz = p[2] - origin[2]
    cx = vy * w[2] - vz * w[1]
    cy = vz * w[0] - vx * w[2]
    cz = vx * w[1] - vy * w[0]
    return math.sqrt(cx * cx + cy * cy + cz * cz)


def _fit_edge_helix(edge, origin, u, v, w):
    """Sample one boundary edge, unwrap to (theta, z) and least-squares fit
    z = slope·theta + c.

    Returns ``(slope, residual_rms, theta_span)`` or ``None`` when the edge
    carries no helical evidence (too few off-axis points, or theta span
    below ``_MIN_THETA_SPAN_RAD`` — axial lines, profile seams).
    """
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    try:
        curve = BRepAdaptor_Curve(edge)
        t0 = curve.FirstParameter()
        t1 = curve.LastParameter()
    except Exception:
        return None
    if not (math.isfinite(t0) and math.isfinite(t1)) or t1 <= t0:
        return None

    thetas: list[float] = []
    zs: list[float] = []
    prev: float | None = None
    n = _SAMPLES_PER_EDGE
    for i in range(n):
        t = t0 + (t1 - t0) * i / (n - 1)
        try:
            p = curve.Value(t)
        except Exception:
            continue
        qx = p.X() - origin[0]
        qy = p.Y() - origin[1]
        qz = p.Z() - origin[2]
        z = qx * w[0] + qy * w[1] + qz * w[2]
        x = qx * u[0] + qy * u[1] + qz * u[2]
        y = qx * v[0] + qy * v[1] + qz * v[2]
        if math.hypot(x, y) < 1e-6:
            continue  # on the axis — theta undefined
        th = math.atan2(y, x)
        if prev is not None:
            # Unwrap the 2*pi jumps — pick the branch closest to the
            # previous sample (valid while per-sample delta < pi, i.e.
            # < ~25 turns per edge at 50 samples).
            while th - prev > math.pi:
                th -= 2.0 * math.pi
            while th - prev < -math.pi:
                th += 2.0 * math.pi
        prev = th
        thetas.append(th)
        zs.append(z)

    if len(thetas) < 8:
        return None
    span = max(thetas) - min(thetas)
    if span < _MIN_THETA_SPAN_RAD:
        return None

    m = len(thetas)
    mean_t = sum(thetas) / m
    mean_z = sum(zs) / m
    var_t = sum((t - mean_t) ** 2 for t in thetas)
    if var_t < 1e-12:
        return None
    cov = sum((t - mean_t) * (z - mean_z) for t, z in zip(thetas, zs))
    slope = cov / var_t
    c0 = mean_z - slope * mean_t
    residual_rms = math.sqrt(
        sum((z - (slope * t + c0)) ** 2 for t, z in zip(thetas, zs)) / m
    )
    return slope, residual_rms, span


def _measure_thread(shape, axis_origin, axis_dir, search_radius_mm: float) -> dict:
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_MapOfShape

    origin = (float(axis_origin[0]), float(axis_origin[1]), float(axis_origin[2]))
    u, v, w = _frame_from_axis(axis_dir)

    no_thread = {
        "is_thread": False,
        "pitch_mm": None,
        "handedness": None,
        "confidence": 0.0,
        "n_edges_used": 0,
    }

    fits: list[tuple[float, float, float]] = []
    seen_edges = TopTools_MapOfShape()
    fexp = TopExp_Explorer(shape, TopAbs_FACE)
    while fexp.More() and len(fits) < _MAX_EDGES:
        face = TopoDS.Face_s(fexp.Current())
        fexp.Next()
        if _surface_kind(face) not in _CANDIDATE_KINDS:
            continue
        center = _face_bbox_center(face)
        if center is None:
            continue
        if _dist_point_to_axis_line(center, origin, w) > search_radius_mm:
            continue
        eexp = TopExp_Explorer(face, TopAbs_EDGE)
        while eexp.More() and len(fits) < _MAX_EDGES:
            edge = TopoDS.Edge_s(eexp.Current())
            eexp.Next()
            if not seen_edges.Add(edge):
                continue  # shared with an already-sampled candidate face
            out = _fit_edge_helix(edge, origin, u, v, w)
            if out is not None:
                fits.append(out)

    if len(fits) < 2:
        # 0–1 usable helical edges — plain bore, knurl (axial grooves all
        # rejected by the theta-span gate), or nothing near the axis.
        no_thread["n_edges_used"] = len(fits)
        return no_thread

    slopes = [f[0] for f in fits]
    residuals = [f[1] for f in fits]
    med_slope = statistics.median(slopes)
    pitch_mm = abs(med_slope) * 2.0 * math.pi

    if pitch_mm < _MIN_PITCH_MM:
        # Circles of revolution (cosmetic thread rings, spline/knurl rims).
        no_thread["n_edges_used"] = len(fits)
        return no_thread

    # Robust spread of slopes (MAD) relative to the median slope, and
    # median residual relative to the pitch.
    mad = statistics.median(abs(s - med_slope) for s in slopes)
    rel_spread = mad / max(abs(med_slope), 1e-9)
    res_rel = statistics.median(residuals) / max(pitch_mm, 1e-9)

    confidence = 1.0 / (1.0 + 4.0 * rel_spread + 6.0 * res_rel)
    confidence = max(0.0, min(1.0, confidence))

    is_thread = rel_spread <= 0.6 and res_rel <= 0.5 and confidence >= 0.3
    return {
        "is_thread": bool(is_thread),
        "pitch_mm": round(pitch_mm, 5),
        "handedness": ("right" if med_slope > 0.0 else "left") if is_thread else None,
        "confidence": round(confidence, 3),
        "n_edges_used": len(fits),
    }


@skill(
    name="measure_thread_pitch",
    category="inspect",
    level="atomic",
    summary="Measure a modeled helical thread around a given axis: sample the "
            "boundary edges of bspline/revolution faces near the axis, unwrap "
            "to cylinder coordinates, least-squares fit z vs theta per edge, "
            "and report pitch (median slope × 2π), handedness, and a "
            "residual/consistency confidence. Cosmetic threads, knurls and "
            "plain bores report is_thread=false. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["thread_measurement"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class MeasureThreadPitch(SkillBase):
    class Args(BaseModel):
        axis_origin: list[float] = Field(
            min_length=3, max_length=3,
            description="A point on the thread axis (world mm).",
        )
        axis_dir: list[float] = Field(
            min_length=3, max_length=3,
            description="Thread axis direction (normalized internally).",
        )
        search_radius_mm: float = Field(
            gt=0.0, le=1000.0,
            description="Collect candidate (bspline/revolution/…) faces whose "
                        "bbox center lies within this distance of the axis line.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        shape = _occt_shape(body)
        measurement = _measure_thread(
            shape, args.axis_origin, args.axis_dir, args.search_radius_mm
        )
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"thread_measurement": measurement},
        )
