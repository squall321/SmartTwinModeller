"""split_body — atomic. Cut the working solid by an arbitrary plane into bodies.

Splits ONE solid into N independent bodies along a plane (point + normal) — the
core Insert>Split / Modify>Split Body op (multi-part enclosures, symmetry halves,
trimming). Distinct from:
  * ``core_cavity_split`` — Z-plane-only, returns the body UNCHANGED (parks halves
    in extras with core/cavity naming);
  * ``split_into_components`` — only ENUMERATES already-disjoint solids;
  * ``surface_split_with_curve`` — subdivides ONE face, not the solid.

Uses ``BRepAlgoAPI_Splitter`` (body = Argument, an unbounded plane-face = Tool),
then ``iter_solid_components`` to enumerate the resulting solids. ``keep`` selects
which side(s) to return: 'both' (a compound of all pieces — the default), 'positive'
(the +normal side), or 'negative'. is_solid gated on total volume>0; a plane that
misses the body yields one piece (a structured note, not a failure).
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult

_EPS_VOL = 1e-6


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(float(g.Mass()))


def _centroid(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    c = g.CentreOfMass()
    return (c.X(), c.Y(), c.Z())


@skill(
    name="split_body",
    category="transform",
    level="atomic",
    summary="Split the working solid by an arbitrary plane (plane_origin_mm + "
            "plane_normal) into independent bodies via BRepAlgoAPI_Splitter. "
            "keep='both' returns a compound of all pieces (default) | 'positive' | "
            "'negative' (by which side of the plane the piece's centroid is on). "
            "Unlike core_cavity_split (Z-only, body unchanged) this returns real cut "
            "bodies. is_solid gated on volume>0.",
    selector_kinds=[],
    history_rules={"body_faces": HistoryRule.MODIFIED_INHERIT,
                   "cut_faces": HistoryRule.GENERATED_NEW},
    produces_features=[],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.zero_plane_normal", "fm.split_failed", "fm.split_no_cut"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class SplitBody(SkillBase):
    class Args(BaseModel):
        plane_origin_mm: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="A point on the cutting plane.")
        plane_normal: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 1.0),
            description="Normal of the cutting plane (need not be unit). The "
                        "'positive' side is the +normal half-space.")
        keep: Literal["both", "positive", "negative"] = Field(
            default="both",
            description="Which piece(s) to return: 'both' = a compound of all cut "
                        "pieces; 'positive'/'negative' = only pieces whose centroid "
                        "is on that side of the plane.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt
        from OCP.TopTools import TopTools_ListOfShape

        from phone_designer.skills.assembly._compound import (
            build_compound,
            iter_solid_components,
        )

        if body is None:
            raise ValueError("fm.split_failed: split_body needs an input body.")
        shape = body.wrapped if hasattr(body, "wrapped") else body

        nx, ny, nz = args.plane_normal
        nmag = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nmag < 1e-12:
            raise ValueError("fm.zero_plane_normal: plane_normal is a zero vector.")
        nx, ny, nz = nx / nmag, ny / nmag, nz / nmag
        ox, oy, oz = args.plane_origin_mm

        # an unbounded plane-face is the cutting Tool.
        pln = gp_Pln(gp_Ax3(gp_Pnt(ox, oy, oz), gp_Dir(nx, ny, nz)))
        tool_face = BRepBuilderAPI_MakeFace(pln).Face()

        try:
            args_l = TopTools_ListOfShape()
            args_l.Append(shape)
            tools_l = TopTools_ListOfShape()
            tools_l.Append(tool_face)
            splitter = BRepAlgoAPI_Splitter()
            splitter.SetArguments(args_l)
            splitter.SetTools(tools_l)
            splitter.Build()
            if not splitter.IsDone():
                raise RuntimeError("Splitter IsDone=False")
            result = splitter.Shape()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.split_failed: {type(exc).__name__}: {exc}")

        pieces = [s for s in iter_solid_components(result) if _volume(s) > _EPS_VOL]
        if not pieces:
            raise ValueError("fm.split_failed: no solid pieces after the split.")

        n_cut = len(pieces)
        # classify each piece by which side of the plane its centroid lies on.
        def side(piece):
            cx, cy, cz = _centroid(piece)
            return (cx - ox) * nx + (cy - oy) * ny + (cz - oz) * nz

        if args.keep == "positive":
            sel = [p for p in pieces if side(p) >= 0]
        elif args.keep == "negative":
            sel = [p for p in pieces if side(p) < 0]
        else:
            sel = pieces
        if not sel:
            raise ValueError(
                f"fm.split_no_cut: keep='{args.keep}' selected no pieces (the plane "
                f"did not separate the body — {n_cut} piece(s) all on one side).")

        out = sel[0] if len(sel) == 1 else build_compound(sel)
        vol = _volume(out)
        if out is None or out.IsNull() or vol <= _EPS_VOL:
            raise ValueError(f"fm.split_failed: kept volume {vol:.6g}mm³ ≈ 0.")

        return SkillResult(
            body=Part(out),
            history=EntityHistoryMap(rules={
                "body_faces": HistoryRule.MODIFIED_INHERIT,
                "cut_faces": HistoryRule.GENERATED_NEW}),
            extras={"transform": {
                "op": "split_body",
                "plane_origin_mm": list(args.plane_origin_mm),
                "plane_normal": [round(nx, 6), round(ny, 6), round(nz, 6)],
                "keep": args.keep,
                "n_pieces_total": n_cut,
                "n_pieces_kept": len(sel),
                "volume_mm3": round(vol, 3),
            }},
        )
