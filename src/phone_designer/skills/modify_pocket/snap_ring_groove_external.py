"""snap_ring_groove_external — atomic.

Cut an external snap-ring groove (DIN 471 retaining ring seat) on a shaft.

The skill is *standards-driven*: given the shaft diameter, it looks up the
closest DIN 471 ring size and cuts an annular groove of the corresponding
``groove_d_mm`` (depth) and ``groove_width_mm`` (axial width), centered on
``axial_position_mm`` measured along the axis from ``axis_origin``.

Geometry:
    The cut volume is a short annular cylinder:
        outer radius  = shaft_diameter / 2  + small overshoot (BIAS) so the
                        cut clears the actual cylinder face cleanly.
        inner radius  = groove_d / 2 (from catalog).
        axial extent  = [axial_position - w/2, axial_position + w/2] along
                        axis_direction.
    It is built in a local frame (axis = +Z) and transformed onto the world
    axis via a rigid transform (mirrors the helical_thread_external pattern).

Args:
    axis_origin:       tuple (x,y,z) — point on the shaft axis (the axial
                       reference for axial_position_mm).
    axis_direction:    tuple (x,y,z) — shaft axis direction (normalized).
    shaft_diameter_mm: float — shaft OD; used to pick the catalog entry.
    axial_position_mm: float — signed distance from axis_origin along
                       axis_direction at which the groove is centered.
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


def _closest_din471_entry(shaft_d: float) -> dict:
    data = _load("snap_rings_din471")
    rings = data.get("rings", {})
    if not rings:
        raise RuntimeError("snap_rings_din471.yaml has no 'rings' section")
    best_key = min(rings, key=lambda k: abs(rings[k]["shaft_d_mm"] - shaft_d))
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
        raise RuntimeError("axis transform failed")
    return xf.Shape()


def _build_annular_ring_local(
    outer_r: float, inner_r: float, axial_center: float, axial_width: float,
):
    """Build an annular cylindrical shell volume in the local frame.

    The shell occupies z ∈ [axial_center - w/2, axial_center + w/2],
    r ∈ [inner_r, outer_r]. Built as (outer cylinder) − (inner cylinder).
    """
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
    name="snap_ring_groove_external",
    category="modify/pocket",
    level="atomic",
    summary="Cut an external snap-ring groove on a shaft, sized per DIN 471 for "
            "the given shaft diameter. Selects the closest catalog entry and "
            "subtracts an annular volume of catalog groove_d × groove_width "
            "centered at the given axial position along the shaft axis.",
    selector_kinds=[],
    history_rules={
        "groove_floor":    HistoryRule.GENERATED_NEW,
        "groove_walls":    HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.diameter_positive",
        "pc.position_inside_or_on_body",
    ],
    produces_features=["snap_ring_groove", "annular_groove"],
    preserves=["axis_envelope"],
    manufacturing={
        "cnc_3axis": {"min_wall_mm": 0.3},
        "turning":   {"min_wall_mm": 0.2, "extras": {"single_point": True}},
    },
    failure_modes=[
        "fm.groove_too_deep_for_shaft",
        "fm.groove_outside_body",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class SnapRingGrooveExternal(SkillBase):
    class Args(BaseModel):
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        shaft_diameter_mm: float = Field(gt=0.0, le=200.0)
        axial_position_mm: float = Field(default=0.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        entry = _closest_din471_entry(args.shaft_diameter_mm)
        groove_d = float(entry["groove_d_mm"])
        groove_w = float(entry["groove_width_mm"])

        if groove_d <= 0.0 or groove_w <= 0.0:
            raise ValueError(
                f"snap_ring_groove_external: invalid catalog values "
                f"groove_d={groove_d}, groove_w={groove_w}"
            )

        # Overshoot the cylinder surface slightly so the cutting solid fully
        # spans the actual body wall (avoids tangent-face Boolean fragility).
        BIAS = 0.05
        outer_r = args.shaft_diameter_mm / 2.0 + BIAS
        inner_r = groove_d / 2.0
        if inner_r >= outer_r:
            raise ValueError(
                "snap_ring_groove_external: catalog groove_d "
                f"({groove_d}) >= shaft_d ({args.shaft_diameter_mm})"
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
            raise RuntimeError("snap_ring_groove_external cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "groove_floor":    HistoryRule.GENERATED_NEW,
                "groove_walls":    HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
