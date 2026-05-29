"""torx_recess — atomic. ISO 10664 internal Torx (6-point star) recess.

지정된 planar face 위 (position_xy) 에 Torx 깊이만큼 6-point star prism cutter
를 빼낸다. size 는 "T6", "T8", ..., "T30" 의 catalog 키.

본 skill 은 정확한 ISO 10664 lobe geometry 가 아니라 simple 6-point star
(12-vertex regular polygon with alternating R_outer / r_inner) approximation.
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


# Ratio inner_radius / outer_radius for a recognisable 6-point star — chosen
# small enough for visible lobes, large enough to leave the recess solid
# enough to model boolean-cleanly. (ISO 10664 actual minor/major ≈ 0.71.)
_TORX_INNER_RATIO = 0.71


def _build_star_prism(major_d_mm: float, depth_mm: float, n_points: int = 6):
    """N-point star prism, opening at Z=0 growing into -Z by depth.

    Vertices alternate R_outer = major_d/2 and R_inner = R_outer * _TORX_INNER_RATIO.
    Total 2N vertices CCW.
    """
    import math

    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    R = major_d_mm / 2.0
    r = R * _TORX_INNER_RATIO
    n_verts = 2 * n_points
    pts = []
    for i in range(n_verts):
        # alternate outer / inner. Step angle = 360 / (2N) deg.
        ang = math.radians(i * (360.0 / n_verts))
        rad = R if (i % 2 == 0) else r
        pts.append(gp_Pnt(rad * math.cos(ang), rad * math.sin(ang), 0.0))

    wm = BRepBuilderAPI_MakeWire()
    for i in range(n_verts):
        p0 = pts[i]
        p1 = pts[(i + 1) % n_verts]
        e = BRepBuilderAPI_MakeEdge(p0, p1).Edge()
        wm.Add(e)
    wire = wm.Wire()
    face = BRepBuilderAPI_MakeFace(wire, True).Face()
    prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, -float(depth_mm))).Shape()
    return prism


@skill(
    name="torx_recess",
    category="modify/pocket",
    level="atomic",
    summary="Cut an internal Torx (6-point star) recess into a planar face. size is the "
            "ISO 10664 tip series key (e.g. 'T20'). Star geometry is a simple "
            "12-vertex approximation of the ISO lobe profile.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":  HistoryRule.MODIFIED_INHERIT,
        "recess_walls": HistoryRule.GENERATED_NEW,
        "recess_floor": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["torx_recess"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4,
                              "extras": {"max_aspect_ratio": 8.0}},
        "die_cast_al":       {"min_wall_mm": 0.8, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.6, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.recess_outside_face", "fm.recess_too_deep"],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class TorxRecess(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float]
        size: str = Field(description="catalog key, e.g. 'T20'")
        depth_mm: float = Field(gt=0, le=50)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        import math

        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

        from phone_designer.skills._resolvers import (
            _face_center,
            _face_normal_at_center,
            resolve_faces,
        )

        catalog = _load("drivers")
        table = catalog.get("torx", {})
        if args.size not in table:
            raise ValueError(
                f"torx_recess: size '{args.size}' not in catalog "
                f"(available: {sorted(table.keys())})"
            )
        major_d = float(table[args.size]["major_diameter_mm"])

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"torx_recess: face_selector matched 0 faces — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        face_origin = _face_center(target_face)
        face_normal = _face_normal_at_center(target_face)
        if face_normal == (0.0, 0.0, 0.0):
            raise RuntimeError("torx_recess: target face 가 planar 가 아님")

        cutter = _build_star_prism(major_d, args.depth_mm, n_points=6)

        px, py = args.position_xy
        if abs(px) > 1e-12 or abs(py) > 1e-12:
            t = gp_Trsf()
            t.SetTranslation(gp_Vec(px, py, 0.0))
            cutter = BRepBuilderAPI_Transform(cutter, t, True).Shape()

        nx, ny, nz = face_normal
        nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nlen < 1e-9:
            raise RuntimeError("torx_recess: face normal == 0")
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
            raise RuntimeError("torx_recess: BRepAlgoAPI_Cut 실패")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":  HistoryRule.MODIFIED_INHERIT,
                "recess_walls": HistoryRule.GENERATED_NEW,
                "recess_floor": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
