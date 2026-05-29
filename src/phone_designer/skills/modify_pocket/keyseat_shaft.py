"""keyseat_shaft — atomic.

Cut a parallel-key keyseat (rectangular slot) into the side of a shaft, sized
per DIN 6885 for the given shaft diameter.

Standards-driven: the closest DIN 6885 entry to ``shaft_diameter_mm`` provides
the key cross-section ``key_width_mm × key_height_mm`` and the shaft-side
keyseat depth ``shaft_keyseat_depth_mm`` (t1).

Geometry:
    In a local frame with axis = +Z, the slot is a box centered on the +X
    side of the shaft:
        x ∈ [shaft_r − t1, shaft_r + BIAS]
        y ∈ [−b/2, +b/2]
        z ∈ [axial_start_mm, axial_start_mm + length_mm]
    A small BIAS overshoot on the OD side keeps the cut from sitting tangent
    to the shaft surface. The slot is then rigid-transformed onto the
    world axis.

Args:
    axis_origin:       tuple (x,y,z) — origin on the shaft axis.
    axis_direction:    tuple (x,y,z) — shaft axis direction.
    shaft_diameter_mm: float — shaft OD (catalog lookup key).
    axial_start_mm:    float — signed distance from axis_origin along axis at
                       which the keyseat begins.
    length_mm:         float — axial length of the keyseat.
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


def _closest_din6885_entry(shaft_d: float) -> dict:
    data = _load("keys_din6885")
    keys = data.get("keys", {})
    if not keys:
        raise RuntimeError("keys_din6885.yaml has no 'keys' section")
    best_key = min(keys, key=lambda k: abs(keys[k]["shaft_d_mm"] - shaft_d))
    return keys[best_key]


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


def _build_slot_local(x_min, x_max, y_half_width, z_min, z_max):
    """Build an axis-aligned slot box in the local frame."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    p1 = gp_Pnt(x_min, -y_half_width, z_min)
    p2 = gp_Pnt(x_max,  y_half_width, z_max)
    return BRepPrimAPI_MakeBox(p1, p2).Shape()


@skill(
    name="keyseat_shaft",
    category="modify/pocket",
    level="atomic",
    summary="Cut a parallel-key keyseat into a shaft per DIN 6885 (Form A). "
            "The slot width and depth come from the closest catalog entry for "
            "the given shaft_diameter_mm; the slot extends along the shaft "
            "axis from axial_start_mm for length_mm.",
    selector_kinds=[],
    history_rules={
        "keyseat_floor":   HistoryRule.GENERATED_NEW,
        "keyseat_walls":   HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.diameter_positive",
        "pc.position_inside_or_on_body",
    ],
    produces_features=["keyseat", "rect_slot"],
    preserves=["axis_envelope"],
    manufacturing={
        "cnc_3axis": {"min_wall_mm": 0.3, "extras": {"end_mill_required": True}},
        "milling":   {"min_wall_mm": 0.2},
    },
    failure_modes=[
        "fm.keyseat_too_deep_for_shaft",
        "fm.keyseat_outside_body",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class KeyseatShaft(SkillBase):
    class Args(BaseModel):
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        shaft_diameter_mm: float = Field(gt=0.0, le=200.0)
        axial_start_mm: float = Field(default=0.0)
        length_mm: float = Field(gt=0.0, le=500.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        entry = _closest_din6885_entry(args.shaft_diameter_mm)
        b = float(entry["key_width_mm"])
        t1 = float(entry["shaft_keyseat_depth_mm"])

        if b <= 0.0 or t1 <= 0.0:
            raise ValueError(
                f"keyseat_shaft: invalid catalog values b={b}, t1={t1}"
            )

        shaft_r = args.shaft_diameter_mm / 2.0
        if t1 >= shaft_r:
            raise ValueError(
                f"keyseat_shaft: keyseat depth t1 ({t1}) >= shaft radius "
                f"({shaft_r}); slot would cross axis"
            )

        BIAS = 0.05
        x_min = shaft_r - t1
        x_max = shaft_r + BIAS
        z_min = args.axial_start_mm
        z_max = args.axial_start_mm + args.length_mm

        slot_local = _build_slot_local(x_min, x_max, b / 2.0, z_min, z_max)
        slot_world = _transform_shape_to_axis(
            slot_local, args.axis_origin, args.axis_direction,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        cut = BRepAlgoAPI_Cut(shape, slot_world)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("keyseat_shaft cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "keyseat_floor":   HistoryRule.GENERATED_NEW,
                "keyseat_walls":   HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
