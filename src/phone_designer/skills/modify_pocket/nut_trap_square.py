"""nut_trap_square — atomic. Square nut trap pocket (DIN 557).

Cuts a square prism pocket sized to capture a standard square nut.
Pocket inscribed side = ``across_flats + clearance_mm``; depth defaults to
catalog nut thickness. ``orientation_deg`` rotates the square about the face
normal.
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


def _build_square_prism(side_mm: float, depth_mm: float):
    """Square prism (full side = side_mm). Opening at local Z=0, grows into -Z."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    h = side_mm / 2.0
    pts = [
        gp_Pnt(-h, -h, 0.0),
        gp_Pnt(+h, -h, 0.0),
        gp_Pnt(+h, +h, 0.0),
        gp_Pnt(-h, +h, 0.0),
    ]
    wm = BRepBuilderAPI_MakeWire()
    for i in range(4):
        wm.Add(BRepBuilderAPI_MakeEdge(pts[i], pts[(i + 1) % 4]).Edge())
    wire = wm.Wire()
    face = BRepBuilderAPI_MakeFace(wire, True).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, -float(depth_mm))).Shape()


@skill(
    name="nut_trap_square",
    category="modify/pocket",
    level="atomic",
    summary="Cut a square-nut trap pocket for DIN 557 square nuts at a face-local "
            "position. Pocket inscribed side = catalog AF + clearance.",
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
    produces_features=["square_nut_trap"],
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
    cost_hint=0.12,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class NutTrapSquare(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float]
        nut_spec: str = Field(description="catalog key, e.g. 'M4'")
        depth_mm_override: Optional[float] = Field(
            default=None, gt=0, le=50,
            description="override pocket depth (default = catalog nut thickness)",
        )
        orientation_deg: float = 0.0
        clearance_mm: float = Field(default=0.1, ge=0.0, le=2.0)

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

        catalog = _load("square_nuts")
        table = catalog.get("nuts", {})
        if args.nut_spec not in table:
            raise ValueError(
                f"nut_trap_square: nut_spec '{args.nut_spec}' not in catalog "
                f"(available: {sorted(table.keys())})"
            )
        af_nominal = float(table[args.nut_spec]["across_flats_mm"])
        side = af_nominal + float(args.clearance_mm)
        depth = float(args.depth_mm_override) if args.depth_mm_override is not None \
            else float(table[args.nut_spec]["thickness_mm"])

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"nut_trap_square: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        face_origin = _face_center(target_face)
        face_normal = _face_normal_at_center(target_face)
        if face_normal == (0.0, 0.0, 0.0):
            raise RuntimeError("nut_trap_square: face is not planar")

        cutter = _build_square_prism(side, depth)

        px, py = args.position_xy
        if abs(px) > 1e-12 or abs(py) > 1e-12:
            t = gp_Trsf()
            t.SetTranslation(gp_Vec(px, py, 0.0))
            cutter = BRepBuilderAPI_Transform(cutter, t, True).Shape()

        if abs(args.orientation_deg) > 1e-9:
            rot = gp_Trsf()
            rot.SetRotation(
                gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)),
                math.radians(args.orientation_deg),
            )
            cutter = BRepBuilderAPI_Transform(cutter, rot, True).Shape()

        nx, ny, nz = face_normal
        nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nlen < 1e-9:
            raise RuntimeError("nut_trap_square: face normal == 0")
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
            raise RuntimeError("nut_trap_square: BRepAlgoAPI_Cut failed")
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
