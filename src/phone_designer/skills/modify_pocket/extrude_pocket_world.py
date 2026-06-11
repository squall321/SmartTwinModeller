"""extrude_pocket_world — atomic. Like extrude_pocket but with WORLD-coord
placement instead of face_selector.

COMPLEX-CAD pass-10 (2026-06-09): solves the INTERIOR-pocket gap exposed
by the box-mode honest test. The existing extrude_pocket needs a
face_selector that resolves to a face on the body's OUTER surface
(top / bottom / front / back / left / right). For features whose entry
plane sits at an INTERIOR position — e.g. as1-oc-214's pocket 2 with
axis_origin world z=-3 inside a body that extends z=-4 to z=80 — no
named outer face reaches that depth, so the cut lands ~80 mm away
from where it should.

This skill bypasses face_selector entirely. The caller supplies a
world position + axis direction; the skill builds a rectangular prism
with that pose, sized by sketch.length_mm / sketch.width_mm, extruded
into the body for depth_mm, and Cut-subtracts from the body.

Used by plan_from_feature_catalog whenever a catalog pocket's
axis_origin is not "on" an outer face (interior pockets). For surface
pockets the planner still prefers extrude_pocket because its
face_selector enables history tracking.

extras schema (none — same SkillResult contract as extrude_pocket).

post_conditions = [volume_decreased]. Same gate as extrude_pocket so a
no-op cut (tool placed off the body) is caught.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _rect_prism_local(length_mm: float, width_mm: float, depth_sign_mm: float,
                      angle_deg: float = 0.0, cx: float = 0.0, cy: float = 0.0):
    """Rectangular prism in LOCAL coords: L×W rectangle centred at (cx, cy)
    on z=0, optionally rotated by ``angle_deg`` about local Z, extruded by
    ``depth_sign_mm`` (signed) along Z.

    COMPLEX-CAD pass-26 (2026-06-11, A10/P9): factored out of
    _build_world_prism so the slot footprint can reuse it. The
    angle_deg == 0.0, cx == cy == 0.0 path produces the exact corner
    points the original inline code did (byte-identical default).
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    L = 0.5 * float(length_mm)
    W = 0.5 * float(width_mm)
    corners = [(-L, -W), (+L, -W), (+L, +W), (-L, +W)]
    if angle_deg != 0.0:
        ca = math.cos(math.radians(float(angle_deg)))
        sa = math.sin(math.radians(float(angle_deg)))
        corners = [(x * ca - y * sa, x * sa + y * ca) for (x, y) in corners]
    if cx != 0.0 or cy != 0.0:
        corners = [(x + cx, y + cy) for (x, y) in corners]

    p1 = gp_Pnt(corners[0][0], corners[0][1], 0)
    p2 = gp_Pnt(corners[1][0], corners[1][1], 0)
    p3 = gp_Pnt(corners[2][0], corners[2][1], 0)
    p4 = gp_Pnt(corners[3][0], corners[3][1], 0)
    e1 = BRepBuilderAPI_MakeEdge(p1, p2).Edge()
    e2 = BRepBuilderAPI_MakeEdge(p2, p3).Edge()
    e3 = BRepBuilderAPI_MakeEdge(p3, p4).Edge()
    e4 = BRepBuilderAPI_MakeEdge(p4, p1).Edge()
    wb = BRepBuilderAPI_MakeWire()
    wb.Add(e1); wb.Add(e2); wb.Add(e3); wb.Add(e4)
    wire = wb.Wire()
    face = BRepBuilderAPI_MakeFace(wire).Face()
    prism_vec = gp_Vec(0, 0, float(depth_sign_mm))
    return BRepPrimAPI_MakePrism(face, prism_vec).Shape()


