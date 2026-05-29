"""hex_socket_recess — atomic. ISO 4762 / DIN 912 internal hex (Allen) recess.

지정된 planar face 위 (position_xy) 에 hex socket 깊이 만큼 hex prism cutter 를
빼낸다. size 는 catalogs/standards/drivers.yaml 의 hex_socket 키 (across-flats
mm, e.g. "3" → AF 3.0mm).
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
    """Hex prism (across-flats = af_mm) of given depth.

    MakeWire(6 segments) → MakeFace → MakePrism(0, 0, depth). Returns a
    TopoDS_Shape (solid) centered at XY = (0,0), Z spanning [-depth, 0]
    (cutter grows downward from the surface — caller transforms it onto face).
    Hex vertices live on a circumscribed circle of radius af / sqrt(3) so the
    inscribed (flat-to-flat) distance equals af.
    """
    import math

    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    # Circumradius = AF / sqrt(3) for a regular hexagon (flat-to-flat = AF).
    r = af_mm / math.sqrt(3.0)

    # Six hex vertices, starting at angle 0 (point on +X), CCW. Sit on Z=0 so
    # the resulting face is the recess opening at the host surface.
    pts = []
    for i in range(6):
        ang = math.radians(60.0 * i)
        pts.append(gp_Pnt(r * math.cos(ang), r * math.sin(ang), 0.0))

    wire_maker = BRepBuilderAPI_MakeWire()
    for i in range(6):
        p0 = pts[i]
        p1 = pts[(i + 1) % 6]
        edge = BRepBuilderAPI_MakeEdge(p0, p1).Edge()
        wire_maker.Add(edge)
    wire = wire_maker.Wire()

    face = BRepBuilderAPI_MakeFace(wire, True).Face()

    # Extrude downward by depth so the cutter occupies Z ∈ [-depth, 0] —
    # the caller positions the local origin at the recess opening, with
    # the host material below.
    prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, -float(depth_mm))).Shape()
    return prism


@skill(
    name="hex_socket_recess",
    category="modify/pocket",
    level="atomic",
    summary="Cut an internal hex (Allen) recess into the selected planar face at a 2D "
            "position on that face. Size is the ISO across-flats key (e.g. '3').",
    selector_kinds=["faces"],
    history_rules={
        "target_face":   HistoryRule.MODIFIED_INHERIT,
        "recess_walls":  HistoryRule.GENERATED_NEW,
        "recess_floor":  HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["hex_socket_recess"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4,
                              "extras": {"max_aspect_ratio": 8.0}},
        "die_cast_al":       {"min_wall_mm": 0.8, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.6, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.recess_outside_face", "fm.recess_too_deep"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class HexSocketRecess(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float]
        size: str = Field(description="catalog key, e.g. '3' (AF 3mm)")
        depth_mm: float = Field(gt=0, le=50)
        orientation_deg: float = 0.0

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

        catalog = _load("drivers")
        table = catalog.get("hex_socket", {})
        if args.size not in table:
            raise ValueError(
                f"hex_socket_recess: size '{args.size}' not in catalog "
                f"(available: {sorted(table.keys())})"
            )
        af = float(table[args.size]["across_flats_mm"])

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"hex_socket_recess: face_selector matched 0 faces — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        face_origin = _face_center(target_face)
        face_normal = _face_normal_at_center(target_face)
        if face_normal == (0.0, 0.0, 0.0):
            raise RuntimeError(
                "hex_socket_recess: target face 가 planar 가 아님 (v0 은 planar only)"
            )

        # 1) build cutter in local XY plane (opening at Z=0, growing into -Z).
        cutter = _build_hex_prism(af, args.depth_mm)

        # 2) translate to (position_xy, 0) in local frame.
        px, py = args.position_xy
        if abs(px) > 1e-12 or abs(py) > 1e-12:
            t = gp_Trsf()
            t.SetTranslation(gp_Vec(px, py, 0.0))
            cutter = BRepBuilderAPI_Transform(cutter, t, True).Shape()

        # 3) rotate around local +Z by orientation_deg.
        if abs(args.orientation_deg) > 1e-9:
            rot = gp_Trsf()
            rot.SetRotation(
                gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)),
                math.radians(args.orientation_deg),
            )
            cutter = BRepBuilderAPI_Transform(cutter, rot, True).Shape()

        # 4) Place onto face: local Z (where cutter opens) → face inward normal.
        # Build target axis with face_normal as the +Z direction; the cutter
        # was built spanning -Z (downward from surface) so it grows into the
        # body along -face_normal — i.e. into material.
        nx, ny, nz = face_normal
        nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nlen < 1e-9:
            raise RuntimeError("hex_socket_recess: face normal == 0")
        nx, ny, nz = nx / nlen, ny / nlen, nz / nlen

        # Pick a safe X reference orthogonal to the normal.
        x_ref = gp_Dir(1.0, 0.0, 0.0) if abs(nx) < 0.9 else gp_Dir(0.0, 1.0, 0.0)
        target_ax = gp_Ax3(gp_Pnt(*face_origin), gp_Dir(nx, ny, nz), x_ref)
        source_ax = gp_Ax3(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0), gp_Dir(1.0, 0.0, 0.0))
        place = gp_Trsf()
        place.SetTransformation(target_ax, source_ax)
        cutter = BRepBuilderAPI_Transform(cutter, place, True).Shape()

        cut = BRepAlgoAPI_Cut(shape, cutter)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("hex_socket_recess: BRepAlgoAPI_Cut 실패")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":  HistoryRule.MODIFIED_INHERIT,
                "recess_walls": HistoryRule.GENERATED_NEW,
                "recess_floor": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
