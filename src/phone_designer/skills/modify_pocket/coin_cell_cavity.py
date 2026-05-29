"""coin_cell_cavity — atomic.

Catalog-driven cylindrical cavity for a primary coin/button cell per IEC
60086-3 (catalogs/coin_cells/<family>.yaml). Subtracts a cylinder of
(diameter + 2*side_clearance) × thickness from the target face.

v1 supports planar +Z / -Z target faces.
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
    name="coin_cell_cavity",
    category="modify/pocket",
    level="atomic",
    summary="Cylindrical cavity for a primary coin/button cell (CR2032/CR2025/"
            "CR2016/SR41/LR44 per IEC 60086-3). Subtracts a cylinder of "
            "(diameter + 2*side_clearance) × thickness from the target face. "
            "v1 supports planar +Z/-Z faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":      HistoryRule.MODIFIED_INHERIT,
        "cavity_wall":      HistoryRule.GENERATED_NEW,
        "cavity_floor":     HistoryRule.GENERATED_NEW,
        "consumed_volume":  HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.coin_cell_spec_in_catalog",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["coin_cell_cavity"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"flat_floor_required": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.coin_cell_spec_unknown",
        "fm.pocket_exits_body",
    ],
    cost_hint=0.12,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class CoinCellCavity(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of cavity center, offset from face centroid.",
        )
        cell_spec: str = Field(
            description="Catalog key (e.g. 'CR2032', 'CR2025', 'CR2016', 'SR41', 'LR44').",
        )
        catalog_family: str = Field(
            default="iec60086",
            description="Catalog filename stem under catalogs/coin_cells/.",
        )
        side_clearance_mm: float = Field(
            default=0.1, ge=0.0, le=2.0,
            description="Radial clearance added to cell diameter on each side "
                        "(cavity_d = cell_d + 2*side_clearance).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        catalog = _load("coin_cells", args.catalog_family)
        cells = catalog.get("cells", {})
        if args.cell_spec not in cells:
            raise ValueError(
                f"coin_cell_cavity: unknown cell_spec '{args.cell_spec}' in "
                f"family '{args.catalog_family}' — catalog keys: "
                f"{sorted(cells.keys())}"
            )
        entry = cells[args.cell_spec]
        cell_d = float(entry["diameter_mm"])
        cell_t = float(entry["thickness_mm"])

        cavity_d = cell_d + 2.0 * args.side_clearance_mm

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"coin_cell_cavity: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "coin_cell_cavity v1: planar +Z/-Z target faces only"
            )

        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        axis_dir = gp_Dir(0.0, 0.0, -nz_sign)

        cavity_base = gp_Pnt(cx, cy, center[2])
        cavity_ax = gp_Ax2(cavity_base, axis_dir)
        cavity_mk = BRepPrimAPI_MakeCylinder(cavity_ax, cavity_d / 2.0, cell_t)
        cavity_mk.Build()
        if not cavity_mk.IsDone():
            raise RuntimeError(
                "coin_cell_cavity: cavity cylinder build failed"
            )
        cut = BRepAlgoAPI_Cut(shape, cavity_mk.Shape())
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError(
                "coin_cell_cavity: boolean cut failed"
            )
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "cavity_wall":     HistoryRule.GENERATED_NEW,
                "cavity_floor":    HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
