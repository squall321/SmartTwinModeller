"""analyze_part reconstruction-strategy routing (2026-06-18).

A true multi-body assembly (>=3 closed shells) is hopeless in box mode — a single
placeholder slab cannot represent N sparse solids. analyze_part._reconstruct now
consults base_step_kind_chooser and routes the 'assembly' verdict (closed_shells
>= 3) to preserve_brep (keep the original B-rep + re-apply cuts), falling back to
box on any preserve failure. Single bodies stay on box + 'auto'.

These tests need no corpus: a synthetic 3-box compound (3 closed shells) must
route to preserve and reconstruct near-losslessly; a single box+hole must stay on
the box path.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from phone_designer.skills.reverse_engineer.analyze_part import AnalyzePart


def _write_step(shape, path: str) -> None:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    assert w.Write(path) == IFSelect_RetDone


def _three_box_compound_step(tmp: Path) -> str:
    """Three disjoint 10mm boxes in ONE compound → 3 closed shells."""
    from build123d import Box, Pos
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    solids = [
        Box(10, 10, 10),
        Pos(20, 0, 0) * Box(10, 10, 10),
        Pos(40, 0, 0) * Box(10, 10, 10),
    ]
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    for s in solids:
        builder.Add(comp, s.wrapped)
    out = str(tmp / "three_boxes.step")
    _write_step(comp, out)
    return out


def _box_with_hole_step(tmp: Path) -> str:
    from build123d import Box, Cylinder

    part = Box(20, 16, 8) - Cylinder(2.0, 8.1)
    out = str(tmp / "single.step")
    _write_step(part.wrapped, out)
    return out


def test_multibody_assembly_routes_to_preserve():
    with tempfile.TemporaryDirectory() as d:
        path = _three_box_compound_step(Path(d))
        res = AnalyzePart().apply(None, {"part_path": path, "reconstruct": True})
        rec = res.extras["part_analysis"]["reconstruction"]
        assert rec is not None, "reconstruction stage should have run"
        assert rec.get("strategy") == "preserve_brep_assembly", (
            f"3-closed-shell compound must route to preserve, got "
            f"strategy={rec.get('strategy')}"
        )
        # preserve keeps the original B-rep → near-lossless geometry
        assert rec.get("hausdorff_mm") is not None
        assert rec["hausdorff_mm"] < 1.0, (
            f"preserve reconstruction of plain boxes should be near-exact, "
            f"got hausdorff={rec.get('hausdorff_mm')}"
        )


def test_single_body_stays_on_box():
    with tempfile.TemporaryDirectory() as d:
        path = _box_with_hole_step(Path(d))
        res = AnalyzePart().apply(None, {"part_path": path, "reconstruct": True})
        rec = res.extras["part_analysis"]["reconstruction"]
        assert rec is not None
        assert rec.get("strategy") == "box", (
            f"single body must stay on the box path, got {rec.get('strategy')}"
        )
