"""knurl_pattern — macro.

Apply a knurled grip texture (diamond or straight) to a cylindrical face on
the body. Diamond knurling is the intersection of two opposite-handed helical
grooves; straight knurling is a set of axial V-grooves running parallel to
the cylinder axis.

Args:
    face_selector: SelectorRef  — must resolve to a cylindrical face. Axis +
                                   radius are extracted from the resolved face.
    pattern_type: "diamond" | "straight"
    pitch_mm: float = 0.5       — spacing between adjacent grooves along axis
    depth_mm: float = 0.2       — V-groove depth
    angle_deg: float = 30.0     — helix angle for diamond knurling (degrees
                                   from the cylinder axis)

Implementation:
    1. Resolve the target cylindrical face, extract `(axis_origin, axis_dir,
       radius, [z_min, z_max])` of the cylindrical region.
    2. For STRAIGHT knurling: build N axial V-grooves (rectangular profile
       sweep along a line parallel to the axis), placed every `pitch_mm` of
       arc length around the circumference.
    3. For DIAMOND knurling: build two families of helical grooves with
       opposite-handed pitch chosen so the helix angle equals `angle_deg`.
       Each helical groove uses the same swept-helix machinery as
       helical_thread_external but with a small triangular V-profile.

Post: volume_decreased.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _extract_cylinder(face) -> tuple[
    tuple[float, float, float],   # axis_origin
    tuple[float, float, float],   # axis_dir (normalized)
    float,                         # radius
    float, float,                  # z_min, z_max along axis
]:
    """Extract cylinder parameters from a cylindrical face.

    Raises NotImplementedError if the face is not a cylinder.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cylinder:
        raise NotImplementedError(
            "knurl_pattern: target face is not cylindrical"
        )
    cyl = surf.Cylinder()
    radius = float(cyl.Radius())
    pos = cyl.Position()       # gp_Ax3
    loc = pos.Location()
    ax_dir = pos.Direction()
    axis_origin = (loc.X(), loc.Y(), loc.Z())
    axis_dir = (ax_dir.X(), ax_dir.Y(), ax_dir.Z())

    # Find the axial extent of the cylindrical region: project each face vertex
    # onto the axis and take min/max.
    z_proj = []
    it = TopExp_Explorer(face, TopAbs_VERTEX)
    seen_pts = set()
    while it.More():
        v = TopoDS.Vertex_s(it.Current())
        p = BRep_Tool.Pnt_s(v)
        key = (round(p.X(), 4), round(p.Y(), 4), round(p.Z(), 4))
        if key in seen_pts:
            it.Next()
            continue
        seen_pts.add(key)
        dx = p.X() - axis_origin[0]
        dy = p.Y() - axis_origin[1]
        dz = p.Z() - axis_origin[2]
        z = dx * axis_dir[0] + dy * axis_dir[1] + dz * axis_dir[2]
        z_proj.append(z)
        it.Next()
    if not z_proj:
        # Fallback: take a generous range so the grooves cover the body.
        z_min, z_max = -100.0, 100.0
    else:
        z_min, z_max = min(z_proj), max(z_proj)
    return axis_origin, axis_dir, radius, z_min, z_max


