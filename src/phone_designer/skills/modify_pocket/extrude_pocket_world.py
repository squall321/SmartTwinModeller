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
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _build_world_prism(
    world_origin: tuple[float, float, float],
    axis_dir: tuple[float, float, float],
    length_mm: float,
    width_mm: float,
    depth_mm: float,
    direction: str = "into",
):
    """Build a rectangular prism centred at ``world_origin`` with its axis
    along ``axis_dir``. ``direction="into"`` means the prism extends in
    -axis_dir (drill INTO the body from world_origin); "out" extends
    along +axis_dir.

    Internal: build the planar rectangle in canonical local XY at z=0,
    extrude along ±Z by depth_mm, then rigid-transform from the local
    Ax3(origin=0, z=+Z, x=+X) to the world Ax3(origin=world_origin,
    z=axis_dir, x=gram_schmidt(world_X onto axis_dir)).
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
        BRepBuilderAPI_Transform,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

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

    L = 0.5 * float(length_mm)
    W = 0.5 * float(width_mm)

    p1 = gp_Pnt(-L, -W, 0)
    p2 = gp_Pnt(+L, -W, 0)
    p3 = gp_Pnt(+L, +W, 0)
    p4 = gp_Pnt(-L, +W, 0)
    e1 = BRepBuilderAPI_MakeEdge(p1, p2).Edge()
    e2 = BRepBuilderAPI_MakeEdge(p2, p3).Edge()
    e3 = BRepBuilderAPI_MakeEdge(p3, p4).Edge()
    e4 = BRepBuilderAPI_MakeEdge(p4, p1).Edge()
    wb = BRepBuilderAPI_MakeWire()
    wb.Add(e1); wb.Add(e2); wb.Add(e3); wb.Add(e4)
    wire = wb.Wire()
    face = BRepBuilderAPI_MakeFace(wire).Face()

    sign = -1.0 if direction == "into" else +1.0
    prism_vec = gp_Vec(0, 0, sign * float(depth_mm))
    prism = BRepPrimAPI_MakePrism(face, prism_vec).Shape()

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

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        shape = body.wrapped if hasattr(body, "wrapped") else body
        tool = _build_world_prism(
            args.world_origin, args.axis_dir,
            args.length_mm, args.width_mm, args.depth_mm,
            direction=args.direction,
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
