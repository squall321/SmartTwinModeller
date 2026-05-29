"""SketchSpec → 3D solid tool (cut 또는 union 용).

Phase 1: simple primitives via build123d (Box / Cylinder).
Phase 4: build a planar Face in local XY (z=0, normal=+Z) from any SketchSpec
using raw OCCT, optionally apply 2D vertex fillets, then `BRepPrimAPI_MakePrism`
to extrude it. Finally, transform the prism so the face base lies on the
target face and the prism axis points along the face normal (into/out).

Non-Z faces still raise NotImplementedError (consistent with Phase 1 scope).
"""
from __future__ import annotations

import math
from typing import Literal

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Plane
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_REVERSED, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_MapOfShape

from phone_designer.skills.modify_pocket._sketch import (
    ArcSegment,
    BSplineSketch,
    CircleSketch,
    CompositeSketch,
    EllipseSketch,
    LineSegment,
    LissajousCurveSketch,
    ParametricCurveSketch,
    PolarCurveSketch,
    PolygonSketch,
    RectangleSketch,
    RoundedRectSketch,
    SlotSketch,
    SplineSegment,
    SketchSpec,
)


# ───────────────────────────────────────────────────────────────────────────
# face-plane utilities (kept compatible with previous public API)
# ───────────────────────────────────────────────────────────────────────────


def _face_plane_normal(face) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """planar face 의 (center, outward_normal) 반환."""
    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Plane:
        raise NotImplementedError("non-planar face 위의 sketch 는 Phase 2 이후")
    pl = surf.Plane()
    n = pl.Axis().Direction()
    nx, ny, nz = n.X(), n.Y(), n.Z()
    if face.Orientation() == TopAbs_REVERSED:
        nx, ny, nz = -nx, -ny, -nz
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    c = props.CentreOfMass()
    return ((c.X(), c.Y(), c.Z()), (nx, ny, nz))


# ───────────────────────────────────────────────────────────────────────────
# wire builders — all live in local XY plane (z=0)
# ───────────────────────────────────────────────────────────────────────────


def _gp_pnt(x: float, y: float, z: float = 0.0):
    from OCP.gp import gp_Pnt
    return gp_Pnt(x, y, z)


def _edge_line(p1, p2):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    me = BRepBuilderAPI_MakeEdge(p1, p2)
    if not me.IsDone():
        raise RuntimeError("MakeEdge(line) failed")
    return me.Edge()


def _edge_arc_through(p1, p2, p3):
    """Arc through three points (start, middle, end)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GC import GC_MakeArcOfCircle
    mk = GC_MakeArcOfCircle(p1, p2, p3)
    if not mk.IsDone():
        raise RuntimeError("GC_MakeArcOfCircle(3p) failed")
    me = BRepBuilderAPI_MakeEdge(mk.Value())
    if not me.IsDone():
        raise RuntimeError("MakeEdge(arc-3p) failed")
    return me.Edge()


def _edge_arc_radius(start_xy, end_xy, radius: float, ccw: bool):
    """Arc from start→end on circle of given radius, direction by `ccw`."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    sx, sy = start_xy
    ex, ey = end_xy
    mx, my = (sx + ex) / 2.0, (sy + ey) / 2.0
    chord = math.hypot(ex - sx, ey - sy)
    if chord < 1e-9:
        raise ValueError("arc endpoints coincide")
    if chord > 2 * radius:
        raise ValueError(f"arc chord {chord} > 2*radius {2*radius}")
    # Perp from midpoint (rotate (dx,dy)→(-dy,dx))
    dx, dy = (ex - sx) / chord, (ey - sy) / chord
    h = math.sqrt(max(0.0, radius * radius - (chord / 2.0) ** 2))
    # Two candidate centers; ccw choose center on left of chord direction
    cx_left = mx - dy * h
    cy_left = my + dx * h
    cx_right = mx + dy * h
    cy_right = my - dx * h
    if ccw:
        cx, cy = cx_left, cy_left
    else:
        cx, cy = cx_right, cy_right
    axis = gp_Ax2(gp_Pnt(cx, cy, 0.0), gp_Dir(0, 0, 1))
    circ = gp_Circ(axis, radius)
    mk = GC_MakeArcOfCircle(circ, gp_Pnt(sx, sy, 0), gp_Pnt(ex, ey, 0), ccw)
    if not mk.IsDone():
        raise RuntimeError("GC_MakeArcOfCircle(circle,p1,p2,sense) failed")
    me = BRepBuilderAPI_MakeEdge(mk.Value())
    if not me.IsDone():
        raise RuntimeError("MakeEdge(arc-radius) failed")
    return me.Edge()


