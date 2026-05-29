"""captive_nut_pocket — atomic. Hex nut pocket with one open side (slide-in).

Same as ``nut_trap_hex`` but extends the pocket through the +X face-local
side wall up to the body bbox edge — so the nut can be slid in from that
side, then becomes captive once the mating screw passes through. Common in
3D-printed parts where the print direction prevents dropping a nut in
top-down.

Geometry:
    - hex prism cutter: full closed hex, depth = catalog thickness, opening
      at the target face.
    - slot extension: rectangular prism from the +X hex flat outward by a
      large distance, of height ``slide_in_height_mm`` (along the face
      normal, starting at the pocket floor). Aligned with the hex flats so
      it cleanly meets the open side.

Both cutters are unioned and subtracted from the body.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(name):
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / "standards" / f"{name}.yaml").read_text())


def _build_hex_prism(af_mm: float, depth_mm: float):
    """Hex prism (across-flats = af_mm). Opening at local Z=0, growing into -Z.

    Vertex 0 at angle 0 (+X axis). With this orientation the two flats
    perpendicular to ±X are at x = ±af/2 — i.e. the "+X flat" is the
    face-local right side, which we extend outward for slide-in.
    """
    import math

    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    r = af_mm / math.sqrt(3.0)
    # Rotate so that one flat is perpendicular to +X axis.
    # With vertices at angles 30°, 90°, ..., the flats are perpendicular to
    # 0°, 60°, ... — i.e. the +X side is a flat (cleaner slot exit).
    pts = []
    for i in range(6):
        ang = math.radians(30.0 + 60.0 * i)
        pts.append(gp_Pnt(r * math.cos(ang), r * math.sin(ang), 0.0))

    wire_maker = BRepBuilderAPI_MakeWire()
    for i in range(6):
        edge = BRepBuilderAPI_MakeEdge(pts[i], pts[(i + 1) % 6]).Edge()
        wire_maker.Add(edge)
    wire = wire_maker.Wire()

    face = BRepBuilderAPI_MakeFace(wire, True).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, -float(depth_mm))).Shape()


def _build_slot_box(af_mm: float, slide_in_height_mm: float, length_mm: float,
                    depth_mm: float):
    """Slot box from the +X hex flat outward by ``length_mm``.

    Width (Y) = af (matches hex flat width).
    Z spans from local Z=0 down to Z=-slide_in_height_mm — so the slot
    opens at the SURFACE (Z=0) and goes down by slide_in_height_mm. Since
    the hex pocket also opens at Z=0 and goes to Z=-depth_mm, the slot
    spans the upper portion (depth-wise) up to slide_in_height_mm.

    The +X hex flat sits at x = af/2 (with vertices rotated 30°). The slot
    starts at that x and extends to x = af/2 + length_mm.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    x0 = af_mm / 2.0
    x1 = x0 + float(length_mm)
    half_w = af_mm / 2.0
    z0 = -float(slide_in_height_mm)
    z1 = 0.0
    p_lo = gp_Pnt(x0, -half_w, z0)
    p_hi = gp_Pnt(x1, +half_w, z1)
    mk = BRepPrimAPI_MakeBox(p_lo, p_hi)
    mk.Build()
    if not mk.IsDone():
        raise RuntimeError("captive_nut_pocket: slot box build failed")
    return mk.Shape()


