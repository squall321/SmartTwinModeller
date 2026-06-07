"""countersink_hole — atomic. 90° countersink hole per ISO 10642 prep.

Cuts a two-stage hole:
  - upper 90° conical cup of top diameter ``countersink_d_iso10642_mm``
    (recesses an ISO 10642 flat-head screw),
  - lower clearance shaft of diameter ``clearance_<fit>_mm`` extending a
    further ``depth_mm`` into the body.

The cone half-angle is 45° (90° included), so cone depth = D / 2.
"""
from __future__ import annotations

import math
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
    name="countersink_hole",
    category="modify/pocket",
    level="atomic",
    summary="ISO 10642 countersink hole — 90° conical cup on top + clearance shaft "
            "of selected fit extending depth_mm below.",
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
    produces_features=["countersink_hole", "conical_recess"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5, "extras": {"max_aspect_ratio": 10.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.hole_exits_body", "fm.countersink_too_close_to_edge"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class CountersinkHole(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of hole axis (anchored at face center)",
        )
        thread_spec: str = Field(description="e.g. 'M3', 'M4', ... — key in threads_metric.yaml")
        fit: Literal["close", "medium", "coarse"] = "medium"
        depth_mm: float = Field(gt=0, le=10000,
                                 description="clearance-shaft depth BELOW the cone apex")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        entry = _thread_entry(args.thread_spec)
        d_clear = float(entry[f"clearance_{args.fit}_mm"])
        d_cs = float(entry["countersink_d_iso10642_mm"])
        angle_deg = float(entry.get("countersink_angle_deg", 90))

        # Cone half-angle (from axis). 90° included → 45° half-angle → depth = R / tan(45°) = R.
        half_angle = math.radians(angle_deg / 2.0)
        cone_depth = (d_cs / 2.0) / math.tan(half_angle)

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"countersink_hole: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "countersink_hole v1: planar +Z/-Z target faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        wx = center[0] + args.position_xy[0]
        wy = center[1] + args.position_xy[1]
        face_z = center[2]
        direction = gp_Dir(0.0, 0.0, -z_sign)

        # Cone: base radius d_cs/2 at the face, apex (r=0) at depth cone_depth into body.
        # BRepPrimAPI_MakeCone(ax, r1_at_base, r2_at_top, height) where the axis
        # points from base toward top. We set the axis pointing INTO the body
        # (-z_sign), so "base" sits at the outer face and "top" is the apex.
        cone_ax = gp_Ax2(gp_Pnt(wx, wy, face_z), direction)
        cone_mk = BRepPrimAPI_MakeCone(cone_ax, d_cs / 2.0, 0.0, cone_depth)
        cone_mk.Build()
        if not cone_mk.IsDone():
            raise RuntimeError("countersink_hole: cone build failed")
        cone_shape = cone_mk.Shape()

        # Clearance shaft: starts at the apex, continues depth_mm deeper.
        # A small overlap with the cone is fine for the boolean op.
        shaft_top_z = face_z - z_sign * cone_depth
        overlap = 0.1
        shaft_start_z = shaft_top_z + z_sign * overlap
        shaft_ax = gp_Ax2(gp_Pnt(wx, wy, shaft_start_z), direction)
        shaft_mk = BRepPrimAPI_MakeCylinder(
            shaft_ax, d_clear / 2.0, args.depth_mm + overlap,
        )
        shaft_mk.Build()
        if not shaft_mk.IsDone():
            raise RuntimeError("countersink_hole: shaft cylinder build failed")
        shaft_shape = shaft_mk.Shape()

        # Fuse the cone + shaft into one tool, then subtract once.
        fuser = BRepAlgoAPI_Fuse(cone_shape, shaft_shape)
        fuser.Build()
        if not fuser.IsDone():
            raise RuntimeError("countersink_hole: tool fuse failed")
        tool = fuser.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("countersink_hole: boolean cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":          HistoryRule.MODIFIED_INHERIT,
                "result_cylinder_face": HistoryRule.GENERATED_NEW,
                "consumed_volume":      HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
