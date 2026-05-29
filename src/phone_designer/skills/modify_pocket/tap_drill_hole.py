"""tap_drill_hole — atomic. Tap-drill (pre-tap) hole per ISO 261/262.

Drills the *core* hole that a tap will subsequently cut threads into.
Diameter = ``tap_drill_mm`` for the chosen metric thread, derived from
``outer_d - pitch`` (the ISO basic rule).

For the clearance (pass-through) counterpart see :mod:`clearance_hole`.
"""
from __future__ import annotations

from typing import Any

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


def _load(name):
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / "standards" / f"{name}.yaml").read_text())


def _thread_entry(thread_spec: str) -> dict:
    data = _load("threads_metric")
    threads = data.get("threads", {})
    if thread_spec not in threads:
        raise ValueError(
            f"unknown thread_spec '{thread_spec}' — available: {sorted(threads)}"
        )
    return threads[thread_spec]


@skill(
    name="tap_drill_hole",
    category="modify/pocket",
    level="atomic",
    summary="Drill a tap-drill core hole sized for ISO metric tapping "
            "(diameter = outer_d - pitch from threads_metric.yaml). Blind only.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":          HistoryRule.MODIFIED_INHERIT,
        "result_cylinder_face": HistoryRule.GENERATED_NEW,
        "consumed_volume":      HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.position_inside_or_on_body",
    ],
    produces_features=["tap_drill_hole", "cylindrical_hole"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5, "extras": {"max_aspect_ratio": 10.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.hole_exits_body", "fm.hole_too_close_to_edge"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class TapDrillHole(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of hole axis (anchored at face center)",
        )
        thread_spec: str = Field(description="e.g. 'M3', 'M4', ... — key in threads_metric.yaml")
        depth_mm: float = Field(gt=0, le=200,
                                 description="blind tap-drill depth into body")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        entry = _thread_entry(args.thread_spec)
        if "tap_drill_mm" not in entry:
            raise ValueError(
                f"thread '{args.thread_spec}' has no tap_drill_mm in catalog"
            )
        d_tap = float(entry["tap_drill_mm"])

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"tap_drill_hole: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "tap_drill_hole v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        wx = center[0] + args.position_xy[0]
        wy = center[1] + args.position_xy[1]
        overshoot = 0.5
        base_z = center[2] + z_sign * overshoot
        direction = gp_Dir(0.0, 0.0, -z_sign)
        ax = gp_Ax2(gp_Pnt(wx, wy, base_z), direction)
        mk = BRepPrimAPI_MakeCylinder(ax, d_tap / 2.0, args.depth_mm + overshoot)
        mk.Build()
        if not mk.IsDone():
            raise RuntimeError(
                f"tap_drill_hole: cylinder build failed at ({wx},{wy})"
            )

        cut = BRepAlgoAPI_Cut(shape, mk.Shape())
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("tap_drill_hole: boolean cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":          HistoryRule.MODIFIED_INHERIT,
                "result_cylinder_face": HistoryRule.GENERATED_NEW,
                "consumed_volume":      HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
