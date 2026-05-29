"""o_ring_groove_radial_spec — atomic.

Radial O-ring groove (cut on the OUTER cylindrical surface of a shaft / plug
along the given axis), sized from the AS568 catalog. This is the "rod seal"
or "piston seal" geometry where the ring sits in a circumferential groove
on a cylindrical surface.

Per ISO 3601-2 industrial dynamic seal practice, the groove for cross-section
W and ring ID has:
    groove_outer_diameter  = id + 2*W      (≈ o_ring.od)  — the shaft surface
    groove_inner_diameter  = id            (groove floor diameter on shaft)
    groove_axial_width     ≈ 1.36 * W      (room for the ring + tolerances)

Implementation reuses `revolve_pocket` semantics: build a rectangular profile
(width = catalog.face_groove_width_mm, depth = catalog.face_groove_depth_mm,
positioned at radius = od/2 on the +X half of the local XZ plane) and revolve
it 360° around the axis (origin = axis_origin + axial_position * axis_direction).

Args:
    axis_origin:        (x,y,z) reference point on the shaft axis.
    axis_direction:     (x,y,z) axis direction (normalized internally).
    o_ring_spec:        AS568 designation, e.g. "AS568-013".
    axial_position_mm:  signed distance along axis_direction from axis_origin
                        to the groove CENTERLINE.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(name):
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / "standards" / f"{name}.yaml").read_text())


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = v
    n = math.sqrt(x * x + y * y + z * z)
    if n < 1e-12:
        raise ValueError("axis_direction is zero")
    return x / n, y / n, z / n


@skill(
    name="o_ring_groove_radial_spec",
    category="modify/pocket",
    level="atomic",
    summary="Radial O-ring groove cut on an outer cylindrical surface, "
            "sized from the AS568 catalog (rod/piston seal geometry per "
            "ISO 3601-2). Selects ring by dash size; groove width and "
            "depth are computed from cross-section W.",
    selector_kinds=[],
    history_rules={
        "groove_walls":    HistoryRule.GENERATED_NEW,
        "groove_floor":    HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.o_ring_spec_known",
        "pc.axis_direction_nonzero",
    ],
    produces_features=["o_ring_groove", "annular_groove"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":   {"min_wall_mm": 0.3, "min_fillet_r_mm": 0.2},
        "turning":     {"min_wall_mm": 0.3,
                        "extras": {"single_point": True}},
    },
    failure_modes=[
        "fm.unknown_o_ring_spec",
        "fm.groove_misses_body",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class ORingGrooveRadialSpec(SkillBase):
    class Args(BaseModel):
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        o_ring_spec: str = Field(..., description='AS568 dash, e.g. "AS568-013"')
        axial_position_mm: float = 0.0

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        from phone_designer.skills.modify_pocket._sketch import RectangleSketch
        from phone_designer.skills.modify_pocket.revolve_pocket import (
            _build_revolved_solid,
        )

        catalog = _load("o_rings_as568")["o_rings"]
        if args.o_ring_spec not in catalog:
            raise ValueError(
                f"o_ring_groove_radial_spec: unknown o_ring_spec "
                f"'{args.o_ring_spec}'. Known: {sorted(catalog.keys())}"
            )
        spec = catalog[args.o_ring_spec]
        od = float(spec["od_mm"])
        groove_w = float(spec["face_groove_width_mm"])
        groove_d = float(spec["face_groove_depth_mm"])

        shape = body.wrapped if hasattr(body, "wrapped") else body

        # Centerline point of the groove on the shaft axis.
        dx, dy, dz = _normalize(args.axis_direction)
        cx = args.axis_origin[0] + args.axial_position_mm * dx
        cy = args.axis_origin[1] + args.axial_position_mm * dy
        cz = args.axis_origin[2] + args.axial_position_mm * dz

        # Rectangle profile sits on the +X half of local XZ plane:
        #   local x (radial)  ranges around r = od/2
        #   local y (axial)   ranges around 0  — centered on groove centerline
        # revolve_pocket maps local x -> world x (radial) and local y -> world z.
        # Center the profile at x = od/2 - groove_d/2 so the radial extent goes
        # from od/2 (outer surface, just touching) down to od/2 - groove_d.
        radial_center = (od / 2.0) - (groove_d / 2.0)
        profile = RectangleSketch(
            kind="rectangle",
            length_mm=groove_d,
            width_mm=groove_w,
            center_x_mm=radial_center,
            center_y_mm=0.0,
        )

        revolved = _build_revolved_solid(
            profile,
            (cx, cy, cz),
            (dx, dy, dz),
            360.0,
        )

        cut = BRepAlgoAPI_Cut(shape, revolved)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("o_ring_groove_radial_spec: cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "groove_walls":    HistoryRule.GENERATED_NEW,
                "groove_floor":    HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
