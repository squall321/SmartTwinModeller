"""nut_trap_hex — atomic. Hex nut trap pocket (DIN 934 / ISO 4032).

Cuts a hexagonal prism pocket sized to capture a standard hex nut.

- Hex pocket inscribed flat-to-flat = ``across_flats + clearance_mm`` so the
  nut slides in with a small interference-free gap.
- Pocket depth defaults to the catalog nut thickness; ``depth_mm_override``
  overrides if e.g. captive depth desired.
- ``orientation_deg`` rotates the hex about the face normal.

Typical use: print-in-place / heat-stake-free threaded inserts on plastic parts.
"""
from __future__ import annotations

from typing import Any, Optional

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

    Vertices on circumscribed circle of radius af/sqrt(3) so inscribed
    (flat-to-flat) distance equals af.
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
    pts = []
    for i in range(6):
        ang = math.radians(60.0 * i)
        pts.append(gp_Pnt(r * math.cos(ang), r * math.sin(ang), 0.0))

    wire_maker = BRepBuilderAPI_MakeWire()
    for i in range(6):
        edge = BRepBuilderAPI_MakeEdge(pts[i], pts[(i + 1) % 6]).Edge()
        wire_maker.Add(edge)
    wire = wire_maker.Wire()

    face = BRepBuilderAPI_MakeFace(wire, True).Face()
    prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, -float(depth_mm))).Shape()
    return prism


@skill(
    name="nut_trap_hex",
    category="modify/pocket",
    level="atomic",
    summary="Cut a hex-nut trap pocket for DIN 934 / ISO 4032 nuts at a face-local "
            "position. Pocket inscribed AF = catalog AF + clearance.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "trap_walls":      HistoryRule.GENERATED_NEW,
        "trap_floor":      HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["hex_nut_trap"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"corner_tool_radius_mm": 0.5}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.trap_outside_face",
        "fm.trap_too_deep",
        "fm.unknown_nut_spec",
    ],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class NutTrapHex(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float]
        nut_spec: str = Field(description="catalog key, e.g. 'M3'")
        depth_mm_override: Optional[float] = Field(
            default=None, gt=0, le=50,
            description="override pocket depth (default = catalog nut thickness)",
        )
        orientation_deg: float = 0.0
        clearance_mm: float = Field(default=0.1, ge=0.0, le=2.0,
                                     description="added to AF for slip-in fit")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        import math

        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
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
                f"nut_trap_hex: nut_spec '{args.nut_spec}' not in catalog "
                f"(available: {sorted(table.keys())})"
            )
        af_nominal = float(table[args.nut_spec]["across_flats_mm"])
        af_pocket = af_nominal + float(args.clearance_mm)
        depth = float(args.depth_mm_override) if args.depth_mm_override is not None \
            else float(table[args.nut_spec]["thickness_mm"])

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"nut_trap_hex: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        face_origin = _face_center(target_face)
        face_normal = _face_normal_at_center(target_face)
        if face_normal == (0.0, 0.0, 0.0):
            raise RuntimeError("nut_trap_hex: face is not planar")

        # 1) cutter in local frame (opening Z=0, grows -Z).
        cutter = _build_hex_prism(af_pocket, depth)

        # 2) face-local translate.
        px, py = args.position_xy
        if abs(px) > 1e-12 or abs(py) > 1e-12:
            t = gp_Trsf()
            t.SetTranslation(gp_Vec(px, py, 0.0))
            cutter = BRepBuilderAPI_Transform(cutter, t, True).Shape()

        # 3) rotate about local +Z.
        if abs(args.orientation_deg) > 1e-9:
            rot = gp_Trsf()
            rot.SetRotation(
                gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)),
                math.radians(args.orientation_deg),
            )
            cutter = BRepBuilderAPI_Transform(cutter, rot, True).Shape()

        # 4) place onto face.
        nx, ny, nz = face_normal
        nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nlen < 1e-9:
            raise RuntimeError("nut_trap_hex: face normal == 0")
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
            raise RuntimeError("nut_trap_hex: BRepAlgoAPI_Cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "trap_walls":      HistoryRule.GENERATED_NEW,
                "trap_floor":      HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
