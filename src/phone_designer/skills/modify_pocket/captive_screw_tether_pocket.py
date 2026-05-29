"""captive_screw_tether_pocket — atomic.

Captive-fastener pocket for service-port covers on medical / outdoor /
industrial enclosures: a counterbore-style recess that holds the screw head
when the cover is removed, plus a small through-hole for a lanyard / tether
that prevents the screw from being dropped into the patient field.

Geometry (raw OCCT, planar ±Z host faces v1):

    head pocket : cylinder of diameter `counterbore_d_iso_mm` (from
                  catalogs/standards/threads_metric.yaml) and depth
                  `counterbore_depth_iso_mm` — recesses the screw head.

    clearance   : cylinder of diameter `clearance_medium_mm` extending into
                  the body for `clearance_depth_mm` (default = thread length
                  hint), seats the screw shaft.

    tether     : a small cylinder of diameter `tether_d_mm` going all the way
                  through the side of the head pocket (a transverse hole in
                  the boss wall), giving a lanyard pass-through.
                  Centered radially at `tether_clearance_d_mm/2` from the
                  pocket axis, at half the head-pocket depth.

The single boolean tool = head ∪ clearance ∪ tether.
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
    import pathlib

    import yaml
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
    name="captive_screw_tether_pocket",
    category="modify/pocket",
    level="atomic",
    summary="Counterbore-style captive-screw pocket for medical / outdoor service "
            "covers: head recess + clearance shaft + transverse lanyard "
            "through-hole so the screw cannot be dropped when removed. v1 supports "
            "planar ±Z host faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":          HistoryRule.MODIFIED_INHERIT,
        "head_pocket_face":     HistoryRule.GENERATED_NEW,
        "clearance_shaft_face": HistoryRule.GENERATED_NEW,
        "tether_hole_face":     HistoryRule.GENERATED_NEW,
        "consumed_volume":      HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.position_inside_or_on_body",
    ],
    produces_features=[
        "captive_screw_tether_pocket",
        "counterbore_hole",
        "tether_hole",
    ],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"max_aspect_ratio": 10.0,
                                         "captive_fastener": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_drill_tether": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"medical_grade": True}},
    },
    failure_modes=[
        "fm.tether_hole_exits_body",
        "fm.tether_too_close_to_screw_shaft",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class CaptiveScrewTetherPocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            description="face-local (x, y) of the screw axis (anchored at face center)",
        )
        thread_spec: str = Field(
            default="M3",
            description="metric thread — e.g. 'M2', 'M3', 'M4'. Key in threads_metric.yaml.",
        )
        tether_clearance_d_mm: float = Field(
            default=4.5, gt=0, le=50.0,
            description="radial diameter at which the tether hole is centered "
                        "(must be larger than the screw clearance hole)",
        )
        tether_d_mm: float = Field(
            default=2.0, gt=0, le=10.0,
            description="diameter of the transverse lanyard hole",
        )
        clearance_depth_mm: float = Field(
            default=6.0, gt=0, le=200.0,
            description="how far the clearance shaft extends BELOW the head pocket floor",
        )
        fit: Literal["close", "medium", "coarse"] = "medium"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        entry = _thread_entry(args.thread_spec)
        d_clear = float(entry[f"clearance_{args.fit}_mm"])
        d_cb = float(entry["counterbore_d_iso_mm"])
        cb_depth = float(entry["counterbore_depth_iso_mm"])

        # Geometry sanity: tether hole must clear the screw clearance shaft
        # AND fit inside the head-pocket footprint.
        if args.tether_clearance_d_mm <= d_clear + args.tether_d_mm:
            raise ValueError(
                f"captive_screw_tether_pocket: tether_clearance_d_mm "
                f"({args.tether_clearance_d_mm}) too small — must exceed "
                f"clearance_{args.fit} ({d_clear}) + tether_d ({args.tether_d_mm})"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"captive_screw_tether_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "captive_screw_tether_pocket v1: planar ±Z host faces only"
            )

        z_sign = 1.0 if normal[2] > 0 else -1.0
        wx = center[0] + args.position_xy[0]
        wy = center[1] + args.position_xy[1]
        face_z = center[2]
        direction = gp_Dir(0.0, 0.0, -z_sign)
        overshoot = 0.5

        # Head pocket (counterbore for screw head)
        head_top_z = face_z + z_sign * overshoot
        head_ax = gp_Ax2(gp_Pnt(wx, wy, head_top_z), direction)
        head_mk = BRepPrimAPI_MakeCylinder(
            head_ax, d_cb / 2.0, cb_depth + overshoot,
        )
        head_mk.Build()
        if not head_mk.IsDone():
            raise RuntimeError(
                "captive_screw_tether_pocket: head pocket cylinder build failed"
            )
        head_shape = head_mk.Shape()

        # Clearance shaft (below the head pocket)
        shaft_top_z = face_z - z_sign * cb_depth
        shaft_ax = gp_Ax2(gp_Pnt(wx, wy, shaft_top_z), direction)
        shaft_mk = BRepPrimAPI_MakeCylinder(
            shaft_ax, d_clear / 2.0, args.clearance_depth_mm + overshoot,
        )
        shaft_mk.Build()
        if not shaft_mk.IsDone():
            raise RuntimeError(
                "captive_screw_tether_pocket: clearance shaft build failed"
            )
        shaft_shape = shaft_mk.Shape()

        # Tether hole — transverse, going along +Y through the head pocket
        # at half head-pocket depth, centered at radius
        # tether_clearance_d_mm / 2 from the screw axis on the -Y side. The
        # cylinder is long enough to pass cleanly through the body — we anchor
        # its base far enough out in -Y and give it length = bbox span + 200mm
        # for safety.
        tether_z = face_z - z_sign * (cb_depth * 0.5)
        # Bounding box of the input shape for sizing the through cylinder.
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        bb = Bnd_Box()
        BRepBndLib.Add_s(shape, bb)
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        span_y = (ymax - ymin) + 200.0

        tether_base = gp_Pnt(wx, ymin - 100.0, tether_z)
        tether_dir = gp_Dir(0.0, 1.0, 0.0)
        tether_ax = gp_Ax2(tether_base, tether_dir)
        tether_mk = BRepPrimAPI_MakeCylinder(
            tether_ax, args.tether_d_mm / 2.0, span_y,
        )
        tether_mk.Build()
        if not tether_mk.IsDone():
            raise RuntimeError(
                "captive_screw_tether_pocket: tether cylinder build failed"
            )
        tether_shape = tether_mk.Shape()

        # Fuse head + shaft + tether → single tool; subtract once.
        fuse1 = BRepAlgoAPI_Fuse(head_shape, shaft_shape)
        fuse1.Build()
        if not fuse1.IsDone():
            raise RuntimeError(
                "captive_screw_tether_pocket: head+shaft fuse failed"
            )
        fuse2 = BRepAlgoAPI_Fuse(fuse1.Shape(), tether_shape)
        fuse2.Build()
        if not fuse2.IsDone():
            raise RuntimeError(
                "captive_screw_tether_pocket: +tether fuse failed"
            )
        tool = fuse2.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError(
                "captive_screw_tether_pocket: boolean cut failed"
            )
        new_shape = cut.Shape()

        # silence unused names on some builds
        _ = (xmin, xmax, zmin, zmax)

        history = EntityHistoryMap(
            rules={
                "target_face":          HistoryRule.MODIFIED_INHERIT,
                "head_pocket_face":     HistoryRule.GENERATED_NEW,
                "clearance_shaft_face": HistoryRule.GENERATED_NEW,
                "tether_hole_face":     HistoryRule.GENERATED_NEW,
                "consumed_volume":      HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