def _v_groove_profile_face(radius: float, depth: float, width: float):
    """Build a triangular V-profile face in the local XZ plane positioned at
    the start of a groove (x ≈ radius, z = 0). Apex points radially INWARD."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
    )
    from OCP.gp import gp_Pnt

    BIAS = 0.05  # mm — small bias outside the cylinder to ensure clean cut
    x_base = radius + BIAS
    x_apex = max(radius - depth, 0.05)
    p_base_lo = gp_Pnt(x_base, 0.0, -width / 2.0)
    p_base_hi = gp_Pnt(x_base, 0.0,  width / 2.0)
    p_apex    = gp_Pnt(x_apex, 0.0,  0.0)

    poly = BRepBuilderAPI_MakePolygon()
    poly.Add(p_base_lo)
    poly.Add(p_base_hi)
    poly.Add(p_apex)
    poly.Close()
    if not poly.IsDone():
        raise RuntimeError("knurl V-profile polygon failed")
    mf = BRepBuilderAPI_MakeFace(poly.Wire(), True)
    if not mf.IsDone():
        raise RuntimeError("knurl V-profile face failed")
    return mf.Face()


def _local_to_axis_trsf(
    axis_origin: tuple[float, float, float],
    axis_dir: tuple[float, float, float],
):
    """gp_Trsf mapping the local frame (origin=0,0,0; +Z=0,0,1) to the
    target frame (origin=axis_origin; +Z=axis_dir)."""
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf
    target = gp_Ax3(gp_Pnt(*axis_origin), gp_Dir(*axis_dir))
    default = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    t = gp_Trsf()
    t.SetTransformation(target, default)
    return t


def _transform_shape(shape, trsf):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    xf = BRepBuilderAPI_Transform(shape, trsf, True)
    xf.Build()
    if not xf.IsDone():
        raise RuntimeError("BRepBuilderAPI_Transform failed")
    return xf.Shape()


def _axial_groove_solid_local(
    radius: float, depth: float, width: float,
    z_min: float, z_max: float, theta: float,
):
    """Build a single axial V-groove solid in the LOCAL frame (cylinder axis = +Z).

    The groove is a V-prism running from z=z_min to z=z_max along +Z,
    positioned at angular position `theta` around the axis.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
        BRepBuilderAPI_Transform,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    BIAS = 0.05
    x_base = radius + BIAS
    x_apex = max(radius - depth, 0.05)
    half_w = width / 2.0
    # Triangle in XY plane at z = z_min
    p_base_lo = gp_Pnt(x_base, -half_w, z_min)
    p_base_hi = gp_Pnt(x_base,  half_w, z_min)
    p_apex    = gp_Pnt(x_apex,  0.0,    z_min)
    poly = BRepBuilderAPI_MakePolygon()
    poly.Add(p_base_lo)
    poly.Add(p_base_hi)
    poly.Add(p_apex)
    poly.Close()
    if not poly.IsDone():
        raise RuntimeError("axial groove profile polygon failed")
    mf = BRepBuilderAPI_MakeFace(poly.Wire(), True)
    if not mf.IsDone():
        raise RuntimeError("axial groove profile face failed")
    prof_face = mf.Face()

    L = z_max - z_min
    prism = BRepPrimAPI_MakePrism(prof_face, gp_Vec(0.0, 0.0, L), False, True)
    if not prism.IsDone():
        raise RuntimeError("axial groove prism failed")
    prism_shape = prism.Shape()

    # Rotate around +Z by `theta` so we sweep around the cylinder.
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), theta)
    xf = BRepBuilderAPI_Transform(prism_shape, t, True)
    xf.Build()
    if not xf.IsDone():
        raise RuntimeError("axial groove rotation failed")
    return xf.Shape()


