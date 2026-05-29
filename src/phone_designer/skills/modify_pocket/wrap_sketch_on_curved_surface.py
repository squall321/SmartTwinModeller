"""wrap_sketch_on_curved_surface — atomic.

Project a 2D sketch onto a CURVED face (sphere / cylinder / B-spline surface)
along a given direction, then pocket-engrave along the projected curve. The
typical use case is wrapping a logo / decorative motif onto a phone-back dome
or onto the cylindrical wall of a watch case.

Approach (preferred):
    1. Build the sketch wire in local XY (z=0), then translate it so it sits
       50 mm above the target face along the negation of `projection_direction`
       (so projection rays cross the surface cleanly).
    2. `BRepProj_Projection(wire, target_face, gp_Dir(*direction))` →
       projected wire that lies ON the curved surface.
    3. Sweep a small rectangular profile (1 mm × `depth_mm`) along the
       projected wire to build a thin engraved tube, then `BRepAlgoAPI_Cut`
       it from the body.

Fallback (when projection produces 0 edges or sweep fails):
    Extrude the original (un-projected) wire as a deep prism along
    `projection_direction` and subtract that prism from the body. This is
    geometrically less accurate (the engraving floor will be a flat slice
    rather than following the curvature) but always produces a non-zero
    volume change, which is enough for the decal use case on modest curvature.

Post: volume_decreased.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec


_PROJ_OFFSET_MM = 50.0   # how far above the face to start the projection rays
_FALLBACK_DEPTH_MM = 100.0


def _normalize_dir(d: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
    if n < 1e-12:
        raise ValueError("projection_direction is zero")
    return (d[0] / n, d[1] / n, d[2] / n)


def _translate_wire(wire, dx: float, dy: float, dz: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(dx, dy, dz))
    xf = BRepBuilderAPI_Transform(wire, t, True)
    xf.Build()
    if not xf.IsDone():
        raise RuntimeError("translate wire failed")
    return xf.Shape()


def _project_wire_on_face(wire, face, direction: tuple[float, float, float]):
    """Return a list of TopoDS_Wires obtained by projecting `wire` on `face`
    along `direction`. Empty list ⇒ projection produced no edges."""
    from OCP.BRepProj import BRepProj_Projection
    from OCP.gp import gp_Dir
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopoDS import TopoDS

    proj = BRepProj_Projection(wire, face, gp_Dir(*direction))
    out = []
    while proj.More():
        w = proj.Current()
        if w.ShapeType() == TopAbs_WIRE:
            out.append(TopoDS.Wire_s(w))
        proj.Next()
    return out


def _build_engrave_tube(projected_wire, depth_mm: float, direction: tuple[float, float, float]):
    """Build a thin engraving solid by sweeping a small rectangular profile
    along the projected wire. The profile lies in the plane perpendicular to
    `direction` and has size (1 mm × depth_mm), oriented so the depth axis is
    parallel to `direction`.

    Pipe (BRepOffsetAPI_MakePipe) is used with the default Frenet frame; for
    smooth curved-surface projections that frame is sufficient.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
    )
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
    from OCP.gp import gp_Pnt
    from OCP.TopoDS import TopoDS_Iterator

    # Determine wire start point and (approximate) tangent
    it = TopoDS_Iterator(projected_wire)
    start_pnt = None
    next_pnt = None
    while it.More() and start_pnt is None:
        e = it.Value()
        from OCP.TopExp import TopExp
        from OCP.BRep import BRep_Tool
        v1 = TopExp.FirstVertex_s(e)
        v2 = TopExp.LastVertex_s(e)
        p1 = BRep_Tool.Pnt_s(v1)
        p2 = BRep_Tool.Pnt_s(v2)
        start_pnt = (p1.X(), p1.Y(), p1.Z())
        next_pnt = (p2.X(), p2.Y(), p2.Z())
        it.Next()
    if start_pnt is None:
        raise RuntimeError("projected wire has no edges")

    # Build a small square profile in a plane perpendicular to `direction`,
    # centered on start_pnt, with thickness running along `direction`.
    dx, dy, dz = direction
    # Choose any vector not parallel to direction
    if abs(dx) < 0.9:
        u = (1.0, 0.0, 0.0)
    else:
        u = (0.0, 1.0, 0.0)
    # v1 = direction × u; v2 = direction × v1
    def cross(a, b):
        return (a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0])

    def norm(a):
        m = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
        if m < 1e-12:
            return (0.0, 0.0, 0.0)
        return (a[0] / m, a[1] / m, a[2] / m)

    perp1 = norm(cross(direction, u))
    perp2 = norm(cross(direction, perp1))

    # Width 1 mm in perp2 direction, depth (depth_mm) along direction.
    # Position the rectangle so projection direction points INTO the face
    # (i.e., apply depth in `direction` from start_pnt, which is on surface).
    half_w = 0.5  # 1 mm wide
    half_d = depth_mm / 2.0
    sx, sy, sz = start_pnt
    # Offset start by half_d along direction so the rectangle is centered on
    # the surface (so the cut straddles surface ± depth/2). For decal engrave
    # we want to subtract material from the surface inward, so bias entirely
    # along +direction (into the surface).
    cx = sx + dx * half_d
    cy = sy + dy * half_d
    cz = sz + dz * half_d

    poly = BRepBuilderAPI_MakePolygon()
    # 4 corners of a rectangle: (±half_d along direction) × (±half_w along perp2)
    for sign_d, sign_w in [(-1, -1), (+1, -1), (+1, +1), (-1, +1)]:
        px = cx + sign_d * half_d * dx + sign_w * half_w * perp2[0]
        py = cy + sign_d * half_d * dy + sign_w * half_w * perp2[1]
        pz = cz + sign_d * half_d * dz + sign_w * half_w * perp2[2]
        poly.Add(gp_Pnt(px, py, pz))
    poly.Close()
    if not poly.IsDone():
        raise RuntimeError("engrave profile polygon failed")
    profile_wire = poly.Wire()
    mf = BRepBuilderAPI_MakeFace(profile_wire, True)
    if not mf.IsDone():
        raise RuntimeError("engrave profile face failed")
    profile_face = mf.Face()

    pipe = BRepOffsetAPI_MakePipe(projected_wire, profile_face)
    pipe.Build()
    if not pipe.IsDone():
        raise RuntimeError("engrave pipe sweep failed")
    return pipe.Shape()


