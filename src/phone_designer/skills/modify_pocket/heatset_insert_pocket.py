"""heatset_insert_pocket — atomic.

Drill the pilot hole that receives a brass heat-set threaded insert in a
thermoplastic boss. The pilot diameter and nominal depth come from
catalogs/standards/inserts_heatset.yaml keyed by metric size (M2..M5). The
caller may override the pocket depth (e.g. blind boss with extra clearance).

v1 supports planar +Z/-Z target faces. Hole axis runs INTO the body along the
face normal.
"""
from __future__ import annotations

from typing import Any, Optional

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


def _load(name):
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / "standards" / f"{name}.yaml").read_text())


@skill(
    name="heatset_insert_pocket",
    category="modify/pocket",
    level="atomic",
    summary="Pilot-hole pocket for a brass heat-set threaded insert in a thermoplastic "
            "boss. Pilot diameter and nominal depth come from inserts_heatset.yaml "
            "keyed by metric size (M2..M5). v1 supports planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":          HistoryRule.MODIFIED_INHERIT,
        "insert_pilot_hole":    HistoryRule.GENERATED_NEW,
        "consumed_volume":      HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.insert_spec_in_catalog",
    ],
    produces_features=["heatset_insert_pocket", "pilot_hole"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"insert_install": "heat-press after molding"}},
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"drill_then_press": True}},
    },
    failure_modes=[
        "fm.insert_spec_unknown",
        "fm.pocket_exits_body",
    ],
    cost_hint=0.12,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class HeatsetInsertPocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of pilot hole center",
        )
        insert_spec: str = Field(
            description="Metric insert size key — one of M2, M2.5, M3, M4, M5.",
        )
        depth_override_mm: Optional[float] = Field(
            default=None, gt=0, le=200,
            description="Override pilot-hole depth; default = catalog length_mm.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        catalog = _load("inserts_heatset")
        inserts = catalog.get("inserts", {})
        if args.insert_spec not in inserts:
            raise ValueError(
                f"heatset_insert_pocket: unknown insert_spec '{args.insert_spec}' — "
                f"catalog keys: {sorted(inserts.keys())}"
            )
        entry = inserts[args.insert_spec]
        pilot_d = float(entry["pilot_hole_d_mm"])
        nominal_len = float(entry["length_mm"])
        depth = float(args.depth_override_mm) if args.depth_override_mm is not None else nominal_len

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"heatset_insert_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "heatset_insert_pocket v1: planar +Z/-Z target faces only"
            )

        # Drill axis runs INTO the body along -normal (so a +Z face drills downward).
        drill_z = -1.0 if normal[2] > 0 else 1.0
        direction = gp_Dir(0.0, 0.0, drill_z)
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        base = gp_Pnt(cx, cy, center[2])

        ax = gp_Ax2(base, direction)
        cyl_mk = BRepPrimAPI_MakeCylinder(ax, pilot_d / 2.0, depth)
        cyl_mk.Build()
        if not cyl_mk.IsDone():
            raise RuntimeError("heatset_insert_pocket: pilot cylinder build failed")
        tool = cyl_mk.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("heatset_insert_pocket: boolean cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":       HistoryRule.MODIFIED_INHERIT,
                "insert_pilot_hole": HistoryRule.GENERATED_NEW,
                "consumed_volume":   HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
