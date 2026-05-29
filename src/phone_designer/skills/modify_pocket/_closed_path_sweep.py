"""Closed-path seal-groove helper — `build_closed_groove`.

Used by the closed-loop seal-groove skills:
    - o_ring_groove_path
    - foam_seal_groove_racetrack
    - (any future closed-loop face-seal groove)

Approach (literal recipe — see Pack PR notes):
    1. ``path_points`` is the CENTERLINE polyline of the desired groove, in
       the host face's local XY frame (z=0 = face surface, +Z = outward
       normal).
    2. Build a closed polyline wire from ``path_points`` at z = face center z.
       This is the "centerline wire".
    3. 2D-offset the centerline wire OUTWARD by ``+width/2`` → outer wire.
    4. 2D-offset the centerline wire INWARD  by ``-width/2`` → inner wire.
       This is the "shrink" step: the inner wire is the centerline shrunk by
       (width/2), and (outer − inner) yields a closed RING face of band
       width = ``width_mm`` straddling the path.
    5. Build planar faces from outer/inner wires and take the boolean
       difference (outer − inner) → planar ring face.
    6. Extrude the ring face INWARD along the face's inward normal by
       ``depth_mm`` → solid TopoDS cutter.

The helper assumes the host face is planar with normal along ±Z (matching the
existing axial face-seal skills in this package). Non-Z planar faces raise
``NotImplementedError`` — adding rotation support is a separate refactor.

Why this approach (vs. ``BRepOffsetAPI_MakePipe``):
    - A 2D wire offset is mathematically stable for polylines with arbitrary
      convex / concave corners (rounded by GeomAbs_Arc joins).
    - Building the ring as a planar boolean keeps everything in one plane,
      which lets us extrude in a single direction with ``BRepPrimAPI_MakePrism``
      — no sweep-frame ambiguity.
    - The resulting cutter is a clean closed solid (no self-intersection at
      sharp inner corners, which sweeps can produce when the radius of
      curvature is smaller than the profile size).

Returns:
    TopoDS_Shape (solid) in WORLD coordinates, ready to feed into
    ``BRepAlgoAPI_Cut(body, cutter)``.
"""
from __future__ import annotations


def _gp_pnt(x: float, y: float, z: float = 0.0):
    from OCP.gp import gp_Pnt
    return gp_Pnt(x, y, z)


def _edge_line(p1, p2):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    me = BRepBuilderAPI_MakeEdge(p1, p2)
    if not me.IsDone():
        raise RuntimeError("_closed_path_sweep: MakeEdge(line) failed")
    return me.Edge()


def _build_closed_polyline_wire(
    path_points: list[tuple[float, float]],
    z: float,
):
    """Build a closed polyline TopoDS_Wire at plane z=`z`.

    Coincident consecutive points are dropped. If the last point doesn't
    equal the first, an implicit closing edge is added.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire

    if len(path_points) < 2:
        raise ValueError(
            "_closed_path_sweep: path_points must contain >= 2 points"
        )
    pts = [_gp_pnt(x, y, z) for (x, y) in path_points]
    if pts[-1].Distance(pts[0]) > 1e-7:
        pts.append(_gp_pnt(path_points[0][0], path_points[0][1], z))

    mw = BRepBuilderAPI_MakeWire()
    added = 0
    for i in range(len(pts) - 1):
        p1, p2 = pts[i], pts[i + 1]
        if p1.Distance(p2) < 1e-9:
            continue
        mw.Add(_edge_line(p1, p2))
        added += 1
    if added < 3 or not mw.IsDone():
        raise RuntimeError(
            "_closed_path_sweep: closed polyline wire build failed "
            f"(added {added} edges)"
        )
    return mw.Wire()


def _polygon_signed_area(path_points: list[tuple[float, float]]) -> float:
    """Signed area (shoelace). Positive = CCW, negative = CW."""
    n = len(path_points)
    a = 0.0
    for i in range(n):
        x1, y1 = path_points[i]
        x2, y2 = path_points[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def _offset_wire(wire, distance: float):
    """2D-offset a planar wire by `distance` (signed).

    Positive distance grows the wire outward (assuming CCW orientation in the
    +Z plane); negative shrinks it inward. Uses GeomAbs_Arc joins so concave
    corners are filleted automatically.
    """
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
    from OCP.GeomAbs import GeomAbs_Arc
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    mk = BRepOffsetAPI_MakeOffset(wire, GeomAbs_Arc, False)
    mk.Perform(distance, 0.0)
    if not mk.IsDone():
        raise RuntimeError(
            f"_closed_path_sweep: 2D wire offset by {distance} failed"
        )
    shape = mk.Shape()
    # The result may be a wire or a compound containing one wire.
    if shape.ShapeType() == TopAbs_WIRE:
        return TopoDS.Wire_s(shape)
    exp = TopExp_Explorer(shape, TopAbs_WIRE)
    if not exp.More():
        raise RuntimeError(
            f"_closed_path_sweep: 2D wire offset produced no wire "
            f"(distance={distance})"
        )
    return TopoDS.Wire_s(exp.Current())


def _face_from_wire_at_z(wire, z: float):
    """Build a planar face on z=`z` plane from a closed wire."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt

    pl = gp_Pln(gp_Ax3(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)))
    mf = BRepBuilderAPI_MakeFace(pl, wire, True)
    if not mf.IsDone():
        raise RuntimeError("_closed_path_sweep: MakeFace from wire failed")
    return mf.Face()