def _edge_circle(cx: float, cy: float, r: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt
    axis = gp_Ax2(gp_Pnt(cx, cy, 0.0), gp_Dir(0, 0, 1))
    circ = gp_Circ(axis, r)
    me = BRepBuilderAPI_MakeEdge(circ)
    if not me.IsDone():
        raise RuntimeError("MakeEdge(circle) failed")
    return me.Edge()


def _wire_from_edges(edges):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    mw = BRepBuilderAPI_MakeWire()
    for e in edges:
        mw.Add(e)
    if not mw.IsDone():
        raise RuntimeError("MakeWire failed")
    return mw.Wire()


def _face_from_wire(wire):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Pln, gp_Pnt, gp_Dir, gp_Ax3
    pl = gp_Pln(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)))
    mf = BRepBuilderAPI_MakeFace(pl, wire, True)
    if not mf.IsDone():
        raise RuntimeError("MakeFace failed")
    return mf.Face()


def _apply_vertex_fillets(face, radius: float):
    """Apply 2D fillet of `radius` to every vertex of the face's outer wire.

    Edges that the 2D filleter rejects (radius too large, non-line/arc) are
    skipped silently — partial fillet is better than zero.
    """
    if radius <= 0:
        return face
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet2d
    maker = BRepFilletAPI_MakeFillet2d(face)
    # Collect unique vertices of the face.
    seen = TopTools_MapOfShape()
    vertices = []
    it = TopExp_Explorer(face, TopAbs_VERTEX)
    while it.More():
        v = it.Current()
        if not seen.Contains(v):
            seen.Add(v)
            vertices.append(TopoDS.Vertex_s(v))
        it.Next()
    added = 0
    for v in vertices:
        try:
            maker.AddFillet(v, radius)
            added += 1
        except Exception:
            continue
    maker.Build()
    if added == 0 or not maker.IsDone():
        return face   # no fillets applied — return original face
    return TopoDS.Face_s(maker.Shape())


# ── per-sketch wire builders ────────────────────────────────────────────────


def _wire_circle(s: CircleSketch):
    e = _edge_circle(s.center_x_mm, s.center_y_mm, s.diameter_mm / 2.0)
    return _wire_from_edges([e])


def _wire_rectangle(s: RectangleSketch):
    cx, cy = s.center_x_mm, s.center_y_mm
    hl, hw = s.length_mm / 2.0, s.width_mm / 2.0
    p1 = _gp_pnt(cx - hl, cy - hw)
    p2 = _gp_pnt(cx + hl, cy - hw)
    p3 = _gp_pnt(cx + hl, cy + hw)
    p4 = _gp_pnt(cx - hl, cy + hw)
    edges = [
        _edge_line(p1, p2),
        _edge_line(p2, p3),
        _edge_line(p3, p4),
        _edge_line(p4, p1),
    ]
    return _wire_from_edges(edges)


def _wire_polygon(s: PolygonSketch):
    cx, cy = s.center_x_mm, s.center_y_mm
    pts = [_gp_pnt(x + cx, y + cy) for (x, y) in s.vertices]
    edges = []
    for i in range(len(pts)):
        edges.append(_edge_line(pts[i], pts[(i + 1) % len(pts)]))
    return _wire_from_edges(edges)


