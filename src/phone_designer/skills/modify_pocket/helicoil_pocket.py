"""helicoil_pocket — atomic.

Drill the STI (Screw Thread Insert) tap-drill pocket that receives a Helicoil
free-running wire-coil insert. Tap-drill diameter and length-multiple options
(1xD / 1.5xD / 2xD) come from catalogs/standards/inserts_helicoil.yaml keyed by
metric screw size (M3..M10).

The cut depth equals the chosen length multiple (the bore is at LEAST this
deep so the wire coil seats fully). v1 supports planar +Z/-Z target faces.
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


def _load(name):
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / "standards" / f"{name}.yaml").read_text())


@skill(
    name="helicoil_pocket",
    category="modify/pocket",
    level="atomic",
    summary="STI tap-drill pocket for a free-running Helicoil wire-coil insert. "
            "Tap-drill diameter and 1xD/1.5xD/2xD coil lengths come from "
            "inserts_helicoil.yaml keyed by metric size (M3..M10). v1 supports "
            "planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":          HistoryRule.MODIFIED_INHERIT,
        "helicoil_tap_bore":    HistoryRule.GENERATED_NEW,
        "consumed_volume":      HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.insert_spec_in_catalog",
    ],
    produces_features=["helicoil_pocket", "sti_tap_drill_bore"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis": {"min_wall_mm": 0.5,
                      "extras": {"sti_tap_required": True}},
        "tapping":   {"min_wall_mm": 0.3,
                      "extras": {"sti_tap_required": True}},
    },
    failure_modes=[
        "fm.insert_spec_unknown",
        "fm.length_multiple_unknown",
        "fm.pocket_exits_body",
    ],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class HelicoilPocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of bore center",
        )
        insert_spec: str = Field(
            description="Metric screw size key — one of M3, M4, M5, M6, M8, M10.",
        )
        length_multiple: Literal["1xD", "1.5xD", "2xD"] = Field(
            default="1.5xD",
            description="Helicoil free-running coil length as a multiple of nominal D.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        catalog = _load("inserts_helicoil")
        inserts = catalog.get("inserts", {})
        if args.insert_spec not in inserts:
            raise ValueError(
                f"helicoil_pocket: unknown insert_spec '{args.insert_spec}' — "
                f"catalog keys: {sorted(inserts.keys())}"
            )
        entry = inserts[args.insert_spec]
        tap_d = float(entry["tap_drill_mm"])
        lengths = entry.get("lengths_mm", {})
        if args.length_multiple not in lengths:
            raise ValueError(
                f"helicoil_pocket: length_multiple '{args.length_multiple}' not in "
                f"catalog entry for {args.insert_spec} (available: {sorted(lengths.keys())})"
            )
        depth = float(lengths[args.length_multiple])

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"helicoil_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "helicoil_pocket v1: planar +Z/-Z target faces only"
            )

        # Drill axis runs INTO the body along -normal.
        drill_z = -1.0 if normal[2] > 0 else 1.0
        direction = gp_Dir(0.0, 0.0, drill_z)
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        base = gp_Pnt(cx, cy, center[2])

        ax = gp_Ax2(base, direction)
        cyl_mk = BRepPrimAPI_MakeCylinder(ax, tap_d / 2.0, depth)
        cyl_mk.Build()
        if not cyl_mk.IsDone():
            raise RuntimeError("helicoil_pocket: tap-drill cylinder build failed")
        tool = cyl_mk.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("helicoil_pocket: boolean cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":       HistoryRule.MODIFIED_INHERIT,
                "helicoil_tap_bore": HistoryRule.GENERATED_NEW,
                "consumed_volume":   HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
