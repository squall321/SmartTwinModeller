"""phillips_recess — atomic. ANSI Type I-A Phillips (cross / 4-flute) recess.

지정된 planar face 위 (position_xy) 에 Phillips driver recess 깊이만큼 cross-
shape (4-flute pyramid approximation) cutter 를 빼낸다. size 는 "PH0".."PH3".

Geometry: a "+" (plus sign) prism whose two arms span ±R along X and Y.
Arm width 는 included_angle 26° 와 depth 로부터 derived — included angle 의 절반
(13°) 의 tangent 를 사용한 narrow band 로 actual 4-flute pyramid 의 근사.
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


def _build_cross_prism(
    major_d_mm: float, depth_mm: float, included_angle_deg: float,
):
    """4-arm "+" (cross) prism cutter, opening Z=0, growing into -Z by depth.

    Arm length = R = major_d / 2.
    Arm half-width = max( R * tan(angle/2), R * 0.18 ) — angle/2 of 13° gives
    a narrow band; we floor at 0.18*R so the cross is always visibly thick
    enough for a robust boolean. Real Phillips bits are pyramidal but we use
    a constant-section "+" as a CAD-friendly approximation.

    12-vertex polygon, CCW:

           y
         8 ── 7
         │    │
     11──9    6 ──5
      │            │
     10──0    3 ──4   →  centre at origin
         │    │
         1 ── 2
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
    half_angle = math.radians(included_angle_deg) / 2.0
    w = max(R * math.tan(half_angle), R * 0.18)  # arm half-width

    # 12 vertices CCW for a "+" centred at origin.
    pts = [
        gp_Pnt( w, -w, 0.0),   # 0  inner SE
        gp_Pnt( w, -R, 0.0),   # 1  south arm SE tip
        gp_Pnt(-w, -R, 0.0),   # 2  south arm SW tip   (NOTE: -w, -R)
        # Wait — I want CCW. Let me reorder cleanly below.
    ]
    # Build a clean CCW list directly.
    pts = []
    # +X arm: from inner-SE (w,-w) → outer-SE (R,-w) → outer-NE (R,w) → inner-NE (w,w)
    pts.append(gp_Pnt( w, -w, 0.0))
    pts.append(gp_Pnt( R, -w, 0.0))
    pts.append(gp_Pnt( R,  w, 0.0))
    pts.append(gp_Pnt( w,  w, 0.0))
    # +Y arm: → (w,w)→(w,R)→(-w,R)→(-w,w)
    pts.append(gp_Pnt( w,  R, 0.0))
    pts.append(gp_Pnt(-w,  R, 0.0))
    pts.append(gp_Pnt(-w,  w, 0.0))
    # -X arm: → (-w,w)→(-R,w)→(-R,-w)→(-w,-w)
    pts.append(gp_Pnt(-R,  w, 0.0))
    pts.append(gp_Pnt(-R, -w, 0.0))
    pts.append(gp_Pnt(-w, -w, 0.0))
    # -Y arm: → (-w,-w)→(-w,-R)→(w,-R)→(w,-w)
    pts.append(gp_Pnt(-w, -R, 0.0))
    pts.append(gp_Pnt( w, -R, 0.0))
    # closes back to pts[0] (w,-w).

    n = len(pts)
    wm = BRepBuilderAPI_MakeWire()
    for i in range(n):
        p0 = pts[i]
        p1 = pts[(i + 1) % n]
        e = BRepBuilderAPI_MakeEdge(p0, p1).Edge()
        wm.Add(e)
    wire = wm.Wire()
    face = BRepBuilderAPI_MakeFace(wire, True).Face()
    prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, -float(depth_mm))).Shape()
    return prism


@skill(
    name="phillips_recess",
    category="modify/pocket",
    level="atomic",
    summary="Cut a 4-flute Phillips (cross-shape) recess into a planar face. size is the "
            "ANSI Type I-A key (e.g. 'PH2'). The cutter is a constant '+' prism — a "
            "CAD-friendly approximation of the pyramidal Phillips tip.",
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
    produces_features=["phillips_recess"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4,
                              "extras": {"max_aspect_ratio": 8.0}},
        "die_cast_al":       {"min_wall_mm": 0.8, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.6, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.recess_outside_face", "fm.recess_too_deep"],
    cost_hint=0.16,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class PhillipsRecess(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float]
        size: str = Field(description="catalog key, e.g. 'PH2'")
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
        table = catalog.get("phillips", {})
        if args.size not in table:
            raise ValueError(
                f"phillips_recess: size '{args.size}' not in catalog "
                f"(available: {sorted(table.keys())})"
            )
        major_d = float(table[args.size]["major_diameter_mm"])
        angle_deg = float(table[args.size]["included_angle_deg"])

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"phillips_recess: face_selector matched 0 faces — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        face_origin = _face_center(target_face)
        face_normal = _face_normal_at_center(target_face)
        if face_normal == (0.0, 0.0, 0.0):
            raise RuntimeError("phillips_recess: target face 가 planar 가 아님")

        cutter = _build_cross_prism(major_d, args.depth_mm, angle_deg)

        px, py = args.position_xy
        if abs(px) > 1e-12 or abs(py) > 1e-12:
            t = gp_Trsf()
            t.SetTranslation(gp_Vec(px, py, 0.0))
            cutter = BRepBuilderAPI_Transform(cutter, t, True).Shape()

        nx, ny, nz = face_normal
        nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nlen < 1e-9:
            raise RuntimeError("phillips_recess: face normal == 0")
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
            raise RuntimeError("phillips_recess: BRepAlgoAPI_Cut 실패")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":  HistoryRule.MODIFIED_INHERIT,
                "recess_walls": HistoryRule.GENERATED_NEW,
                "recess_floor": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
