"""parting_surface — atomic. Generate a planar parting surface across the
body's silhouette at a given Z plane.

Output goes into `result.extras["parting_surface_shape"]` as a TopoDS_Shape;
subsequent mold-tooling skills (core_cavity_split, etc.) can consume it. The
body itself is returned unchanged — this skill only enriches the carrier with
diagnostic data, so only the `body_present` post-condition is declared.

Approach:
    1. Compute the body's XY bbox (extents).
    2. Build a rectangular planar face at z=parting_z, expanded by
       `extension_mm` on every side.
    3. Section the body with that plane (BRepAlgoAPI_Section) to get the
       body's cross-section wires at parting_z.
    4. Build a face from those wires (best-effort — multiple closed wires
       form a face with holes) and cut it from the rectangle. If no
       cross-section is found (parting plane outside body), the parting
       surface is simply the full rectangle.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _xy_bbox(shape) -> tuple[float, float, float, float]:
    """Return (xmin, ymin, xmax, ymax) for the shape."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return (-1.0, -1.0, 1.0, 1.0)
    xmin, ymin, _zmin, xmax, ymax, _zmax = bb.Get()
    return (xmin, ymin, xmax, ymax)


def _build_rect_face(xmin: float, ymin: float, xmax: float, ymax: float, z: float):
    """Build a planar rectangular TopoDS_Face at z = const with the given XY extents."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.gp import gp_Pnt

    p1 = gp_Pnt(xmin, ymin, z)
    p2 = gp_Pnt(xmax, ymin, z)
    p3 = gp_Pnt(xmax, ymax, z)
    p4 = gp_Pnt(xmin, ymax, z)

    e1 = BRepBuilderAPI_MakeEdge(p1, p2).Edge()
    e2 = BRepBuilderAPI_MakeEdge(p2, p3).Edge()
    e3 = BRepBuilderAPI_MakeEdge(p3, p4).Edge()
    e4 = BRepBuilderAPI_MakeEdge(p4, p1).Edge()

    wire_maker = BRepBuilderAPI_MakeWire()
    wire_maker.Add(e1)
    wire_maker.Add(e2)
    wire_maker.Add(e3)
    wire_maker.Add(e4)
    wire = wire_maker.Wire()
    return BRepBuilderAPI_MakeFace(wire, True).Face()


def _body_section_face_at_z(shape, z: float):
    """Return the TopoDS face cut out of the body at z=const, or None if no
    intersection.

    Strategy: BRepAlgoAPI_Section gives section edges; we collect them into
    wires (ShapeAnalysis_FreeBounds) and convert each closed wire to a face.
    If only one closed wire is found we return that face; otherwise return
    a compound of faces.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS, TopoDS_Compound
    from OCP.TopTools import TopTools_HSequenceOfShape
    from OCP.BRep import BRep_Builder

    plane = gp_Pln(gp_Pnt(0.0, 0.0, z), gp_Dir(0.0, 0.0, 1.0))
    sec = BRepAlgoAPI_Section(shape, plane, False)
    sec.ComputePCurveOn1(True)
    sec.Approximation(True)
    sec.Build()
    if not sec.IsDone():
        return None

    section_shape = sec.Shape()

    # Collect section edges into a sequence for FreeBounds.
    edges_seq = TopTools_HSequenceOfShape()
    has_any = False
    it = TopExp_Explorer(section_shape, TopAbs_EDGE)
    while it.More():
        edges_seq.Append(it.Current())
        has_any = True
        it.Next()
    if not has_any:
        return None

    closed_wires = TopTools_HSequenceOfShape()
    open_wires = TopTools_HSequenceOfShape()
    try:
        ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(
            edges_seq, 1e-4, False, closed_wires, open_wires,
        )
    except Exception:
        return None

    faces = []
    for i in range(1, closed_wires.Length() + 1):
        w = TopoDS.Wire_s(closed_wires.Value(i))
        try:
            f = BRepBuilderAPI_MakeFace(w, True).Face()
            faces.append(f)
        except Exception:
            continue

    if not faces:
        return None
    if len(faces) == 1:
        return faces[0]

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for f in faces:
        builder.Add(compound, f)
    return compound


@skill(
    name="parting_surface",
    category="modify/mold",
    level="atomic",
    summary="Generate a planar parting surface at the given Z plane "
            "(rectangle minus body cross-section). Body is returned unchanged; "
            "the surface shape is exposed via result.extras['parting_surface_shape'] "
            "for downstream mold-tooling skills (core/cavity split, etc.).",
    selector_kinds=[],
    history_rules={},
    produces_features=["parting_surface"],
    preserves=["body_topology"],
    manufacturing={
        "die_cast_al":       {"min_draft_deg": 1.0},
        "injection_mold_pa": {"min_draft_deg": 0.5},
    },
    failure_modes=["fm.parting_plane_outside_body"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class PartingSurface(SkillBase):
    class Args(BaseModel):
        parting_z_mm: float
        extension_mm: float = Field(default=20.0, ge=0.0, le=1000.0)
        pull_direction: Literal["+Z", "-Z"] = "+Z"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        shape = _occt_shape(body)
        if shape is None:
            raise RuntimeError("parting_surface: body is None")

        xmin, ymin, xmax, ymax = _xy_bbox(shape)
        # Expand by extension on every side
        ex = args.extension_mm
        rect = _build_rect_face(
            xmin - ex, ymin - ex, xmax + ex, ymax + ex, args.parting_z_mm,
        )

        body_cross = _body_section_face_at_z(shape, args.parting_z_mm)
        if body_cross is None:
            # Parting plane outside body — surface = full rectangle.
            parting_shape = rect
        else:
            cut = BRepAlgoAPI_Cut(rect, body_cross)
            cut.Build()
            if cut.IsDone():
                parting_shape = cut.Shape()
            else:
                # Boolean failed — fall back to full rectangle.
                parting_shape = rect

        extras = {
            "parting_surface_shape": parting_shape,
            "parting_z_mm": args.parting_z_mm,
            "pull_direction": args.pull_direction,
            "bbox_xy": [round(xmin, 3), round(ymin, 3),
                        round(xmax, 3), round(ymax, 3)],
        }

        # Body returned unchanged.
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
