"""dowel_pin_hole — atomic. Cylindrical dowel pin hole (press or slip fit).

지정된 planar face 위의 (position_xy) 에서, 표준 dowel pin 직경 (`pin_diameter_mm`
는 catalogs/standards/dowel_pins.yaml 의 key, e.g. "3") + 끼워맞춤 등급 (`fit`)
에 맞는 hole 을 cut 한다.

- fit="press" → H7 hole (interference; pin 은 망치/press 로 박음)
- fit="slip"  → H8 hole (location fit; pin 을 손으로 꽂아 정렬)

`through=True` 면 body bbox 대각의 1.5× 깊이로 관통.
"""
from __future__ import annotations

from typing import Any, Literal

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


@skill(
    name="dowel_pin_hole",
    category="modify/pocket",
    level="atomic",
    summary="Drill a dowel-pin hole (ISO 2338 m6 mating) at a face-local position with "
            "selectable press (H7) or slip (H8) fit. Diameter is a catalog key "
            "(e.g. '3' → ø3 nominal).",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "hole_cylinder":   HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["dowel_pin_hole"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"max_aspect_ratio": 10.0,
                                         "reamer_finish": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_fit": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.hole_exits_body",
        "fm.hole_too_close_to_edge",
        "fm.unknown_pin_size",
    ],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class DowelPinHole(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float]
        pin_diameter_mm: str = Field(
            description="catalog key, e.g. '3' (ø3 nominal pin)",
        )
        fit: Literal["press", "slip"] = "press"
        depth_mm: float = Field(gt=0, le=200)
        through: bool = False

    def _apply(self, body: Any, args: Args) -> SkillResult:
        import math

        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

        from phone_designer.skills._resolvers import (
            _face_center,
            _face_normal_at_center,
            resolve_faces,
        )

        catalog = _load("dowel_pins")
        table = catalog.get("pins", {})
        if args.pin_diameter_mm not in table:
            raise ValueError(
                f"dowel_pin_hole: pin_diameter_mm '{args.pin_diameter_mm}' not in "
                f"catalog (available: {sorted(table.keys())})"
            )
        row = table[args.pin_diameter_mm]
        if args.fit == "press":
            d = float(row["press_fit_hole_mm"])
        else:
            d = float(row["slip_fit_hole_mm"])

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"dowel_pin_hole: face_selector matched 0 faces — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        face_origin = _face_center(target_face)
        face_normal = _face_normal_at_center(target_face)
        nx, ny, nz = face_normal
        nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nlen < 1e-9:
            raise RuntimeError("dowel_pin_hole: face normal == 0 (non-planar?)")
        nx, ny, nz = nx / nlen, ny / nlen, nz / nlen

        # Depth: if through, use body diagonal.
        if args.through:
            from OCP.Bnd import Bnd_Box
            from OCP.BRepBndLib import BRepBndLib
            bb = Bnd_Box()
            BRepBndLib.AddOptimal_s(shape, bb)
            xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
            diag = math.sqrt(
                (xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2
            )
            depth = diag * 1.5
        else:
            depth = float(args.depth_mm)

        # Cylinder cutter: opening at face_origin + face-local (px, py),
        # growing INTO the body (i.e. along -face_normal).
        px, py = args.position_xy
        # Build a face-local frame: nlz = -face_normal (into material),
        # nlx & nly are any orthonormal pair on the face plane.
        # Use world X as a seed for nlx; if too parallel to normal, use world Y.
        if abs(nx) < 0.9:
            seed = (1.0, 0.0, 0.0)
        else:
            seed = (0.0, 1.0, 0.0)
        # nly = normal × seed (orthogonal to both)
        nly = (
            ny * seed[2] - nz * seed[1],
            nz * seed[0] - nx * seed[2],
            nx * seed[1] - ny * seed[0],
        )
        nly_len = math.sqrt(nly[0] ** 2 + nly[1] ** 2 + nly[2] ** 2)
        nly = (nly[0] / nly_len, nly[1] / nly_len, nly[2] / nly_len)
        # nlx = nly × normal
        nlx = (
            nly[1] * nz - nly[2] * ny,
            nly[2] * nx - nly[0] * nz,
            nly[0] * ny - nly[1] * nx,
        )

        # World position of cylinder base center = face_origin + px·nlx + py·nly.
        cx = face_origin[0] + px * nlx[0] + py * nly[0]
        cy = face_origin[1] + px * nlx[1] + py * nly[1]
        cz = face_origin[2] + px * nlx[2] + py * nly[2]
        # Axis points INTO the body (= -normal). Cylinder grows in +axis direction.
        axis_dir = gp_Dir(-nx, -ny, -nz)
        ax2 = gp_Ax2(gp_Pnt(cx, cy, cz), axis_dir)
        cyl_mk = BRepPrimAPI_MakeCylinder(ax2, d / 2.0, depth)
        cyl_mk.Build()
        if not cyl_mk.IsDone():
            raise RuntimeError("dowel_pin_hole: cylinder build failed")
        cutter = cyl_mk.Shape()

        cut = BRepAlgoAPI_Cut(shape, cutter)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("dowel_pin_hole: BRepAlgoAPI_Cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "hole_cylinder":   HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
