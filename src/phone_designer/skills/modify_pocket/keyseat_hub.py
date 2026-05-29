"""keyseat_hub — atomic.

Cut a hub-side keyseat (rectangular slot extending radially out from the
bore wall) per DIN 6885 Form A, sized for the given bore diameter.

Standards-driven: the closest DIN 6885 entry to ``bore_diameter_mm`` provides
the key width ``key_width_mm`` (b) and the hub-side keyseat depth
``hub_keyseat_depth_mm`` (t2).

Geometry:
    In a local frame with axis = +Z, the slot is a box on the +X side of the
    bore:
        x ∈ [bore_r − BIAS, bore_r + t2]
        y ∈ [−b/2, +b/2]
        z ∈ [0, length_mm]            if through=False
        z ∈ [−EXTEND, length_mm + EXTEND]  if through=True (passes through
                                            the hub regardless of its axial
                                            extent)
    A small BIAS undershoot of the bore radius keeps the cut from sitting
    tangent to the bore wall. The slot is rigid-transformed onto the world
    axis.

Args:
    axis_origin:      tuple (x,y,z) — point on the bore axis.
    axis_direction:   tuple (x,y,z) — bore axis direction.
    bore_diameter_mm: float — bore ID (catalog lookup key).
    length_mm:        float — keyseat axial length when through=False, or the
                      nominal hub length when through=True (the cut still
                      extends past both ends).
    through:          bool  — when True, extend the slot well past the hub
                      faces so it passes all the way through.
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


def _closest_din6885_entry(bore_d: float) -> dict:
    data = _load("keys_din6885")
    keys = data.get("keys", {})
    if not keys:
        raise RuntimeError("keys_din6885.yaml has no 'keys' section")
    best_key = min(keys, key=lambda k: abs(keys[k]["shaft_d_mm"] - bore_d))
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
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    p1 = gp_Pnt(x_min, -y_half_width, z_min)
    p2 = gp_Pnt(x_max,  y_half_width, z_max)
    return BRepPrimAPI_MakeBox(p1, p2).Shape()


@skill(
    name="keyseat_hub",
    category="modify/pocket",
    level="atomic",
    summary="Cut a hub-side keyseat (radial rectangular slot in the bore wall) "
            "per DIN 6885 Form A, sized from the catalog for the given "
            "bore_diameter_mm. When through=True, the slot extends past the "
            "hub faces so it passes all the way through.",
    selector_kinds=[],
    history_rules={
        "keyseat_outer":   HistoryRule.GENERATED_NEW,
        "keyseat_walls":   HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.diameter_positive",
        "pc.position_inside_or_on_body",
    ],
    produces_features=["keyseat", "rect_slot"],
    preserves=["bore_envelope"],
    manufacturing={
        "cnc_3axis": {"min_wall_mm": 0.3, "extras": {"broach_or_em": True}},
        "broaching": {"min_wall_mm": 0.2},
    },
    failure_modes=[
        "fm.keyseat_too_deep_for_wall",
        "fm.keyseat_outside_body",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class KeyseatHub(SkillBase):
    class Args(BaseModel):
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        bore_diameter_mm: float = Field(gt=0.0, le=200.0)
        length_mm: float = Field(gt=0.0, le=500.0)
        through: bool = True

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        entry = _closest_din6885_entry(args.bore_diameter_mm)
        b = float(entry["key_width_mm"])
        t2 = float(entry["hub_keyseat_depth_mm"])

        if b <= 0.0 or t2 <= 0.0:
            raise ValueError(
                f"keyseat_hub: invalid catalog values b={b}, t2={t2}"
            )

        bore_r = args.bore_diameter_mm / 2.0

        BIAS = 0.05
        x_min = bore_r - BIAS
        x_max = bore_r + t2
        if args.through:
            # Push well past both faces. 1000 mm is far greater than any
            # plausible hub axial length in this codebase and the cut is
            # bounded by the body itself.
            EXTEND = 1000.0
            z_min = -EXTEND
            z_max = args.length_mm + EXTEND
        else:
            z_min = 0.0
            z_max = args.length_mm

        slot_local = _build_slot_local(x_min, x_max, b / 2.0, z_min, z_max)
        slot_world = _transform_shape_to_axis(
            slot_local, args.axis_origin, args.axis_direction,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        cut = BRepAlgoAPI_Cut(shape, slot_world)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("keyseat_hub cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "keyseat_outer":   HistoryRule.GENERATED_NEW,
                "keyseat_walls":   HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
