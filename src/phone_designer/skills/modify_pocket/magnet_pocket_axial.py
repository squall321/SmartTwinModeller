"""magnet_pocket_axial — atomic. Catalog-driven axial NdFeB disc magnet pocket.

A cylindrical recess sized for an axial-magnetised disc magnet (e.g. closure
magnet, sensor magnet, accessory mount).

Catalog: catalogs/magnets/<family>.yaml (default family = "ndfeb"). Each entry
supplies the magnet disc diameter (`magnet_d_mm`) and thickness
(`magnet_t_mm`).

Pocket sizing:
  pocket_d = magnet_d + diametral_clearance[retention]
    press  → -0.02 mm  (slight interference, press-fit)
    glue   → +0.05 mm  (gap for adhesive)
    loose  → +0.10 mm  (drop-in fit)
  pocket_depth = magnet_t - proud_offset_mm
    Default proud_offset_mm = 0.05 mm so the magnet sits a touch proud of
    the host face (ensures full contact with the mating magnet / part).

v1 supports planar +Z/-Z target faces. The pocket opens AT the selected face
and grows INTO the body opposite the face normal.
"""
from __future__ import annotations

from typing import Any, Literal

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


# Diametral clearance per retention mode (mm).
_CLEARANCE_MM = {
    "press": -0.02,
    "glue":  +0.05,
    "loose": +0.10,
}


def _load(family: str, name: str):
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


@skill(
    name="magnet_pocket_axial",
    category="modify/pocket",
    level="atomic",
    summary="Catalog-driven cylindrical pocket for an axial NdFeB disc magnet. "
            "Pocket diameter = magnet_d + retention clearance "
            "(press=-0.02, glue=+0.05, loose=+0.10 mm). Pocket depth = "
            "magnet_t - proud_offset so the magnet sits slightly proud. "
            "Catalog: catalogs/magnets/<family>.yaml. v1 supports planar "
            "+Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "magnet_pocket":   HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.magnet_spec_in_catalog",
    ],
    produces_features=["magnet_pocket_axial"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"flat_bottom_endmill": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_press_fit": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.magnet_spec_unknown",
        "fm.pocket_outside_body",
        "fm.pocket_depth_non_positive",
    ],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class MagnetPocketAxial(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) offset from face center to "
                        "the magnet pocket axis",
        )
        magnet_spec: str = Field(
            description="Catalog key (e.g. 'N42_D5x2', 'N42_D8x3')",
        )
        retention: Literal["press", "glue", "loose"] = Field(
            default="press",
            description="Retention mode → diametral clearance "
                        "(press=-0.02, glue=+0.05, loose=+0.10 mm)",
        )
        proud_offset_mm: float = Field(
            default=0.05,
            ge=0.0,
            le=1.0,
            description="How much the magnet sits proud of the host face. "
                        "Pocket depth = magnet_t - proud_offset_mm.",
        )
        catalog_family: str = Field(
            default="ndfeb",
            description="Catalog subfolder under catalogs/magnets/",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        catalog = _load("magnets", args.catalog_family)
        magnets = catalog.get("magnets", {})
        if args.magnet_spec not in magnets:
            raise ValueError(
                f"magnet_pocket_axial: unknown magnet_spec "
                f"'{args.magnet_spec}' in family '{args.catalog_family}' — "
                f"catalog keys: {sorted(magnets.keys())}"
            )
        entry = magnets[args.magnet_spec]
        magnet_d = float(entry["magnet_d_mm"])
        magnet_t = float(entry["magnet_t_mm"])

        clearance = _CLEARANCE_MM[args.retention]
        pocket_d = magnet_d + clearance
        pocket_depth = magnet_t - args.proud_offset_mm

        if pocket_d <= 0.0:
            raise ValueError(
                f"magnet_pocket_axial: derived pocket diameter "
                f"{pocket_d:.3f} <= 0"
            )
        if pocket_depth <= 0.0:
            raise ValueError(
                f"magnet_pocket_axial: derived pocket depth "
                f"{pocket_depth:.3f} <= 0 — proud_offset_mm "
                f"({args.proud_offset_mm}) >= magnet_t ({magnet_t})"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"magnet_pocket_axial: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "magnet_pocket_axial v1: planar +Z/-Z target faces only"
            )

        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        # Pocket axis grows INTO the body, opposite the face normal.
        axis_dir = gp_Dir(0.0, 0.0, -nz_sign)
        base = gp_Pnt(cx, cy, center[2])
        ax = gp_Ax2(base, axis_dir)

        cyl_mk = BRepPrimAPI_MakeCylinder(ax, pocket_d / 2.0, pocket_depth)
        cyl_mk.Build()
        if not cyl_mk.IsDone():
            raise RuntimeError(
                "magnet_pocket_axial: pocket cylinder build failed"
            )
        cut = BRepAlgoAPI_Cut(shape, cyl_mk.Shape())
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError(
                "magnet_pocket_axial: pocket boolean cut failed"
            )
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "magnet_pocket":   HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
