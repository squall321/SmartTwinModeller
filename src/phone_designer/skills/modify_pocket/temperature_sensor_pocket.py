"""temperature_sensor_pocket — atomic.

Catalog-driven recessed pocket for a temperature sensor on a host face.
Reads body footprint (w × h × t and shape) from
catalogs/sensors_health/temperature.yaml keyed by `sensor_kind` (Literal)
and subtracts either a cylindrical (circular package — TO-39 style) or
rectangular (DFN / SMD package) pocket sized to fit the sensor body plus
a per-side `clearance_mm`.

v1 supports planar +Z / -Z target faces.
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
    name="temperature_sensor_pocket",
    category="modify/pocket",
    level="atomic",
    summary="Catalog-driven recessed pocket for a temperature sensor "
            "(MLX90614 TO-39, Si705x DFN, NTC SMD). Cylindrical pocket for "
            "circular packages, rectangular pocket for SMD. Reads footprint "
            "from catalogs/sensors_health/temperature.yaml. v1 supports "
            "planar +Z/-Z faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "sensor_walls":    HistoryRule.GENERATED_NEW,
        "sensor_floor":    HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.sensor_kind_in_catalog",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["temperature_sensor_pocket"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"flat_floor_required": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.sensor_kind_unknown",
        "fm.pocket_exits_body",
    ],
    cost_hint=0.14,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class TemperatureSensorPocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of pocket center, offset from face centroid.",
        )
        sensor_kind: Literal["mlx90614", "si705x", "ntc_0603"] = Field(
            default="mlx90614",
            description="Temperature sensor catalog key in temperature.yaml.",
        )
        clearance_override_mm: float | None = Field(
            default=None,
            description="Override catalog clearance_mm (per side).",
        )
        catalog_family: str = Field(
            default="temperature",
            description="Catalog filename stem under catalogs/sensors_health/.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        catalog = _load("sensors_health", args.catalog_family)
        sensors = catalog.get("sensors", {})
        if args.sensor_kind not in sensors:
            raise ValueError(
                f"temperature_sensor_pocket: unknown sensor_kind "
                f"'{args.sensor_kind}' in family '{args.catalog_family}' — "
                f"catalog keys: {sorted(sensors.keys())}"
            )
        entry = sensors[args.sensor_kind]
        body_w = float(entry["body_w_mm"])
        body_h = float(entry["body_h_mm"])
        body_t = float(entry["body_t_mm"])
        is_circular = bool(entry.get("is_circular", False))
        clearance = (
            float(args.clearance_override_mm)
            if args.clearance_override_mm is not None
            else float(entry.get("clearance_mm", 0.1))
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"temperature_sensor_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "temperature_sensor_pocket v1: planar +Z/-Z target faces only"
            )
        nz_sign = 1.0 if normal[2] > 0 else -1.0
        axis_dir = gp_Dir(0.0, 0.0, -nz_sign)

        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        cz = center[2]

        if is_circular:
            pocket_d = body_w + 2.0 * clearance  # body_w == body_h for circular
            base = gp_Pnt(cx, cy, cz)
            ax2 = gp_Ax2(base, axis_dir)
            mk = BRepPrimAPI_MakeCylinder(ax2, pocket_d / 2.0, body_t)
            mk.Build()
            if not mk.IsDone():
                raise RuntimeError(
                    "temperature_sensor_pocket: cylindrical pocket build failed"
                )
            tool_shape = mk.Shape()
        else:
            pocket_w = body_w + 2.0 * clearance
            pocket_h = body_h + 2.0 * clearance
            origin = gp_Pnt(
                cx - pocket_w / 2.0,
                cy - pocket_h / 2.0,
                cz - (body_t if nz_sign > 0 else 0.0),
            )
            mk = BRepPrimAPI_MakeBox(origin, pocket_w, pocket_h, body_t)
            mk.Build()
            if not mk.IsDone():
                raise RuntimeError(
                    "temperature_sensor_pocket: rectangular pocket build failed"
                )
            tool_shape = mk.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool_shape)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError(
                "temperature_sensor_pocket: boolean cut failed"
            )
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "sensor_walls":    HistoryRule.GENERATED_NEW,
                "sensor_floor":    HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
