"""worm_thread — atomic create skill.

Cylindrical worm shaft with a single-start helical V-thread. The shaft is a
cylinder of diameter `pitch_d_mm` and length `length_mm`; the thread is a
swept V-profile groove cut into its outer surface along a helix whose lead
matches the requested lead angle.

Geometry & parameterization:
    module      m   (mm/tooth, normal module)
    lead_angle  γ   (deg, helix angle of the pitch helix from the
                     circumferential direction; γ small for typical worms)
    pitch_d     Dp  (mm, pitch diameter of the worm / shaft outer diameter)
    length      L   (mm, axial length of the worm)

Derived:
    axial pitch px  = π · m / cos(γ)         (axial advance per worm turn,
                                              single-start)
    thread_height h = 2.25 · m               (standard whole-depth ratio)
    turns           = L / px
    helix radius    = Dp / 2

Construction:
    1. Build a cylinder of radius Dp/2 and length L along +Z (local frame).
    2. Build a helix wire (B-spline interpolation, ≥ 16 pts/turn) on the
       cylinder over `turns` turns.
    3. Build a V-profile triangular face in the XZ plane at the helix start
       (base on x = R + bias, apex at x = R − h).
    4. Sweep the V-profile along the helix with BRepOffsetAPI_MakePipe.
    5. Subtract the swept solid from the cylinder.

post: body_present
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _build_helix_wire(radius: float, pitch: float, turns: float):
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.gp import gp_Pnt
    from OCP.TColgp import TColgp_HArray1OfPnt

    samples_per_turn = 16
    n = max(int(math.ceil(turns * samples_per_turn)) + 1, 10)
    arr = TColgp_HArray1OfPnt(1, n)
    total_t = turns * 2.0 * math.pi
    for i in range(n):
        t = (i / (n - 1)) * total_t
        x = radius * math.cos(t)
        y = radius * math.sin(t)
        z = t * pitch / (2.0 * math.pi)
        arr.SetValue(i + 1, gp_Pnt(x, y, z))

    interp = GeomAPI_Interpolate(arr, False, 1e-5)
    interp.Perform()
    if not interp.IsDone():
        raise RuntimeError("worm_thread: helix interpolation failed")
    curve = interp.Curve()
    me = BRepBuilderAPI_MakeEdge(curve)
    if not me.IsDone():
        raise RuntimeError("worm_thread: helix edge failed")
    mw = BRepBuilderAPI_MakeWire()
    mw.Add(me.Edge())
    if not mw.IsDone():
        raise RuntimeError("worm_thread: helix wire failed")
    return mw.Wire()


def _build_v_profile_face(radius: float, thread_height: float,
                          thread_angle_deg: float):
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
    )
    from OCP.gp import gp_Pnt

    BIAS = 0.05
    w = 2.0 * thread_height * math.tan(math.radians(thread_angle_deg) / 2.0)
    x_base = radius + BIAS
    x_apex = max(radius - thread_height, 1e-3)

    p_lo = gp_Pnt(x_base, 0.0, -w / 2.0)
    p_hi = gp_Pnt(x_base, 0.0,  w / 2.0)
    p_ap = gp_Pnt(x_apex, 0.0,  0.0)

    poly = BRepBuilderAPI_MakePolygon()
    poly.Add(p_lo); poly.Add(p_hi); poly.Add(p_ap)
    poly.Close()
    if not poly.IsDone():
        raise RuntimeError("worm_thread: V-profile polygon failed")
    mf = BRepBuilderAPI_MakeFace(poly.Wire(), True)
    if not mf.IsDone():
        raise RuntimeError("worm_thread: V-profile face failed")
    return mf.Face()


@skill(
    name="worm_thread",
    category="create",
    level="atomic",
    summary="Cylindrical worm shaft with a single-start helical V-thread. "
            "Standard module + lead-angle parameterization; axial pitch "
            "px = π·m/cos(γ). Produces a true 3D thread (not decorative).",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["worm_shaft", "helical_thread", "v_profile"],
    preserves=[],
    manufacturing={
        "turning":   {"min_wall_mm": 0.3, "extras": {"single_point": True}},
        "thread_milling": {"min_wall_mm": 0.4},
        "hobbing":   {"min_wall_mm": 0.4},
    },
    failure_modes=[
        "fm.worm_lead_angle_invalid",
        "fm.worm_thread_height_exceeds_radius",
        "fm.worm_length_too_short_for_one_turn",
    ],
    cost_hint=0.5,
    post_conditions=[PostCondition(kind="body_present")],
)
class WormThread(SkillBase):
    class Args(BaseModel):
        module_mm: float = Field(default=1.0, gt=0.0)
        lead_angle_deg: float = Field(default=5.0, gt=0.0, lt=45.0)
        pitch_d_mm: float = Field(default=20.0, gt=0.0)
        length_mm: float = Field(default=30.0, gt=0.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
        from build123d import Part

        m = args.module_mm
        gamma = math.radians(args.lead_angle_deg)
        R = args.pitch_d_mm / 2.0
        L = args.length_mm

        thread_height = 2.25 * m
        if thread_height >= R:
            raise ValueError(
                f"worm_thread: thread_height ({thread_height:.3f}) >= "
                f"pitch radius ({R:.3f}); apex would cross axis"
            )

        # Single-start axial pitch:
        axial_pitch = math.pi * m / max(math.cos(gamma), 1e-6)
        if axial_pitch >= L:
            raise ValueError(
                f"worm_thread: axial pitch ({axial_pitch:.3f}) >= "
                f"length ({L:.3f}); cannot fit one full turn"
            )

        turns = L / axial_pitch

        # 1. Cylindrical shaft.
        ax2 = gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0))
        cyl = BRepPrimAPI_MakeCylinder(ax2, R, L)
        cyl.Build()
        if not cyl.IsDone():
            raise RuntimeError("worm_thread: shaft cylinder failed")
        shaft = cyl.Shape()

        # 2. Helix + V-profile sweep.
        helix = _build_helix_wire(R, axial_pitch, turns)
        profile = _build_v_profile_face(R, thread_height, thread_angle_deg=60.0)
        pipe = BRepOffsetAPI_MakePipe(helix, profile)
        pipe.Build()
        if not pipe.IsDone():
            raise RuntimeError("worm_thread: pipe sweep failed")
        thread_solid = pipe.Shape()

        # 3. Cut the thread groove from the shaft.
        cut = BRepAlgoAPI_Cut(shaft, thread_solid)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("worm_thread: thread cut failed")
        shape = cut.Shape()

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shape), history=history)
