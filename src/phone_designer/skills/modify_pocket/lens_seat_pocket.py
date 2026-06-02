"""lens_seat_pocket — atomic. Stepped circular seat for an optical lens.

Catalog-driven circular pocket that holds a molded optical lens
(``Plano-Convex_Ø6_f10``, ``Aspheric_Ø8_f8``, ``Cylindrical_Ø10_f20``,
etc. per catalogs/optical/lenses.yaml). Two stages:

  - upper "seat" pocket of diameter lens.outer_d + 2*optical_clearance,
    depth = lens.thickness_mm — captures the lens body radially with
    a small clear-air margin so we do not touch the polished optical
    surface,
  - inner "optical clear" pocket of slightly smaller diameter
    (outer_d - 2*optical_clearance), depth optical_clearance_mm deeper.
    This is the optically clear "air well" under the lens. The skill
    never carves geometry inside the lens itself — only around it.

v1 supports planar ±Z target faces.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._resolvers import (
    _face_center,
    _face_normal_at_center,
    resolve_faces,
)
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family: str, name: str):
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


@skill(
    name="lens_seat_pocket",
    category="modify/pocket",
    level="atomic",
    summary="Stepped circular seat for an optical lens "
            "(catalogs/optical/lenses.yaml). Upper pocket captures the lens "
            "body with optical_clearance_mm lateral air gap; inner pocket "
            "is a small recessed clear-aperture well below the lens — the "
            "polished optical surface is never touched. v1 supports planar "
            "+Z/-Z faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "lens_seat":       HistoryRule.GENERATED_NEW,
        "clear_aperture":  HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.lens_spec_in_catalog",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["lens_seat_pocket"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4, "min_fillet_r_mm": 0.2,
                              "extras": {"flat_floor_required": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.lens_spec_unknown",
        "fm.pocket_exits_body",
        "fm.optical_clearance_too_large",
    ],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class LensSeatPocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of lens center, offset from face centroid.",
        )
        lens_spec: str = Field(
            description="Catalog key from catalogs/optical/lenses.yaml "
                        "(e.g. 'Plano-Convex_Ø6_f10', 'Aspheric_Ø8_f8').",
        )
        optical_clearance_mm: float = Field(
            default=0.3, gt=0.0, le=2.0,
            description="Radial / axial air gap around the lens so the "
                        "polished optical surface never touches the housing.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        catalog = _load("optical", "lenses")
        lenses = catalog.get("lenses", {})
        if args.lens_spec not in lenses:
            raise ValueError(
                f"lens_seat_pocket: unknown lens_spec '{args.lens_spec}' — "
                f"catalog keys: {sorted(lenses.keys())}"
            )
        entry = lenses[args.lens_spec]
        lens_d = float(entry["outer_d_mm"])
        lens_t = float(entry["thickness_mm"])

        if 2.0 * args.optical_clearance_mm >= lens_d:
            raise ValueError(
                f"lens_seat_pocket: optical_clearance_mm "
                f"({args.optical_clearance_mm}) too large for lens "
                f"diameter {lens_d}; inner clear-aperture collapses"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"lens_seat_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "lens_seat_pocket v1: planar +Z/-Z target faces only"
            )

        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        face_z = center[2]
        axis_dir = gp_Dir(0.0, 0.0, -nz_sign)

        overshoot = 0.05

        # ── Upper lens seat (lens body + 2*clearance radial).
        seat_d = lens_d + 2.0 * args.optical_clearance_mm
        seat_top_z = face_z + nz_sign * overshoot
        seat_ax = gp_Ax2(gp_Pnt(cx, cy, seat_top_z), axis_dir)
        seat_mk = BRepPrimAPI_MakeCylinder(
            seat_ax, seat_d / 2.0, lens_t + overshoot,
        )
        seat_mk.Build()
        if not seat_mk.IsDone():
            raise RuntimeError("lens_seat_pocket: seat cylinder failed")
        seat_solid = seat_mk.Shape()

        # ── Inner clear-aperture well (smaller diameter, deeper by clearance).
        clear_d = lens_d - 2.0 * args.optical_clearance_mm
        clear_top_z = face_z - nz_sign * lens_t
        clear_ax = gp_Ax2(gp_Pnt(cx, cy, clear_top_z), axis_dir)
        clear_mk = BRepPrimAPI_MakeCylinder(
            clear_ax, clear_d / 2.0, args.optical_clearance_mm + overshoot,
        )
        clear_mk.Build()
        if not clear_mk.IsDone():
            raise RuntimeError("lens_seat_pocket: clear aperture cyl failed")
        clear_solid = clear_mk.Shape()

        fuse = BRepAlgoAPI_Fuse(seat_solid, clear_solid)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("lens_seat_pocket: seat+clear fuse failed")
        tool = fuse.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("lens_seat_pocket: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "lens_seat":       HistoryRule.GENERATED_NEW,
                "clear_aperture":  HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