def _extrude_face(face, dz: float):
    """Extrude planar face along world Z by `dz` (signed). Returns a solid."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, dz), False, True)
    if not prism.IsDone():
        raise RuntimeError("_closed_path_sweep: MakePrism failed")
    return prism.Shape()


def build_closed_groove(
    face,
    path_points: list[tuple[float, float]],
    width_mm: float,
    depth_mm: float,
):
    """Build a closed-loop groove cutter as a solid TopoDS shape.

    Args:
        face:        host TopoDS_Face. Must be planar with a ±Z normal in
                     world coordinates (matches the convention used by the
                     other axial / face-seal skills in this package).
        path_points: list of (x, y) tuples giving the GROOVE CENTERLINE in the
                     face's local XY frame. Local origin = face centroid.
                     The path is implicitly closed (last point connects back
                     to the first if not already coincident).
        width_mm:    groove band width (across the path), in mm. The cutter
                     extends ``width_mm/2`` to either side of the centerline.
        depth_mm:    groove depth (into the face), in mm.

    Returns:
        TopoDS_Shape (solid) in WORLD coordinates. Caller does
        ``BRepAlgoAPI_Cut(body, this)`` to actually sink the groove.

    Raises:
        ValueError:             on bad sizing inputs.
        NotImplementedError:    if the face is non-planar or its normal is
                                not along ±Z (within tolerance).
        RuntimeError:           if an OCCT step (offset / face / boolean /
                                extrusion) fails.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    from phone_designer.skills.modify_pocket._sketch_to_solid import (
        _face_plane_normal,
    )

    if width_mm <= 0:
        raise ValueError(
            f"_closed_path_sweep: width_mm must be > 0 (got {width_mm})"
        )
    if depth_mm <= 0:
        raise ValueError(
            f"_closed_path_sweep: depth_mm must be > 0 (got {depth_mm})"
        )
    if len(path_points) < 3:
        raise ValueError(
            "_closed_path_sweep: closed-path groove needs >= 3 distinct "
            f"points (got {len(path_points)})"
        )

    fc, fn = _face_plane_normal(face)
    if abs(fn[2]) < 0.95:
        raise NotImplementedError(
            "build_closed_groove: only planar faces whose normal is along ±Z "
            f"are supported (face normal z = {fn[2]:.3f})"
        )

    # World-space anchor of the local XY frame: shift path_points by the face
    # centroid so the path sits ON the face surface.
    cx, cy, cz = fc
    world_path = [(x + cx, y + cy) for (x, y) in path_points]

    # 2D-offset wants a CCW spine in the +Z plane to make +distance grow
    # OUTWARD. If the polygon is CW, flip the point order.
    if _polygon_signed_area(world_path) < 0:
        world_path = list(reversed(world_path))

    half_w = width_mm / 2.0

    # 1. centerline wire at the face plane.
    center_wire = _build_closed_polyline_wire(world_path, z=cz)

    # 2. outward offset = +half_w → outer wire.
    outer_wire = _offset_wire(center_wire, +half_w)
    # 3. inward offset  = -half_w → inner wire. ("SHRINK by width/2 inward")
    inner_wire = _offset_wire(center_wire, -half_w)

    # 4. planar faces from each wire, take the boolean difference → ring face.
    outer_face = _face_from_wire_at_z(outer_wire, cz)
    inner_face = _face_from_wire_at_z(inner_wire, cz)
    ring_cut = BRepAlgoAPI_Cut(outer_face, inner_face)
    ring_cut.Build()
    if not ring_cut.IsDone():
        raise RuntimeError(
            "_closed_path_sweep: ring face boolean (outer - inner) failed"
        )
    ring_face = ring_cut.Shape()

    # 5. extrude ring face along the face's INWARD normal by depth_mm.
    inward_dz = -depth_mm if fn[2] > 0 else +depth_mm
    return _extrude_face(ring_face, inward_dz)
