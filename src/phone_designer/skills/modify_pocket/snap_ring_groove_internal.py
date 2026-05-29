"""snap_ring_groove_internal — atomic.

Cut an internal snap-ring groove (DIN 472 retaining ring seat) inside a bore.

Standards-driven: looks up the closest DIN 472 ring for the given bore
diameter and subtracts an annular volume of catalog ``groove_d`` (outer
diameter of the cut, > bore_d) × catalog ``groove_width_mm`` centered at the
given axial position.

Geometry:
    The cut volume is an annular cylinder whose inner radius slightly
    *under-shoots* the bore wall (so the cut intersects the bore cleanly)
    and whose outer radius equals the catalog ``groove_d / 2``. The annulus
    is built in a local frame (axis = +Z) and rigid-transformed onto the
    world axis.

Args mirror :class:`SnapRingGrooveExternal` but the parameter is the
*bore* diameter rather than the shaft OD.
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


def _closest_din472_entry(bore_d: float) -> dict:
    data = _load("snap_rings_din472")
    rings = data.get("rings", {})
    if not rings:
        raise RuntimeError("snap_rings_din472.yaml has no 'rings' section")
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
        raise RuntimeError("axis transform failed")
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
    name="snap_ring_groove_internal",
    category="modify/pocket",
    level="atomic",
    summary="Cut an internal snap-ring groove inside a bore, sized per DIN 472 "
            "for the given bore diameter. Selects the closest catalog entry "
            "and subtracts an annular volume whose outer diameter equals the "
            "catalog groove_d (> bore_d) and whose axial width matches the "
            "catalog groove_width_mm.",
    selector_kinds=[],
    history_rules={
        "groove_outer":    HistoryRule.GENERATED_NEW,
        "groove_walls":    HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.diameter_positive",
        "pc.position_inside_or_on_body",
    ],
    produces_features=["snap_ring_groove", "annular_groove"],
    preserves=["bore_envelope"],
    manufacturing={
        "cnc_3axis": {"min_wall_mm": 0.3},
        "turning":   {"min_wall_mm": 0.2, "extras": {"single_point": True}},
    },
    failure_modes=[
        "fm.groove_too_deep_for_wall",
        "fm.groove_outside_body",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class SnapRingGrooveInternal(SkillBase):
    class Args(BaseModel):
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        bore_diameter_mm: float = Field(gt=0.0, le=200.0)
        axial_position_mm: float = Field(default=0.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        entry = _closest_din472_entry(args.bore_diameter_mm)
        groove_d = float(entry["groove_d_mm"])
        groove_w = float(entry["groove_width_mm"])

        if groove_d <= args.bore_diameter_mm:
            raise ValueError(
                "snap_ring_groove_internal: catalog groove_d "
                f"({groove_d}) <= bore_d ({args.bore_diameter_mm}); "
                "internal groove must enlarge the bore"
            )

        # Slight undershoot of the bore so the cut overlaps the bore wall and
        # doesn't sit tangent to it (Boolean robustness, mirror of the
        # external case).
        BIAS = 0.05
        outer_r = groove_d / 2.0
        inner_r = args.bore_diameter_mm / 2.0 - BIAS
        if inner_r <= 0.0 or inner_r >= outer_r:
            raise ValueError(
                "snap_ring_groove_internal: invalid radii "
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
            raise RuntimeError("snap_ring_groove_internal cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "groove_outer":    HistoryRule.GENERATED_NEW,
                "groove_walls":    HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
