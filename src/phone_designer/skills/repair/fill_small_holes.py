"""fill_small_holes — atomic, body-modifying repair.

Fill small open-boundary loops in a shell with planar faces, leaving large
openings alone. Designed for mesh→BREP outputs of real-world 3D scans
(iPhone teardown, photogrammetry) where:

  - Genuine holes that the part HAS (camera cutout, button aperture, screw
    hole) are typically < 30 mm perimeter — caller may want to keep or fill
    these depending on intent.
  - Open boundaries that come from teardown / scan incompleteness (rear
    glass missing, motherboard not modeled) are typically large.

The skill finds every free-boundary wire on the shell (edges shared by
exactly one face), groups them by perimeter, and:

  - perimeter <= max_perimeter_mm  → build a planar face on the loop and
    add it to the shell
  - perimeter >  max_perimeter_mm  → leave as-is

extras schema::

    extras["fill_small_holes"] = {
        "open_boundaries_found": int,
        "filled": int,
        "skipped_too_big": int,
        "filled_perimeters_mm": [float, ...],
    }

post_conditions = [body_present] — the body MAY change (more faces, fewer
open edges) but it is not required to. shell→solid promotion happens only
if every open boundary is filled.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body):
    return body.wrapped if hasattr(body, "wrapped") else body


def _wire_perimeter_mm(wire) -> float:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.GCPnts import GCPnts_AbscissaPoint

    total = 0.0
    it = BRepTools_WireExplorer(wire)
    while it.More():
        e = it.Current()
        try:
            total += float(GCPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(e)))
        except Exception:
            pass
        it.Next()
    return total


@skill(
    name="fill_small_holes",
    category="repair",
    level="atomic",
    summary="Fill small open-boundary loops in a shell with planar faces, "
            "leaving large openings alone. Designed for mesh→BREP outputs "
            "where camera/button cutouts (small perimeter) should be filled "
            "but missing rear-glass / motherboard (large perimeter) should "
            "not be. extras report filled vs skipped counts.",
    selector_kinds=[],
    history_rules={},
    produces_features=["filled_faces"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_open_boundaries", "fm.planar_face_build_failed"],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class FillSmallHoles(SkillBase):
    class Args(BaseModel):
        max_perimeter_mm: float = Field(
            default=50.0, gt=0,
            description="Fill boundary loops whose total perimeter is at most "
                        "this. Cutouts on phone-scale parts (camera, button, "
                        "screw) are typically 5-30 mm; teardown openings (back "
                        "glass) > 100 mm.",
        )
        max_holes_to_fill: int = Field(default=200, ge=1, le=10000)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_Sewing,
        )
        from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
        from OCP.TopAbs import TopAbs_FACE, TopAbs_WIRE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS, TopoDS_Compound
        from OCP.BRep import BRep_Builder
        from build123d import Part

        shape = _occt_shape(body)

        # 1. Find all free-boundary wires (edges shared by exactly one face).
        fb = ShapeAnalysis_FreeBounds(shape, False, True)  # split_closed=True
        closed_wires_compound = fb.GetClosedWires()
        open_wires_compound = fb.GetOpenWires()

        wires: list = []
        for comp in (closed_wires_compound, open_wires_compound):
            it = TopExp_Explorer(comp, TopAbs_WIRE)
            while it.More():
                wires.append(TopoDS.Wire_s(it.Current()))
                it.Next()

        total_found = len(wires)
        filled_count = 0
        skipped_too_big = 0
        filled_perimeters: list[float] = []

        # 2. Try to build a planar face on each small-perimeter loop.
        sewing = BRepBuilderAPI_Sewing(1e-3)  # 1um tolerance
        # Add original shape first
        sewing.Add(shape)

        for w in wires[: args.max_holes_to_fill]:
            try:
                peri = _wire_perimeter_mm(w)
            except Exception:
                peri = 0.0
            if peri <= 0.0:
                continue
            if peri > args.max_perimeter_mm:
                skipped_too_big += 1
                continue
            try:
                mf = BRepBuilderAPI_MakeFace(w, True)  # OnlyPlane=True
                if mf.IsDone():
                    sewing.Add(mf.Face())
                    filled_count += 1
                    filled_perimeters.append(round(peri, 3))
            except Exception:
                # Wire not planar — leave it alone
                continue

        sewing.Perform()
        new_shape = sewing.SewedShape()

        extras = {
            "fill_small_holes": {
                "open_boundaries_found": total_found,
                "filled": filled_count,
                "skipped_too_big": skipped_too_big,
                "filled_perimeters_mm": filled_perimeters[:50],  # cap output
            }
        }
        return SkillResult(
            body=Part(new_shape),
            history=EntityHistoryMap(),
            extras=extras,
        )
