"""haptic_motor_mount — atomic.

Catalog-driven cylindrical cradle / seat for a haptic vibration motor
(coin-style LRA or bar-style ERM). The mount is a hollow cylindrical boss
sized to the motor's outer diameter plus a per-side radial clearance:

    outer_d = motor_d + 2 * (seat_clearance + wall_thickness)
    inner_d = motor_d + 2 * seat_clearance
    height  = motor_length

Reads motor footprint from catalogs/sensors_health/haptic_motors.yaml
keyed by `motor_kind` (Literal). v1 supports planar +Z / -Z target faces.
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


def _load(family: str, name: str):
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


@skill(
    name="haptic_motor_mount",
    category="modify/boss",
    level="atomic",
    summary="Catalog-driven cylindrical mount cradle for a haptic motor "
            "(LRA / ERM). Hollow boss sized to the motor diameter from "
            "catalogs/sensors_health/haptic_motors.yaml plus radial "
            "clearance and wall thickness. v1 supports planar +Z/-Z faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":         HistoryRule.MODIFIED_INHERIT,
        "haptic_mount_solid":  HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.motor_kind_in_catalog",
    ],
    produces_features=["haptic_motor_mount"],
    preserves=["outer_envelope_outside_boss"],
    manufacturing={
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "cnc_3axis":         {"min_wall_mm": 0.5},
    },
    failure_modes=[
        "fm.motor_kind_unknown",
        "fm.wall_thickness_negative",
    ],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class HapticMotorMount(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of mount center, offset from face centroid.",
        )
        motor_kind: Literal["lra_10x3", "lra_8x3", "erm_4x14", "erm_6x14"] = Field(
            default="lra_10x3",
            description="Haptic motor catalog key in haptic_motors.yaml.",
        )
        wall_thickness_mm: float = Field(
            default=1.0, gt=0, le=10.0,
            description="boss wall thickness around the motor seat",
        )
        catalog_family: str = Field(
            default="haptic_motors",
            description="Catalog filename stem under catalogs/sensors_health/.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        catalog = _load("sensors_health", args.catalog_family)
        motors = catalog.get("motors", {})
        if args.motor_kind not in motors:
            raise ValueError(
                f"haptic_motor_mount: unknown motor_kind '{args.motor_kind}' "
                f"in family '{args.catalog_family}' — catalog keys: "
                f"{sorted(motors.keys())}"
            )
        entry = motors[args.motor_kind]
        motor_d = float(entry["diameter_mm"])
        motor_l = float(entry["length_mm"])
        seat_clearance = float(entry.get("seat_clearance_mm", 0.15))

        if args.wall_thickness_mm <= 0.0:
            raise ValueError(
                f"haptic_motor_mount: wall_thickness_mm "
                f"({args.wall_thickness_mm}) must be > 0"
            )

        inner_d = motor_d + 2.0 * seat_clearance
        outer_d = inner_d + 2.0 * args.wall_thickness_mm
        height = motor_l

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"haptic_motor_mount: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "haptic_motor_mount v1: planar +Z/-Z target faces only"
            )
        z_sign = 1.0 if normal[2] > 0 else -1.0
        direction = gp_Dir(0.0, 0.0, z_sign)

        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        cz = center[2]

        # Outer boss cylinder (grows outward along face normal).
        outer_base = gp_Pnt(cx, cy, cz)
        outer_ax = gp_Ax2(outer_base, direction)
        outer_mk = BRepPrimAPI_MakeCylinder(outer_ax, outer_d / 2.0, height)
        outer_mk.Build()
        if not outer_mk.IsDone():
            raise RuntimeError("haptic_motor_mount: outer cylinder build failed")
        outer_shape = outer_mk.Shape()

        # Inner motor seat — slightly longer for clean boolean cut.
        overshoot = 1.0
        inner_base = gp_Pnt(cx, cy, cz - z_sign * (overshoot / 2.0))
        inner_ax = gp_Ax2(inner_base, direction)
        inner_mk = BRepPrimAPI_MakeCylinder(
            inner_ax, inner_d / 2.0, height + overshoot,
        )
        inner_mk.Build()
        if not inner_mk.IsDone():
            raise RuntimeError("haptic_motor_mount: inner cylinder build failed")
        inner_shape = inner_mk.Shape()

        cut = BRepAlgoAPI_Cut(outer_shape, inner_shape)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("haptic_motor_mount: outer - inner cut failed")
        tool = cut.Shape()

        fuse = BRepAlgoAPI_Fuse(shape, tool)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("haptic_motor_mount: body fuse failed")
        new_shape = fuse.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":        HistoryRule.MODIFIED_INHERIT,
                "haptic_mount_solid": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
