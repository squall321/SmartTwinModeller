"""bearing_seat — atomic.

Catalog-keyed cylindrical bearing seat (housing bore) for a metric deep-groove
ball bearing per ``catalogs/bearings/deep_groove_ball.yaml``. Subtracts a
cylinder of (outer_d_mm × width_mm) along the given axis. Optionally drops a
narrower bore (``outer_d - 2*shoulder_drop``) past the seat that the bearing
rests against — a square retaining shoulder.

Distinct from ``bearing_bore`` (which keys off the legacy
``catalogs/standards/bearings_metric.yaml`` table): ``bearing_seat`` reads the
new ``catalogs/bearings/deep_groove_ball.yaml`` family, including the
miniature MR105 and thin-section 6900 series specified for Pack 5.

Args:
    axis_origin:    (x,y,z) world point on the bore axis; treated as the
                    *entry* face (mouth) of the seat.
    axis_direction: (x,y,z) bore direction (normalized internally).
    bearing_spec:   catalog key (e.g. "608", "MR105", "6900").
    with_shoulder:  if True, add a retaining shoulder past the seat depth.
    shoulder_drop_mm: radial drop of the retaining shoulder (so shoulder
                    bore Ø = outer_d - 2*shoulder_drop_mm).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family, name):
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


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
        raise RuntimeError("bearing_seat axis transform failed")
    return xf.Shape()


@skill(
    name="bearing_seat",
    category="modify/pocket",
    level="atomic",
    summary="Catalog-keyed cylindrical housing seat for a metric deep-groove ball "
            "bearing (deep_groove_ball.yaml: 608, 625, 60xx, MR105, 6900). "
            "Subtracts a cylinder of (outer_d × width) along the given axis; "
            "optional retaining shoulder past the seat.",
    selector_kinds=[],
    history_rules={
        "seat_wall":       HistoryRule.GENERATED_NEW,
        "seat_floor":      HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.bearing_spec_known",
        "pc.axis_direction_nonzero",
    ],
    produces_features=["bearing_seat", "cylindrical_pocket"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis": {"min_wall_mm": 0.5, "extras": {"H7_recommended": True}},
        "turning":   {"min_wall_mm": 0.3, "extras": {"H7_recommended": True}},
    },
    failure_modes=[
        "fm.unknown_bearing_spec",
        "fm.seat_exits_body",
        "fm.shoulder_drop_too_large",
    ],
    cost_hint=0.20,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class BearingSeat(SkillBase):
    class Args(BaseModel):
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, -1.0)
        bearing_spec: str = Field(..., description='catalog key, e.g. "608", "MR105"')
        with_shoulder: bool = False
        shoulder_drop_mm: float = Field(default=2.0, gt=0.0, le=20.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        catalog = _load("bearings", "deep_groove_ball")["bearings"]
        if args.bearing_spec not in catalog:
            raise ValueError(
                f"bearing_seat: unknown bearing_spec '{args.bearing_spec}'. "
                f"Known: {sorted(catalog.keys())}"
            )
        spec = catalog[args.bearing_spec]
        outer_d = float(spec["outer_d_mm"])
        width = float(spec["width_mm"])

        if args.with_shoulder and args.shoulder_drop_mm * 2.0 >= outer_d:
            raise ValueError(
                f"bearing_seat: shoulder_drop_mm ({args.shoulder_drop_mm}) too large "
                f"for bearing outer_d ({outer_d}); inner shoulder bore would be <= 0"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body

        # Build the seat cylinder in local frame (axis = +Z, base z=0).
        seat_local_axis = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
        seat = BRepPrimAPI_MakeCylinder(seat_local_axis, outer_d / 2.0, width).Shape()

        if args.with_shoulder:
            inner_r = (outer_d - 2.0 * args.shoulder_drop_mm) / 2.0
            # Shoulder continues past the seat — long enough to punch through
            # any reasonable housing wall thickness.
            shoulder_len = width + 200.0
            shoulder_axis = gp_Ax2(gp_Pnt(0, 0, width), gp_Dir(0, 0, 1))
            shoulder = BRepPrimAPI_MakeCylinder(
                shoulder_axis, inner_r, shoulder_len,
            ).Shape()
            fuse = BRepAlgoAPI_Fuse(seat, shoulder)
            fuse.Build()
            if not fuse.IsDone():
                raise RuntimeError("bearing_seat: seat+shoulder fuse failed")
            tool_local = fuse.Shape()
        else:
            tool_local = seat

        tool = _transform_shape_to_axis(tool_local, args.axis_origin, args.axis_direction)

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("bearing_seat: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "seat_wall":       HistoryRule.GENERATED_NEW,
                "seat_floor":      HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