@skill(
    name="captive_nut_pocket",
    category="modify/pocket",
    level="atomic",
    summary="Hex nut pocket with one open side — nut slides in from the +X face-local "
            "direction. Useful for 3D-printed captive-nut joints.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":      HistoryRule.MODIFIED_INHERIT,
        "pocket_walls":     HistoryRule.GENERATED_NEW,
        "pocket_floor":     HistoryRule.GENERATED_NEW,
        "slide_in_walls":   HistoryRule.GENERATED_NEW,
        "consumed_volume":  HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["captive_nut_pocket"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"undercut_warning": "slide-in opening is "
                                          "a side undercut — needs side action"}},
        "cnc_3axis":         {"min_wall_mm": 0.5},
    },
    failure_modes=[
        "fm.pocket_outside_face",
        "fm.pocket_too_deep",
        "fm.unknown_nut_spec",
        "fm.slide_in_height_zero",
    ],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class CaptiveNutPocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float]
        nut_spec: str = Field(description="catalog key, e.g. 'M3'")
        slide_in_height_mm: float = Field(
            gt=0, le=20,
            description="vertical span of the slide-in opening (along face normal)",
        )
        orientation_deg: float = Field(
            default=0.0,
            description="rotate the hex + slide-in axis about the face normal",
        )
        clearance_mm: float = Field(default=0.1, ge=0.0, le=2.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        import math

        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.gp import gp_Ax1, gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

        from phone_designer.skills._resolvers import (
            _face_center,
            _face_normal_at_center,
            resolve_faces,
        )

        catalog = _load("hex_nuts_din934")
        table = catalog.get("nuts", {})
        if args.nut_spec not in table:
            raise ValueError(
                f"captive_nut_pocket: nut_spec '{args.nut_spec}' not in catalog "
                f"(available: {sorted(table.keys())})"
            )
        af_nominal = float(table[args.nut_spec]["across_flats_mm"])
        af_pocket = af_nominal + float(args.clearance_mm)
        depth = float(table[args.nut_spec]["thickness_mm"])
        if args.slide_in_height_mm > depth:
            # Slide-in slot deeper than pocket — clamp so the slot doesn't dangle
            # below the pocket floor (would create an under-undercut).
            slide_h = depth
        else:
            slide_h = float(args.slide_in_height_mm)

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"captive_nut_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        face_origin = _face_center(target_face)
        face_normal = _face_normal_at_center(target_face)
        if face_normal == (0.0, 0.0, 0.0):
            raise RuntimeError("captive_nut_pocket: face is not planar")

        # body diagonal as slide-out distance — guaranteed to exit the body.
        bb = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, bb)
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        diag = math.sqrt(
            (xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2
        )
        slot_len = diag * 1.5

        # 1) build cutters in local frame (opening at Z=0, grow -Z).
        hex_cutter = _build_hex_prism(af_pocket, depth)
        slot_cutter = _build_slot_box(af_pocket, slide_h, slot_len, depth)

        # 2) fuse hex + slot → captive cutter.
        f1 = BRepAlgoAPI_Fuse(hex_cutter, slot_cutter)
        f1.Build()
        if not f1.IsDone():
            raise RuntimeError("captive_nut_pocket: hex+slot fuse failed")
        cutter = f1.Shape()

        # 3) face-local translate.
        px, py = args.position_xy
        if abs(px) > 1e-12 or abs(py) > 1e-12:
            t = gp_Trsf()
            t.SetTranslation(gp_Vec(px, py, 0.0))
            cutter = BRepBuilderAPI_Transform(cutter, t, True).Shape()

        # 4) rotate about local +Z.
        if abs(args.orientation_deg) > 1e-9:
            rot = gp_Trsf()
            rot.SetRotation(
                gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)),
                math.radians(args.orientation_deg),
            )
            cutter = BRepBuilderAPI_Transform(cutter, rot, True).Shape()

        # 5) place onto face.
        nx, ny, nz = face_normal
        nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nlen < 1e-9:
            raise RuntimeError("captive_nut_pocket: face normal == 0")
        nx, ny, nz = nx / nlen, ny / nlen, nz / nlen
        x_ref = gp_Dir(1.0, 0.0, 0.0) if abs(nx) < 0.9 else gp_Dir(0.0, 1.0, 0.0)
        target_ax = gp_Ax3(gp_Pnt(*face_origin), gp_Dir(nx, ny, nz), x_ref)
        source_ax = gp_Ax3(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0), gp_Dir(1.0, 0.0, 0.0))
        place = gp_Trsf()
        place.SetTransformation(target_ax, source_ax)
        cutter = BRepBuilderAPI_Transform(cutter, place, True).Shape()

        cut = BRepAlgoAPI_Cut(shape, cutter)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("captive_nut_pocket: BRepAlgoAPI_Cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "pocket_walls":    HistoryRule.GENERATED_NEW,
                "pocket_floor":    HistoryRule.GENERATED_NEW,
                "slide_in_walls":  HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
