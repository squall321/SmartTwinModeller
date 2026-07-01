"""trim_surface — atomic. Trim/split a FACE or a SOLID by a cutting plane, keep one side.

The generalised "trim to plane" op: take a body that is EITHER a planar/curved FACE
(a surface body — the output of surface_blend_g1, fill_surface_patch, …) OR a solid,
supply a cutting plane (``plane_origin_mm`` + ``plane_normal``), and keep the piece(s)
whose centroid lies on the chosen side (``keep='positive'`` = the +normal half-space |
``'negative'``). Distinct from ``transform/split_body`` (solid-only, keep='both'
default, returns a compound of all pieces) — trim_surface ALSO handles a bare surface
body and always keeps exactly one side (a true trim).

Mechanism (same unbounded-plane-tool approach as split_body):
  1. Build an unbounded plane face ``BRepBuilderAPI_MakeFace(gp_Pln)`` as the cutting
     Tool.
  2. Feed the input shape (Argument) + the plane (Tool) to ``BRepAlgoAPI_Splitter``.
  3. Enumerate the result's SUB-SHAPES of the input's own kind — FACES for a face
     input, SOLIDS for a solid input.
  4. Keep the pieces whose centroid (surface centroid for faces, volume centroid for
     solids) is on the ``keep`` side of the plane; rebuild a single shape or compound.

A face input is returned as a FACE (surface body → ``body_present`` post-condition, the
surface_blend_g1 precedent). A solid input is returned as a solid. Trimming a 20×20
planar face through its centre keeps ~200 mm² (half the 400 mm² area); trimming a solid
through its centre keeps ~half the volume. A plane that misses the body leaves one
piece entirely on one side — ``keep`` then either returns it whole (still on that side)
or refuses (``fm.trim_no_piece``: nothing on the kept side), never a silent empty body.
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
_EPS_AREA = 1e-9


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(float(g.Mass()))


def _area(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, g)
    return abs(float(g.Mass()))


def _vol_centroid(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    c = g.CentreOfMass()
    return (c.X(), c.Y(), c.Z())


def _area_centroid(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, g)
    c = g.CentreOfMass()
    return (c.X(), c.Y(), c.Z())


def _has_solid(shape) -> bool:
    """True if `shape` contains at least one TopoDS_Solid (a solid input)."""
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    it = TopExp_Explorer(shape, TopAbs_SOLID)
    return it.More()


def _iter_faces(shape):
    """Unique TopoDS_Face sub-shapes of `shape`."""
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_MapOfShape
    seen = TopTools_MapOfShape()
    out = []
    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        f = it.Current()
        if not seen.Contains(f):
            seen.Add(f)
            out.append(TopoDS.Face_s(f))
        it.Next()
    return out


def _build_compound(pieces):
    """Compound of ≥1 shapes (single piece returned directly)."""
    if len(pieces) == 1:
        return pieces[0]
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound
    comp = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(comp)
    for p in pieces:
        b.Add(comp, p)
    return comp


@skill(
    name="trim_surface",
    category="modify/curvature",
    level="atomic",
    summary="Trim/split a FACE (surface body) OR a solid by a cutting plane "
            "(plane_origin_mm + plane_normal) and KEEP ONE side (keep='positive' = "
            "+normal half-space | 'negative'), via BRepAlgoAPI_Splitter with an "
            "unbounded plane-face tool. A face input yields ~half its area and is "
            "returned as a FACE (body_present); a solid input yields ~half its volume. "
            "Unlike split_body (solid-only, keep='both' default) this trims a surface "
            "body too and always keeps exactly one side.",
    selector_kinds=[],
    history_rules={"kept_faces": HistoryRule.MODIFIED_INHERIT,
                   "cut_edges": HistoryRule.GENERATED_NEW},
    produces_features=["trim_surface"],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.zero_plane_normal", "fm.trim_no_input",
                   "fm.trim_failed", "fm.trim_no_piece", "fm.trim_degenerate"],
    cost_hint=0.2,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class TrimSurface(SkillBase):
    class Args(BaseModel):
        plane_origin_mm: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="A point on the cutting plane.")
        plane_normal: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 1.0),
            description="Normal of the cutting plane (need not be unit). The kept "
                        "'positive' side is the +normal half-space.")
        keep: Literal["positive", "negative"] = Field(
            default="positive",
            description="Which side to keep: 'positive' = pieces whose centroid is "
                        "on the +normal side of the plane; 'negative' = the other.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt
        from OCP.TopTools import TopTools_ListOfShape

        if body is None:
            raise ValueError("fm.trim_no_input: trim_surface needs an input body "
                             "(a FACE surface body or a solid).")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        if shape is None or shape.IsNull():
            raise ValueError("fm.trim_no_input: input body is null.")

        nx, ny, nz = args.plane_normal
        nmag = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nmag < 1e-12:
            raise ValueError("fm.zero_plane_normal: plane_normal is a zero vector.")
        nx, ny, nz = nx / nmag, ny / nmag, nz / nmag
        ox, oy, oz = args.plane_origin_mm

        is_solid = _has_solid(shape)

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
            raise ValueError(f"fm.trim_failed: {type(exc).__name__}: {exc}")

        # Enumerate the result's pieces of the INPUT's own kind: solids for a solid
        # input, faces for a surface body. Measure size (vol/area) + centroid the
        # same way, so the keep-side logic is identical for both.
        if is_solid:
            from phone_designer.skills.assembly._compound import iter_solid_components
            pieces = [s for s in iter_solid_components(result)
                      if _volume(s) > _EPS_VOL]
            size_of = _volume
            centroid_of = _vol_centroid
            size_key = "volume_mm3"
            eps_size = _EPS_VOL
        else:
            pieces = [f for f in _iter_faces(result) if _area(f) > _EPS_AREA]
            size_of = _area
            centroid_of = _area_centroid
            size_key = "area_mm2"
            eps_size = _EPS_AREA

        if not pieces:
            raise ValueError("fm.trim_failed: no pieces after the split.")
        n_total = len(pieces)

        # signed distance of a piece's centroid from the plane (+ = positive side).
        def side(piece) -> float:
            cx, cy, cz = centroid_of(piece)
            return (cx - ox) * nx + (cy - oy) * ny + (cz - oz) * nz

        if args.keep == "positive":
            sel = [p for p in pieces if side(p) >= 0.0]
        else:
            sel = [p for p in pieces if side(p) < 0.0]
        if not sel:
            raise ValueError(
                f"fm.trim_no_piece: keep='{args.keep}' selected no pieces — the "
                f"whole body ({n_total} piece(s)) lies on the other side of the "
                "plane (plane missed the body, or wrong keep side).")

        out = _build_compound(sel)
        kept_size = size_of(out)
        if out is None or out.IsNull() or kept_size <= eps_size:
            raise ValueError(
                f"fm.trim_degenerate: kept {size_key}={kept_size:.6g} ≈ 0.")

        input_size = size_of(shape)
        return SkillResult(
            body=Part(out),
            history=EntityHistoryMap(rules={
                "kept_faces": HistoryRule.MODIFIED_INHERIT,
                "cut_edges": HistoryRule.GENERATED_NEW}),
            extras={"trim_surface": {
                "input_kind": "solid" if is_solid else "face",
                "plane_origin_mm": list(args.plane_origin_mm),
                "plane_normal": [round(nx, 6), round(ny, 6), round(nz, 6)],
                "keep": args.keep,
                "n_pieces_total": n_total,
                "n_pieces_kept": len(sel),
                size_key: round(kept_size, 3),
                f"input_{size_key}": round(input_size, 3),
                "kept_fraction": (round(kept_size / input_size, 4)
                                  if input_size > eps_size else None),
                "grade": "measured",
            }},
        )
