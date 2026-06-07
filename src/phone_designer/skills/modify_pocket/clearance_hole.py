"""clearance_hole — atomic. Clearance through/blind hole per ISO 273.

Drills a straight cylindrical hole sized to the chosen clearance fit
(close / medium / coarse) for a given metric thread spec (e.g. "M3").
Diameter is looked up from ``catalogs/standards/threads_metric.yaml``.

This is the *unthreaded* hole that the shank of a screw passes through.
For the *tapped* counterpart see :mod:`tap_drill_hole`.
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
    name="clearance_hole",
    category="modify/pocket",
    level="atomic",
    summary="Drill an ISO 273 clearance hole on a planar face for a given metric "
            "thread spec (close/medium/coarse fit). Pass-through shank hole only — "
            "no thread, no counterbore, no countersink.",
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
    produces_features=["clearance_hole", "cylindrical_hole"],
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
class ClearanceHole(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of hole axis (anchored at face center)",
        )
        thread_spec: str = Field(description="e.g. 'M3', 'M4', ... — key in threads_metric.yaml")
        fit: Literal["close", "medium", "coarse"] = "medium"
        depth_mm: float = Field(gt=0, le=10000,
                                 description="blind depth into body; ignored if through=True")
        through: bool = False

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        entry = _thread_entry(args.thread_spec)
        d_key = f"clearance_{args.fit}_mm"
        if d_key not in entry:
            raise ValueError(
                f"thread '{args.thread_spec}' has no {d_key} in catalog"
            )
        d_clear = float(entry[d_key])

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"clearance_hole: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "clearance_hole v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0

        # Depth: blind=depth_mm; through=body bbox diagonal × 1.5 to guarantee exit.
        if args.through:
            bb = Bnd_Box()
            BRepBndLib.AddOptimal_s(shape, bb)
            xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
            diag = ((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2) ** 0.5
            depth = diag * 1.5
        else:
            depth = args.depth_mm

        wx = center[0] + args.position_xy[0]
        wy = center[1] + args.position_xy[1]
        overshoot = 0.5  # start tool above face for clean entry edge
        base_z = center[2] + z_sign * overshoot
        direction = gp_Dir(0.0, 0.0, -z_sign)
        ax = gp_Ax2(gp_Pnt(wx, wy, base_z), direction)
        mk = BRepPrimAPI_MakeCylinder(ax, d_clear / 2.0, depth + overshoot)
        mk.Build()
        if not mk.IsDone():
            raise RuntimeError(
                f"clearance_hole: cylinder build failed at ({wx},{wy})"
            )

        cut = BRepAlgoAPI_Cut(shape, mk.Shape())
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("clearance_hole: boolean cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":          HistoryRule.MODIFIED_INHERIT,
                "result_cylinder_face": HistoryRule.GENERATED_NEW,
                "consumed_volume":      HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