def _build_world_prism(
    world_origin: tuple[float, float, float],
    axis_dir: tuple[float, float, float],
    length_mm: float,
    width_mm: float,
    depth_mm: float,
    direction: str = "into",
    kind: str = "rect",
    angle_deg: float = 0.0,
):
    """Build the pocket cutting tool centred at ``world_origin`` with its
    axis along ``axis_dir``. ``direction="into"`` means the tool extends in
    -axis_dir (drill INTO the body from world_origin); "out" extends
    along +axis_dir.

    Internal: build the planar profile in canonical local XY at z=0,
    extrude along ±Z by depth_mm, then rigid-transform from the local
    Ax3(origin=0, z=+Z, x=+X) to the world Ax3(origin=world_origin,
    z=axis_dir, x=gram_schmidt(world_X onto axis_dir)).

    COMPLEX-CAD pass-26 (2026-06-11, A10/P9) — ADDITIVE footprint args:
      kind="rect"      L×W rectangle (default — byte-identical to the
                       pre-pass-26 behaviour when angle_deg == 0.0).
      kind="circular"  cylinder Ø = min(length_mm, width_mm).
      kind="slot"      stadium: (L−W)×W rectangle + two ØW end caps.
      angle_deg        in-plane rotation about the tool axis (rect/slot).
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    nx, ny, nz = float(axis_dir[0]), float(axis_dir[1]), float(axis_dir[2])
    nmag = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nmag < 1e-9:
        raise RuntimeError("extrude_pocket_world: zero axis_dir")
    nx, ny, nz = nx / nmag, ny / nmag, nz / nmag

    # In-plane reference for u: project world +X (or +Y if n is X-aligned).
    if abs(nx) < 0.9:
        ref = (1.0, 0.0, 0.0)
    else:
        ref = (0.0, 1.0, 0.0)
    dot = ref[0] * nx + ref[1] * ny + ref[2] * nz
    ux = ref[0] - dot * nx
    uy = ref[1] - dot * ny
    uz = ref[2] - dot * nz
    umag = math.sqrt(ux * ux + uy * uy + uz * uz)
    if umag < 1e-9:
        ux, uy, uz, umag = 0.0, 1.0, 0.0, 1.0
    ux, uy, uz = ux / umag, uy / umag, uz / umag

    sign = -1.0 if direction == "into" else +1.0

    if kind == "circular":
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2

        r = 0.5 * min(float(length_mm), float(width_mm))
        if r <= 0.0:
            raise RuntimeError("extrude_pocket_world: circular kind needs r>0")
        ax2 = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, sign))
        prism = BRepPrimAPI_MakeCylinder(ax2, r, float(depth_mm)).Shape()
    elif kind == "slot":
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2

        Lf = float(length_mm)
        Wf = float(width_mm)
        r = 0.5 * Wf
        inner = Lf - Wf  # straight section between the two cap centres
        if r <= 0.0:
            raise RuntimeError("extrude_pocket_world: slot kind needs width>0")
        if inner <= 1e-9:
            # degenerate slot — pure cylinder.
            ax2 = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, sign))
            prism = BRepPrimAPI_MakeCylinder(ax2, r, float(depth_mm)).Shape()
        else:
            ca = math.cos(math.radians(float(angle_deg)))
            sa = math.sin(math.radians(float(angle_deg)))
            prism = _rect_prism_local(
                inner, Wf, sign * float(depth_mm), angle_deg=float(angle_deg),
            )
            half = 0.5 * inner
            for ex in (-half, +half):
                cap_ax2 = gp_Ax2(
                    gp_Pnt(ex * ca, ex * sa, 0), gp_Dir(0, 0, sign),
                )
                cap = BRepPrimAPI_MakeCylinder(
                    cap_ax2, r, float(depth_mm),
                ).Shape()
                fuse = BRepAlgoAPI_Fuse(prism, cap)
                fuse.Build()
                if not fuse.IsDone():
                    raise RuntimeError(
                        "extrude_pocket_world: slot cap fuse failed"
                    )
                prism = fuse.Shape()
    else:  # "rect" — default; angle_deg=0.0 path is byte-identical pre-pass-26
        prism = _rect_prism_local(
            length_mm, width_mm, sign * float(depth_mm),
            angle_deg=float(angle_deg),
        )

    local_ax3 = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0))
    world_ax3 = gp_Ax3(
        gp_Pnt(world_origin[0], world_origin[1], world_origin[2]),
        gp_Dir(nx, ny, nz),
        gp_Dir(ux, uy, uz),
    )
    trsf = gp_Trsf()
    trsf.SetTransformation(world_ax3, local_ax3)
    xf = BRepBuilderAPI_Transform(prism, trsf, True)
    xf.Build()
    return xf.Shape()


@skill(
    name="extrude_pocket_world",
    category="modify/pocket",
    level="atomic",
    summary="Extrude a rectangular pocket at a WORLD position + axis_dir, "
            "without needing a face_selector. For catalog features whose "
            "entry plane is INTERIOR to the body (no named outer face "
            "reaches them).",
    selector_kinds=[],
    history_rules={
        "pocket_walls":    HistoryRule.GENERATED_NEW,
        "pocket_bottom":   HistoryRule.GENERATED_NEW,
    },
    preconditions=[],
    produces_features=["pocket"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5, "extras": {"max_aspect_ratio": 4.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.tool_outside_body"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class ExtrudePocketWorld(SkillBase):
    class Args(BaseModel):
        world_origin: tuple[float, float, float] = Field(
            description="Pocket entry-face centroid in WORLD coordinates.",
        )
        axis_dir: tuple[float, float, float] = Field(
            description="Outward normal of the open face. The tool extends "
                        "in -axis_dir for `direction='into'` (drill inward).",
        )
        length_mm: float = Field(gt=0, le=10000)
        width_mm: float = Field(gt=0, le=10000)
        depth_mm: float = Field(gt=0, le=10000)
        direction: str = Field(default="into")
        # COMPLEX-CAD pass-26 (2026-06-11, A10/P9) — ADDITIVE footprint
        # args. Defaults reproduce the pre-pass-26 rectangular prism
        # byte-identically, so every existing plan YAML re-executes
        # unchanged.
        kind: Literal["rect", "circular", "slot"] = Field(
            default="rect",
            description="Tool cross-section: 'rect' (default, legacy "
                        "behaviour), 'circular' (cylinder Ø=min(L,W)), "
                        "'slot' (stadium LxW with ØW end caps).",
        )
        angle_deg: float = Field(
            default=0.0,
            description="In-plane rotation of the footprint about the tool "
                        "axis (rect/slot). 0.0 = legacy orientation.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        shape = body.wrapped if hasattr(body, "wrapped") else body
        tool = _build_world_prism(
            args.world_origin, args.axis_dir,
            args.length_mm, args.width_mm, args.depth_mm,
            direction=args.direction,
            # pass-26 (2026-06-11, A10/P9): footprint args — defaults keep
            # the legacy rectangular tool byte-identical.
            kind=args.kind,
            angle_deg=args.angle_deg,
        )
        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("extrude_pocket_world cut 실패 (IsDone=False)")
        new_shape = cut.Shape()
        if new_shape.IsNull():
            raise RuntimeError("extrude_pocket_world: cut.Shape() is null")
        history = EntityHistoryMap(
            rules={
                "pocket_walls":  HistoryRule.GENERATED_NEW,
                "pocket_bottom": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)


def _largest_solid(shape):
    """Return the largest TopoDS_Solid inside ``shape`` by volume. If the
    input is already a Solid, return it unchanged. If no Solids are
    found, return the input as-is (caller may still get a Compound but
    at least the chain doesn't crash silently)."""
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.TopAbs import TopAbs_SOLID
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        it = TopExp_Explorer(shape, TopAbs_SOLID)
        best = None
        best_vol = -1.0
        while it.More():
            s = TopoDS.Solid_s(it.Current())
            p = GProp_GProps()
            BRepGProp.VolumeProperties_s(s, p)
            v = abs(float(p.Mass()))
            if v > best_vol:
                best_vol = v
                best = s
            it.Next()
        return best if best is not None else shape
    except Exception:
        return shape