def _wire_slot(s: SlotSketch):
    """Slot wire: two semicircles + two lines.

    Local coordinates: slot along X, then rotated by rotation_deg around (cx,cy).
    """
    half_inner = (s.length_mm - s.width_mm) / 2.0
    r = s.width_mm / 2.0
    # Local (un-rotated, un-translated) key points:
    #   left center: (-half_inner, 0), right center: (+half_inner, 0)
    #   line top: (-half_inner, +r) → (+half_inner, +r)
    #   line bot: (+half_inner, -r) → (-half_inner, -r)
    # Right semicircle: from (+half_inner, +r) through (+half_inner+r, 0) to (+half_inner, -r)
    # Left semicircle:  from (-half_inner, -r) through (-half_inner-r, 0) to (-half_inner, +r)
    pts_local = [
        (-half_inner, +r),
        (+half_inner, +r),
        (+half_inner + r, 0.0),
        (+half_inner, -r),
        (-half_inner, -r),
        (-half_inner - r, 0.0),
    ]
    # rotate + translate
    theta = math.radians(s.rotation_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    def xf(p):
        x, y = p
        rx = cos_t * x - sin_t * y + s.center_x_mm
        ry = sin_t * x + cos_t * y + s.center_y_mm
        return _gp_pnt(rx, ry)

    P_tl, P_tr, P_rm, P_br, P_bl, P_lm = [xf(p) for p in pts_local]
    edges = [
        _edge_line(P_tl, P_tr),                  # top line
        _edge_arc_through(P_tr, P_rm, P_br),      # right semicircle
        _edge_line(P_br, P_bl),                  # bottom line
        _edge_arc_through(P_bl, P_lm, P_tl),      # left semicircle
    ]
    return _wire_from_edges(edges)


def _wire_bspline_closed(s: BSplineSketch):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.TColgp import TColgp_HArray1OfPnt

    n = len(s.fit_points)
    cx, cy = s.center_x_mm, s.center_y_mm
    arr = TColgp_HArray1OfPnt(1, n)
    for i, (x, y) in enumerate(s.fit_points, start=1):
        arr.SetValue(i, _gp_pnt(x + cx, y + cy))
    interp = GeomAPI_Interpolate(arr, True, 1e-6)  # periodic=True for closed loop
    interp.Perform()
    if not interp.IsDone():
        raise RuntimeError("GeomAPI_Interpolate (closed) failed")
    curve = interp.Curve()
    me = BRepBuilderAPI_MakeEdge(curve)
    if not me.IsDone():
        raise RuntimeError("MakeEdge(bspline_closed) failed")
    return _wire_from_edges([me.Edge()])


def _wire_bspline_open(points: list[tuple[float, float]]):
    """Open interpolating B-spline through `points` (no periodic flag)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.TColgp import TColgp_HArray1OfPnt

    n = len(points)
    arr = TColgp_HArray1OfPnt(1, n)
    for i, (x, y) in enumerate(points, start=1):
        arr.SetValue(i, _gp_pnt(x, y))
    interp = GeomAPI_Interpolate(arr, False, 1e-6)
    interp.Perform()
    if not interp.IsDone():
        raise RuntimeError("GeomAPI_Interpolate (open) failed")
    curve = interp.Curve()
    me = BRepBuilderAPI_MakeEdge(curve)
    if not me.IsDone():
        raise RuntimeError("MakeEdge(bspline_open) failed")
    return me.Edge()


def _wire_ellipse(s: EllipseSketch):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GC import GC_MakeEllipse
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    # OCCT gp_Elips needs major ≥ minor — swap and rotate if not.
    rx, ry = s.radius_x_mm, s.radius_y_mm
    rot = s.rotation_deg
    if ry > rx:
        rx, ry = ry, rx
        rot += 90.0
    theta = math.radians(rot)
    # X-direction of ellipse axes
    xdir = gp_Dir(math.cos(theta), math.sin(theta), 0.0)
    ax = gp_Ax2(gp_Pnt(s.center_x_mm, s.center_y_mm, 0.0), gp_Dir(0, 0, 1), xdir)
    mk = GC_MakeEllipse(ax, rx, ry)
    if not mk.IsDone():
        raise RuntimeError("GC_MakeEllipse failed")
    me = BRepBuilderAPI_MakeEdge(mk.Value())
    if not me.IsDone():
        raise RuntimeError("MakeEdge(ellipse) failed")
    return _wire_from_edges([me.Edge()])


# ── analytical curve helpers (Phase 4 curved-area) ──────────────────────────


_ALLOWED_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "exp": math.exp, "sqrt": math.sqrt, "abs": abs,
}
_ALLOWED_NAMES = {"pi": math.pi, "e": math.e}


def _safe_eval_curve(expr: str, var: str, value: float) -> float:
    """Evaluate `expr` (a simpleeval expression) with `var`=value bound.

    Whitelisted funcs: sin/cos/tan/exp/sqrt/abs. Names: pi, e.
    Defensive: reject expressions containing '__' (double underscore).
    """
    if "__" in expr:
        raise ValueError(f"expression contains forbidden '__': {expr!r}")
    from simpleeval import SimpleEval
    ev = SimpleEval(functions=dict(_ALLOWED_FUNCS))
    ev.names = dict(_ALLOWED_NAMES)
    ev.names[var] = value
    try:
        return float(ev.eval(expr))
    except Exception as e:
        raise ValueError(
            f"safe_eval failed for expr={expr!r} {var}={value}: {e}"
        ) from e


def _interp_closed_spline_edge(points_xy: list[tuple[float, float]],
                               periodic: bool = True,
                               tol: float = 1e-4):
    """Sampled XY points → interpolated B-spline edge (periodic if requested).

    For periodic interpolation the first and last input points should NOT
    coincide — GeomAPI_Interpolate auto-closes the loop. If they coincide we
    drop the final duplicate.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.TColgp import TColgp_HArray1OfPnt

    pts = list(points_xy)
    if periodic and len(pts) >= 2:
        ax, ay = pts[0]
        bx, by = pts[-1]
        if math.hypot(ax - bx, ay - by) < 1e-7:
            pts = pts[:-1]
    if len(pts) < 3:
        raise RuntimeError("interp_closed_spline: need ≥3 distinct points")

    arr = TColgp_HArray1OfPnt(1, len(pts))
    for i, (x, y) in enumerate(pts, start=1):
        arr.SetValue(i, _gp_pnt(x, y))
    interp = GeomAPI_Interpolate(arr, periodic, tol)
    interp.Perform()
    if not interp.IsDone():
        raise RuntimeError(
            f"GeomAPI_Interpolate (periodic={periodic}) failed for "
            f"{len(pts)} points"
        )
    curve = interp.Curve()
    me = BRepBuilderAPI_MakeEdge(curve)
    if not me.IsDone():
        raise RuntimeError("MakeEdge from interpolated curve failed")
    return me.Edge()


def _wire_parametric(s: ParametricCurveSketch):
    cx, cy = s.center_x_mm, s.center_y_mm
    n = int(s.samples)
    # For closed periodic spline, sample t in [t_min, t_max) (exclusive end);
    # for open, sample inclusive.
    pts: list[tuple[float, float]] = []
    if s.close_curve:
        step = (s.t_max - s.t_min) / n
        for i in range(n):
            t = s.t_min + i * step
            x = _safe_eval_curve(s.x_expr, "t", t)
            y = _safe_eval_curve(s.y_expr, "t", t)
            pts.append((x + cx, y + cy))
    else:
        step = (s.t_max - s.t_min) / (n - 1)
        for i in range(n):
            t = s.t_min + i * step
            x = _safe_eval_curve(s.x_expr, "t", t)
            y = _safe_eval_curve(s.y_expr, "t", t)
            pts.append((x + cx, y + cy))
        # Force-close by appending start point — periodic spline will treat
        # the curve as a loop.
        pts.append(pts[0])
    edge = _interp_closed_spline_edge(pts, periodic=True, tol=1e-4)
    return _wire_from_edges([edge])


def _wire_polar(s: PolarCurveSketch):
    cx, cy = s.center_x_mm, s.center_y_mm
    n = int(s.samples)
    step = (s.theta_max - s.theta_min) / n  # exclusive end for periodic
    pts: list[tuple[float, float]] = []
    for i in range(n):
        theta = s.theta_min + i * step
        r = _safe_eval_curve(s.r_expr, "theta", theta)
        x = r * math.cos(theta) + cx
        y = r * math.sin(theta) + cy
        pts.append((x, y))
    edge = _interp_closed_spline_edge(pts, periodic=True, tol=1e-4)
    return _wire_from_edges([edge])


def _wire_lissajous(s: LissajousCurveSketch):
    cx, cy = s.center_x_mm, s.center_y_mm
    n = int(s.samples)
    phase = math.radians(s.phase_deg)
    step = (2.0 * math.pi) / n  # exclusive end for periodic
    pts: list[tuple[float, float]] = []
    for i in range(n):
        t = i * step
        x = s.amp_x * math.sin(s.freq_x * t + phase) + cx
        y = s.amp_y * math.sin(s.freq_y * t) + cy
        pts.append((x, y))
    edge = _interp_closed_spline_edge(pts, periodic=True, tol=1e-4)
    return _wire_from_edges([edge])


def _wire_composite(s: CompositeSketch):
    cx, cy = s.center_x_mm, s.center_y_mm
    edges = []
    for seg in s.segments:
        if isinstance(seg, LineSegment):
            p1 = _gp_pnt(seg.start[0] + cx, seg.start[1] + cy)
            p2 = _gp_pnt(seg.end[0] + cx, seg.end[1] + cy)
            edges.append(_edge_line(p1, p2))
        elif isinstance(seg, ArcSegment):
            edges.append(_edge_arc_radius(
                (seg.start[0] + cx, seg.start[1] + cy),
                (seg.end[0] + cx, seg.end[1] + cy),
                seg.radius, seg.ccw,
            ))
        elif isinstance(seg, SplineSegment):
            pts = [(p[0] + cx, p[1] + cy) for p in seg.points]
            edges.append(_wire_bspline_open(pts))
        else:
            raise NotImplementedError(f"composite segment {type(seg).__name__}")
    return _wire_from_edges(edges)


# ── face builder dispatch ───────────────────────────────────────────────────


def _build_planar_face(sketch):
    """Build a planar TopoDS_Face from `sketch` in local XY (z=0, normal=+Z)."""
    if isinstance(sketch, CircleSketch):
        return _face_from_wire(_wire_circle(sketch))

    if isinstance(sketch, RectangleSketch):
        return _face_from_wire(_wire_rectangle(sketch))

    if isinstance(sketch, RoundedRectSketch):
        # Build base rectangle, then apply uniform 2D fillet to corners.
        base = _face_from_wire(_wire_rectangle(
            RectangleSketch(
                length_mm=sketch.length_mm,
                width_mm=sketch.width_mm,
                center_x_mm=sketch.center_x_mm,
                center_y_mm=sketch.center_y_mm,
            )
        ))
        return _apply_vertex_fillets(base, sketch.corner_r_mm)

    if isinstance(sketch, PolygonSketch):
        base = _face_from_wire(_wire_polygon(sketch))
        if sketch.corner_fillet_r > 0:
            return _apply_vertex_fillets(base, sketch.corner_fillet_r)
        return base

    if isinstance(sketch, SlotSketch):
        return _face_from_wire(_wire_slot(sketch))

    if isinstance(sketch, BSplineSketch):
        return _face_from_wire(_wire_bspline_closed(sketch))

    if isinstance(sketch, EllipseSketch):
        return _face_from_wire(_wire_ellipse(sketch))

    if isinstance(sketch, CompositeSketch):
        base = _face_from_wire(_wire_composite(sketch))
        if sketch.corner_fillet_r > 0:
            return _apply_vertex_fillets(base, sketch.corner_fillet_r)
        return base

    if isinstance(sketch, ParametricCurveSketch):
        return _face_from_wire(_wire_parametric(sketch))

    if isinstance(sketch, PolarCurveSketch):
        return _face_from_wire(_wire_polar(sketch))

    if isinstance(sketch, LissajousCurveSketch):
        return _face_from_wire(_wire_lissajous(sketch))

    raise NotImplementedError(f"sketch kind {type(sketch).__name__}")


def _build_planar_wire(sketch):
    """Build a TopoDS_Wire in local XY (z=0) from a SketchSpec — used for sweep paths.

    Note: this may be an OPEN wire — caller must know whether the sketch is
    actually open or closed.
    """
    if isinstance(sketch, CircleSketch):
        return _wire_circle(sketch)
    if isinstance(sketch, RectangleSketch):
        return _wire_rectangle(sketch)
    if isinstance(sketch, PolygonSketch):
        return _wire_polygon(sketch)
    if isinstance(sketch, SlotSketch):
        return _wire_slot(sketch)
    if isinstance(sketch, BSplineSketch):
        return _wire_bspline_closed(sketch)
    if isinstance(sketch, EllipseSketch):
        return _wire_ellipse(sketch)
    if isinstance(sketch, CompositeSketch):
        return _wire_composite(sketch)
    if isinstance(sketch, RoundedRectSketch):
        # Just give the rectangle wire; rounded variant is via filleted face.
        return _wire_rectangle(RectangleSketch(
            length_mm=sketch.length_mm,
            width_mm=sketch.width_mm,
            center_x_mm=sketch.center_x_mm,
            center_y_mm=sketch.center_y_mm,
        ))
    if isinstance(sketch, ParametricCurveSketch):
        return _wire_parametric(sketch)
    if isinstance(sketch, PolarCurveSketch):
        return _wire_polar(sketch)
    if isinstance(sketch, LissajousCurveSketch):
        return _wire_lissajous(sketch)
    raise NotImplementedError(f"sketch kind {type(sketch).__name__}")


# ───────────────────────────────────────────────────────────────────────────
# extrude + place
# ───────────────────────────────────────────────────────────────────────────


def _extrude_face_along_z(face, length_mm: float, direction_sign: int = 1):
    """Extrude `face` along ±Z by `length_mm`. Returns a TopoDS_Shape (solid)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec
    vec = gp_Vec(0.0, 0.0, direction_sign * length_mm)
    prism = BRepPrimAPI_MakePrism(face, vec, False, True)
    if not prism.IsDone():
        raise RuntimeError("MakePrism failed")
    return prism.Shape()


def _translate_shape(shape, dx: float, dy: float, dz: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(dx, dy, dz))
    xf = BRepBuilderAPI_Transform(shape, t, True)
    xf.Build()
    return xf.Shape()


def build_pocket_tool(
    face,
    sketch,
    depth_mm: float,
    direction: Literal["into", "out"] = "into",
):
    """face 의 normal 방향으로 sketch 를 extrude 해서 cut/union 용 solid 반환.

    direction = "into": face 의 normal 반대 (face 안쪽으로 파냄). pocket / hole 용.
    direction = "out":  face 의 normal 방향 (외부로 돌출). plateau / bump 용.

    구현: 현재는 face normal 이 ±Z 인 경우만 지원. 다른 경우 NotImplementedError.
    """
    center, normal = _face_plane_normal(face)
    nz = normal[2]
    if abs(nz) <= 0.95:
        raise NotImplementedError(
            "non-Z face 위의 pocket/plateau 는 Phase 2 이후 (face-local coord 변환 필요)"
        )

    # 1. build the planar face in local XY (z=0, normal=+Z)
    planar_face = _build_planar_face(sketch)

    # 2. extrude along +Z by depth — prism sits in z ∈ [0, depth]
    prism = _extrude_face_along_z(planar_face, depth_mm, direction_sign=+1)

    # 3. translate prism into world position.
    #    base sketch lives at z=0 +Z extruded. We want the BASE of the extrusion to
    #    coincide with `center` (the face center), and the prism to grow into/out.
    #    - top face (nz > 0), direction=into  → prism grows -Z from face → translate by (cx, cy, cz - depth) and extrude was +Z, so we need to flip.
    #
    # Simpler: extrude in the right sign based on case so that the prism is already in correct world Z.
    # We'll re-extrude:
    if direction == "into" and nz > 0:
        # face is +Z top, pocket grows -Z. Place prism base at z=center_z, top at center_z - depth
        prism = _extrude_face_along_z(planar_face, depth_mm, direction_sign=-1)
        dx, dy, dz = center[0], center[1], center[2]
    elif direction == "into" and nz < 0:
        # face is -Z bottom, pocket grows +Z. prism base at center_z, top at center_z + depth
        prism = _extrude_face_along_z(planar_face, depth_mm, direction_sign=+1)
        dx, dy, dz = center[0], center[1], center[2]
    elif direction == "out" and nz > 0:
        # face is +Z top, plateau grows +Z. prism base at center_z, top at center_z + depth
        prism = _extrude_face_along_z(planar_face, depth_mm, direction_sign=+1)
        dx, dy, dz = center[0], center[1], center[2]
    elif direction == "out" and nz < 0:
        # face is -Z bottom, plateau grows -Z. prism base at center_z, top at center_z - depth
        prism = _extrude_face_along_z(planar_face, depth_mm, direction_sign=-1)
        dx, dy, dz = center[0], center[1], center[2]
    else:
        raise NotImplementedError("unreachable")

    return _translate_shape(prism, dx, dy, dz)
