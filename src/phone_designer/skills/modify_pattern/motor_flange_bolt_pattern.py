"""motor_flange_bolt_pattern — macro. NEMA motor mounting flange bolt holes.

Catalog-driven circular pattern of clearance holes on a planar face, sized
and spaced to mount a standard NEMA stepper motor flange. Each NEMA frame
defines a bolt circle diameter, per-bolt clearance hole diameter, and bolt
count — typically 4 bolts at 45° + k * 90° around the circle.

Catalog: `catalogs/motor_frames/nema.yaml` (NEMA_17, NEMA_23, NEMA_34, ...).

v1 supports planar +Z / -Z target faces only. `hole_through=True` punches
through the body; `hole_through=False` drills a blind clearance recess as
deep as the catalog hole diameter (conservative default depth).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


# Through-hole length convention — generous, matches hole.py / pattern macro.
_THROUGH_LEN_MM = 200.0


def _load(family: str, name: str):
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


@skill(
    name="motor_flange_bolt_pattern",
    category="modify/pattern",
    level="macro",
    summary="Circular pattern of clearance holes matching a NEMA motor "
            "mounting flange (bolt circle diameter, hole diameter, bolt "
            "count come from catalogs/motor_frames/<...>.yaml). v1 supports "
            "planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "bolt_holes":      HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.motor_spec_in_catalog",
    ],
    produces_features=["motor_flange_bolt_pattern"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"drill_through": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.motor_spec_unknown",
        "fm.bolt_circle_exits_face",
    ],
    cost_hint=0.3,
    expansion=["hole"] * 4,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class MotorFlangeBoltPattern(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        center_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of the bolt circle center",
        )
        motor_spec: str = Field(
            description="NEMA frame catalog key (e.g. 'NEMA_17', 'NEMA_23')",
        )
        hole_through: bool = Field(
            default=True,
            description="True = through-hole, False = blind clearance recess",
        )
        catalog_family: str = Field(
            default="nema",
            description="Catalog file under catalogs/motor_frames/",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        from phone_designer.skills._resolvers import (
            _face_center,
            _face_normal_at_center,
            resolve_faces,
        )

        catalog = _load("motor_frames", args.catalog_family)
        frames = catalog.get("motor_frames", {})
        if args.motor_spec not in frames:
            raise ValueError(
                f"motor_flange_bolt_pattern: unknown motor_spec "
                f"'{args.motor_spec}' in family '{args.catalog_family}' — "
                f"catalog keys: {sorted(frames.keys())}"
            )
        entry = frames[args.motor_spec]
        bolt_circle_d = float(entry["bolt_circle_d_mm"])
        hole_d = float(entry["hole_d_mm"])
        bolts = int(entry["bolts"])
        if bolts < 2:
            raise ValueError(
                f"motor_flange_bolt_pattern: bolt count {bolts} < 2 — "
                f"catalog entry '{args.motor_spec}' is invalid"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"motor_flange_bolt_pattern: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "motor_flange_bolt_pattern v1: planar +Z/-Z target faces only"
            )

        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.center_xy[0]
        cy = center[1] + args.center_xy[1]
        face_z = center[2]

        # Bolt holes are evenly spaced around the bolt circle. For 4 bolts,
        # they sit at 45° + k * 90° (the standard NEMA convention) — start at
        # 45° so the pattern matches the diagonal frame orientation.
        start_deg = 45.0 if bolts == 4 else 0.0
        step_deg = 360.0 / bolts
        bolt_r = bolt_circle_d / 2.0

        # Cylinder direction: INTO the body, opposite the face normal.
        axis_dir = gp_Dir(0.0, 0.0, -nz_sign)

        if args.hole_through:
            length = _THROUGH_LEN_MM
            # Start a hair outside the face so the boolean cleanly punches.
            base_z = face_z + nz_sign * 1.0
        else:
            # Blind recess — conservative depth = max(hole_d, 5 mm).
            length = max(hole_d, 5.0)
            base_z = face_z

        for k in range(bolts):
            theta = math.radians(start_deg + k * step_deg)
            bx = cx + bolt_r * math.cos(theta)
            by = cy + bolt_r * math.sin(theta)
            ax = gp_Ax2(gp_Pnt(bx, by, base_z), axis_dir)
            cyl_mk = BRepPrimAPI_MakeCylinder(ax, hole_d / 2.0, length)
            cyl_mk.Build()
            if not cyl_mk.IsDone():
                raise RuntimeError(
                    f"motor_flange_bolt_pattern: bolt cylinder #{k} failed"
                )
            cut = BRepAlgoAPI_Cut(shape, cyl_mk.Shape())
            cut.Build()
            if not cut.IsDone():
                raise RuntimeError(
                    f"motor_flange_bolt_pattern: bolt #{k} cut failed"
                )
            shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "bolt_holes":      HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(shape), history=history)
