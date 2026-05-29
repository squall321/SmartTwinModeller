"""retaining_ring_groove — atomic.

Annular retaining-ring groove sized per DIN 471 (external, on a shaft) or
DIN 472 (internal, in a bore). Picks the closest catalog entry to the supplied
``shaft_or_bore_diameter_mm`` and cuts a groove of catalog
``groove_d_mm × groove_w_mm`` centered at ``axial_position_mm``.

Catalogs:
    - catalogs/retaining_rings/din471_external.yaml (shaft 4..30 mm)
    - catalogs/retaining_rings/din472_internal.yaml (bore  8..30 mm)

Args:
    axis_origin:                   (x,y,z) point on the shaft/bore axis.
    axis_direction:                (x,y,z) axis direction (normalized).
    shaft_or_bore_diameter_mm:     OD (external) or ID (internal).
    axial_position_mm:             signed distance from axis_origin along
                                   axis_direction at which the groove is
                                   centered.
    side:                          "external" → DIN 471 (groove_d < shaft_d);
                                   "internal" → DIN 472 (groove_d > bore_d).
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family, name):
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


def _closest_external_entry(shaft_d: float) -> dict:
    data = _load("retaining_rings", "din471_external")
    rings = data.get("rings", {})
    if not rings:
        raise RuntimeError("din471_external.yaml has no 'rings' section")
    best_key = min(rings, key=lambda k: abs(rings[k]["shaft_d_mm"] - shaft_d))
    return rings[best_key]


def _closest_internal_entry(bore_d: float) -> dict:
    data = _load("retaining_rings", "din472_internal")
    rings = data.get("rings", {})
    if not rings:
        raise RuntimeError("din472_internal.yaml has no 'rings' section")
    best_key = min(rings, key=lambda k: abs(rings[k]["bore_d_mm"] - bore_d))
    return rings[best_key]


def _transform_shape_to_axis(shape, axis_origin, axis_direction):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    ax, ay, az = axis_direction
    norm = math.sqrt(ax * ax + ay * ay + az * az)
    if norm < 1e-12:
        raise ValueError("axis_direction is zero")
    ax, ay, az = ax / norm, ay / norm, az / norm

    target = gp_Ax3(gp_Pnt(*axis_origin), gp_Dir(ax, ay, az))
    default = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    t = gp_Trsf()
    t.SetTransformation(target, default)
    xf = BRepBuilderAPI_Transform(shape, t, True)
    xf.Build()
    if not xf.IsDone():
        raise RuntimeError("retaining_ring_groove axis transform failed")
    return xf.Shape()


def _build_annular_ring_local(
    outer_r: float, inner_r: float, axial_center: float, axial_width: float,
):
    """outer cyl − inner cyl in the local frame, centered axially."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    z_min = axial_center - axial_width / 2.0
    ax = gp_Ax2(gp_Pnt(0.0, 0.0, z_min), gp_Dir(0.0, 0.0, 1.0))

    outer = BRepPrimAPI_MakeCylinder(ax, outer_r, axial_width).Shape()
    inner = BRepPrimAPI_MakeCylinder(ax, inner_r, axial_width).Shape()

    cut = BRepAlgoAPI_Cut(outer, inner)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError("annular ring construction failed")
    return cut.Shape()


@skill(
    name="retaining_ring_groove",
    category="modify/pocket",
    level="atomic",
    summary="Cut a DIN 471 (external) or DIN 472 (internal) retaining-ring groove. "
            "Picks the closest catalog entry to the supplied diameter and "
            "subtracts an annular volume of catalog groove_d × groove_w "
            "centered at the given axial position.",
    selector_kinds=[],
    history_rules={
        "groove_floor":    HistoryRule.GENERATED_NEW,
        "groove_walls":    HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.diameter_positive",
        "pc.axis_direction_nonzero",
    ],
    produces_features=["retaining_ring_groove", "annular_groove"],
    preserves=["axis_envelope"],
    manufacturing={
        "cnc_3axis": {"min_wall_mm": 0.3},
        "turning":   {"min_wall_mm": 0.2, "extras": {"single_point": True}},
    },
    failure_modes=[
        "fm.groove_too_deep_for_shaft",
        "fm.groove_too_deep_for_wall",
        "fm.groove_outside_body",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class RetainingRingGroove(SkillBase):
    class Args(BaseModel):
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        shaft_or_bore_diameter_mm: float = Field(gt=0.0, le=200.0)
        axial_position_mm: float = Field(default=0.0)
        side: Literal["external", "internal"] = "external"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        # Slight overshoot so the cutting solid fully spans the actual body
        # wall (avoids tangent-face Boolean fragility).
        BIAS = 0.05

        if args.side == "external":
            entry = _closest_external_entry(args.shaft_or_bore_diameter_mm)
            groove_d = float(entry["groove_d_mm"])
            groove_w = float(entry["groove_w_mm"])
            if groove_d <= 0.0 or groove_w <= 0.0:
                raise ValueError(
                    f"retaining_ring_groove(external): invalid catalog values "
                    f"groove_d={groove_d}, groove_w={groove_w}"
                )
            outer_r = args.shaft_or_bore_diameter_mm / 2.0 + BIAS
            inner_r = groove_d / 2.0
            if inner_r >= outer_r:
                raise ValueError(
                    "retaining_ring_groove(external): catalog groove_d "
                    f"({groove_d}) >= shaft_d ({args.shaft_or_bore_diameter_mm})"
                )
        else:  # internal
            entry = _closest_internal_entry(args.shaft_or_bore_diameter_mm)
            groove_d = float(entry["groove_d_mm"])
            groove_w = float(entry["groove_w_mm"])
            if groove_d <= args.shaft_or_bore_diameter_mm:
                raise ValueError(
                    "retaining_ring_groove(internal): catalog groove_d "
                    f"({groove_d}) <= bore_d ({args.shaft_or_bore_diameter_mm}); "
                    "internal groove must enlarge the bore"
                )
            outer_r = groove_d / 2.0
            inner_r = args.shaft_or_bore_diameter_mm / 2.0 - BIAS
            if inner_r <= 0.0 or inner_r >= outer_r:
                raise ValueError(
                    "retaining_ring_groove(internal): invalid radii "
                    f"(outer_r={outer_r}, inner_r={inner_r})"
                )

        ring_local = _build_annular_ring_local(
            outer_r=outer_r, inner_r=inner_r,
            axial_center=args.axial_position_mm, axial_width=groove_w,
        )
        ring_world = _transform_shape_to_axis(
            ring_local, args.axis_origin, args.axis_direction,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        cut = BRepAlgoAPI_Cut(shape, ring_world)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("retaining_ring_groove cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "groove_floor":    HistoryRule.GENERATED_NEW,
                "groove_walls":    HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