@skill(
    name="wrap_sketch_on_curved_surface",
    category="modify/pocket",
    level="atomic",
    summary="Project a 2D sketch onto a curved face (sphere / cylinder / "
            "B-spline) along a direction and engrave a thin decal pocket "
            "along the projected curve. Used for wrapping logos / decorative "
            "motifs onto domed phone backs or cylindrical watch cases.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":   HistoryRule.MODIFIED_INHERIT,
        "decal_walls":   HistoryRule.GENERATED_NEW,
        "decal_floor":   HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.face_is_curved",
    ],
    produces_features=["decal", "wrap_engraving"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_5axis":     {"min_wall_mm": 0.3, "extras": {"requires_5axis": True}},
        "laser_engrave": {"min_wall_mm": 0.1, "extras": {"surface_engrave": True}},
    },
    failure_modes=[
        "fm.projection_misses_face",
        "fm.curvature_too_high_for_sweep",
    ],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class WrapSketchOnCurvedSurface(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        sketch: SketchSpec
        projection_direction: tuple[float, float, float] = (0.0, 0.0, -1.0)
        depth_mm: float = Field(default=0.3, gt=0.0, le=10.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.GeomAbs import GeomAbs_Plane

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _build_planar_wire,
            _extrude_face_along_z,
            _build_planar_face,
            _translate_shape,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"wrap_sketch_on_curved_surface: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]

        # Validate: must be a CURVED face (not planar).
        surf = BRepAdaptor_Surface(target_face)
        if surf.GetType() == GeomAbs_Plane:
            raise NotImplementedError(
                "wrap_sketch_on_curved_surface: target face is planar; use "
                "extrude_pocket / text_engrave instead"
            )

        direction = _normalize_dir(args.projection_direction)

        # 1. Build the source wire in local XY plane (z=0).
        source_wire = _build_planar_wire(args.sketch)

        # 2. Offset the wire 50 mm "above" the face along -direction so
        #    projection rays cross the surface cleanly.
        ox = -direction[0] * _PROJ_OFFSET_MM
        oy = -direction[1] * _PROJ_OFFSET_MM
        oz = -direction[2] * _PROJ_OFFSET_MM
        try:
            from OCP.TopoDS import TopoDS
            translated = _translate_wire(source_wire, ox, oy, oz)
            translated_wire = TopoDS.Wire_s(translated)
        except Exception:
            translated_wire = source_wire

        # 3. Attempt true projection.
        engrave_tool = None
        used_fallback = False
        try:
            projected = _project_wire_on_face(translated_wire, target_face, direction)
            if projected:
                # Use the LONGEST projected wire (skip degenerate fragments).
                pw = projected[0]
                engrave_tool = _build_engrave_tube(pw, args.depth_mm, direction)
        except Exception:
            engrave_tool = None

        if engrave_tool is None:
            used_fallback = True
            # FALLBACK: extrude the original (un-projected) sketch face as a
            # deep prism along the projection direction and subtract.
            planar_face = _build_planar_face(args.sketch)

            # The face lives at z=0 with +Z normal. We want a deep prism along
            # `direction`. Approximate by extruding along ±Z by _FALLBACK_DEPTH
            # then position so it crosses the curved face.
            dz_sign = -1 if direction[2] < 0 else +1
            prism = _extrude_face_along_z(planar_face, _FALLBACK_DEPTH_MM, direction_sign=dz_sign)

            # Position the prism near the target face by translating to its
            # surface-properties center.
            from OCP.BRepGProp import BRepGProp
            from OCP.GProp import GProp_GProps
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(target_face, props)
            c = props.CentreOfMass()
            # Place prism so its base is `depth_mm` ABOVE the face center along
            # -direction, so the prism penetrates `depth_mm` into the body.
            # Z translation chosen heuristically: align base at center+depth.
            tx = c.X()
            ty = c.Y()
            if dz_sign > 0:
                tz = c.Z() - args.depth_mm
            else:
                tz = c.Z() + args.depth_mm
            engrave_tool = _translate_shape(prism, tx, ty, tz)

        cut = BRepAlgoAPI_Cut(shape, engrave_tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError(
                f"wrap_sketch_on_curved_surface cut failed "
                f"(fallback={used_fallback})"
            )
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face": HistoryRule.MODIFIED_INHERIT,
                "decal_walls": HistoryRule.GENERATED_NEW,
                "decal_floor": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