def _helical_groove_solid_local(
    radius: float, depth: float, width: float,
    z_min: float, z_max: float,
    pitch: float, theta_start: float, right_handed: bool,
):
    """Build a helical V-groove solid in the LOCAL frame (cylinder axis = +Z).

    `pitch` is the axial distance per turn; `right_handed=True` ⇒ +Z advances
    with +theta. `theta_start` rotates the start point around the axis.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.gp import gp_Pnt
    from OCP.TColgp import TColgp_HArray1OfPnt

    L = z_max - z_min
    if L <= 0 or pitch <= 0:
        raise ValueError("helical groove: invalid axial span or pitch")
    turns = L / pitch
    if turns < 1e-4:
        raise ValueError("helical groove: degenerate (turns≈0)")
    samples_per_turn = 16
    n = max(int(math.ceil(turns * samples_per_turn)) + 1, 12)
    sign = 1.0 if right_handed else -1.0
    arr = TColgp_HArray1OfPnt(1, n)
    total_t = turns * 2.0 * math.pi
    for i in range(n):
        t = (i / (n - 1)) * total_t
        ang = sign * t + theta_start
        x = radius * math.cos(ang)
        y = radius * math.sin(ang)
        z = z_min + (t / (2.0 * math.pi)) * pitch
        arr.SetValue(i + 1, gp_Pnt(x, y, z))
    interp = GeomAPI_Interpolate(arr, False, 1e-4)
    interp.Perform()
    if not interp.IsDone():
        raise RuntimeError("knurl helix interpolation failed")
    curve = interp.Curve()
    me = BRepBuilderAPI_MakeEdge(curve)
    if not me.IsDone():
        raise RuntimeError("knurl helix edge failed")
    mw = BRepBuilderAPI_MakeWire()
    mw.Add(me.Edge())
    if not mw.IsDone():
        raise RuntimeError("knurl helix wire failed")
    helix_wire = mw.Wire()

    profile = _v_groove_profile_face(radius, depth, width)

    pipe = BRepOffsetAPI_MakePipe(helix_wire, profile)
    pipe.Build()
    if not pipe.IsDone():
        raise RuntimeError("knurl helix pipe sweep failed")
    return pipe.Shape()


@skill(
    name="knurl_pattern",
    category="modify/pocket",
    level="macro",
    summary="Apply a knurled grip texture (diamond or straight) to a "
            "cylindrical face. Diamond knurling = two opposite-handed helical "
            "grooves intersecting; straight knurling = axial V-grooves. "
            "Standard knob / handle / barrel grip texture.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":   HistoryRule.MODIFIED_INHERIT,
        "knurl_grooves": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.face_is_cylindrical",
    ],
    produces_features=["knurl"],
    preserves=["axis_envelope"],
    manufacturing={
        "turning":   {"min_wall_mm": 0.3, "extras": {"knurling_tool": True}},
        "cnc_5axis": {"min_wall_mm": 0.5, "extras": {"requires_5axis": True}},
    },
    failure_modes=[
        "fm.face_not_cylindrical",
        "fm.knurl_depth_too_large",
    ],
    cost_hint=0.4,
    expansion=["helical_thread_external"] * 2,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class KnurlPattern(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        pattern_type: Literal["diamond", "straight"] = "diamond"
        pitch_mm: float = Field(default=0.5, gt=0.0, le=10.0)
        depth_mm: float = Field(default=0.2, gt=0.0, le=5.0)
        angle_deg: float = Field(default=30.0, gt=5.0, lt=85.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        from phone_designer.skills._resolvers import resolve_faces

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"knurl_pattern: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]

        axis_origin, axis_dir, radius, z_min, z_max = _extract_cylinder(target_face)
        if args.depth_mm >= radius:
            raise ValueError(
                f"knurl_pattern: depth_mm ({args.depth_mm}) >= radius ({radius})"
            )

        # Groove width tied to pitch (1/3 of pitch for the V-profile base)
        groove_width = max(args.pitch_mm * 0.4, 0.05)

        # Build the union of groove solids in the LOCAL frame
        groove_shapes = []

        if args.pattern_type == "straight":
            # N grooves around circumference, spaced by pitch_mm of arc length
            circumference = 2.0 * math.pi * radius
            n_grooves = max(int(circumference / args.pitch_mm), 4)
            # Slightly extend axial range so cuts span the whole face
            pad = 0.5
            for k in range(n_grooves):
                theta = 2.0 * math.pi * k / n_grooves
                g = _axial_groove_solid_local(
                    radius, args.depth_mm, groove_width,
                    z_min - pad, z_max + pad, theta,
                )
                groove_shapes.append(g)
        else:
            # Diamond pattern: two helix families at ±helix_angle from axis.
            # Helix angle α: pitch_axial = circumference / tan(α).
            alpha = math.radians(args.angle_deg)
            tan_a = math.tan(alpha)
            if tan_a < 1e-6:
                raise ValueError(
                    f"knurl_pattern: angle_deg ({args.angle_deg}) too small"
                )
            circumference = 2.0 * math.pi * radius
            helix_pitch = circumference / tan_a
            # Number of starts so adjacent grooves are spaced by pitch_mm
            # along axis at theta=0. Cap to keep total sweep count modest
            # (helix sweep is the slowest OCCT op in this skill).
            n_starts = max(min(int(helix_pitch / args.pitch_mm), 8), 3)
            pad = max(helix_pitch * 0.6, 1.0)
            extended_zmin = z_min - pad
            extended_zmax = z_max + pad
            for k in range(n_starts):
                theta0 = 2.0 * math.pi * k / n_starts
                # Right-handed helix
                try:
                    g1 = _helical_groove_solid_local(
                        radius, args.depth_mm, groove_width,
                        extended_zmin, extended_zmax, helix_pitch, theta0,
                        right_handed=True,
                    )
                    groove_shapes.append(g1)
                except Exception:
                    pass
                # Left-handed helix (opposite)
                try:
                    g2 = _helical_groove_solid_local(
                        radius, args.depth_mm, groove_width,
                        extended_zmin, extended_zmax, helix_pitch, theta0,
                        right_handed=False,
                    )
                    groove_shapes.append(g2)
                except Exception:
                    pass

        if not groove_shapes:
            raise RuntimeError("knurl_pattern: no groove solids built")

        # Transform all grooves from local to axis frame
        trsf = _local_to_axis_trsf(axis_origin, axis_dir)
        new_shape = shape
        cuts_done = 0
        for g in groove_shapes:
            try:
                g_world = _transform_shape(g, trsf)
                cut = BRepAlgoAPI_Cut(new_shape, g_world)
                cut.Build()
                if cut.IsDone():
                    new_shape = cut.Shape()
                    cuts_done += 1
            except Exception:
                continue
        if cuts_done == 0:
            raise RuntimeError("knurl_pattern: all cuts failed")

        history = EntityHistoryMap(
            rules={
                "target_face":   HistoryRule.MODIFIED_INHERIT,
                "knurl_grooves": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
