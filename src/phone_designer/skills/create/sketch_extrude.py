"""sketch_extrude — atomic create skill (2026-07).

FROM-SCRATCH "draw a 2D profile (with arcs) → extrude into a solid". An LLM client
builds an ARBITRARY closed 2D profile — a composite of LINE + ARC + SPLINE segments
("a face with arcs"), or any of the closed primitives (circle/rectangle/polygon/
slot/ellipse/…) — and extrudes it along an axis. This is the clean, RE-free
counterpart to ``extrude_profile_world`` (which requires the recovered-section
frame: u_axis / section_origin_world / box_shift — all identity for a from-scratch
part, the documented awkwardness). The minimal call is just ``{sketch, height_mm}``.

Reuses the EXACT sketch→solid machinery pockets use: ``_build_planar_face``
(SketchSpec → planar face via the composite/arc/bspline dispatcher) +
``_extrude_face_along_z`` (BRepPrimAPI_MakePrism). Optional ``draft_deg`` tapers
the side walls (best-effort; on a self-intersecting offset it keeps the straight
prism and flags ``draft_applied=False`` — never a broken solid).

Honest: ``is_solid`` is gated on ``volume_mm3 > 0`` (the project convention,
generate_from_spec) — NOT BRepCheck.IsValid(), which reports a zero-area collinear
sliver as valid. A non-closed / endpoint-mismatch / arc-chord>2r profile is
rejected by SketchSpec's own pydantic validators BEFORE _apply; a degenerate /
self-intersecting (zero net area) profile is caught by the volume gate →
``fm.degenerate_profile``. Example composite-arc D-shape::

    {"sketch": {"kind": "composite", "segments": [
        {"kind": "line", "start": [-10, 0], "end": [10, 0]},
        {"kind": "arc",  "start": [10, 0], "end": [-10, 0], "radius": 10, "ccw": true}]},
     "height_mm": 5}
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec

_EPS_VOL = 1e-6


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(float(g.Mass()))


def _bbox_mm(shape) -> list[float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return [0.0, 0.0, 0.0]
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return [round(xmax - xmin, 3), round(ymax - ymin, 3), round(zmax - zmin, 3)]


_AXIS_DIR = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


def _place_in_world(shape, axis: str, origin_mm):
    """Map the local (origin, +Z normal) frame to world (origin_mm, sweep-axis).
    For axis='Z' + origin (0,0,0) this is the identity. The world X-reference must
    NOT be parallel to the sweep axis or gp_Ax3 raises a zero-norm-cross error —
    pick a reference that is not (the parallel-axis hazard the validators caught)."""
    n = _AXIS_DIR[axis]
    if axis == "Z" and origin_mm == (0.0, 0.0, 0.0):
        return shape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf
    xref = (0.0, 1.0, 0.0) if abs(n[0]) > 0.9 else (1.0, 0.0, 0.0)
    src = gp_Ax3(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0), gp_Dir(1.0, 0.0, 0.0))
    dst = gp_Ax3(gp_Pnt(*origin_mm), gp_Dir(*n), gp_Dir(*xref))
    tr = gp_Trsf()
    tr.SetTransformation(dst, src)
    return BRepBuilderAPI_Transform(shape, tr, True).Shape()


def _apply_draft(prism, axis: str, draft_deg: float):
    """Best-effort side-wall taper about the base plane. Returns (shape, applied)."""
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle
        from OCP.GeomAbs import GeomAbs_Plane
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
        from OCP.TopAbs import TopAbs_REVERSED

        from phone_designer.skills._resolvers import _all_faces
        n = _AXIS_DIR[axis]
        # neutral plane = base (z=0 in the local frame; the prism is still local here).
        neutral = gp_Pln(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0))
        pull = gp_Dir(0.0, 0.0, 1.0)
        draft = BRepOffsetAPI_DraftAngle(prism)
        ang = math.radians(draft_deg)
        added = 0
        for f in _all_faces(prism):
            surf = BRepAdaptor_Surface(f)
            if surf.GetType() != GeomAbs_Plane:
                continue
            d = surf.Plane().Axis().Direction()
            nx, ny, nz = d.X(), d.Y(), d.Z()
            if f.Orientation() == TopAbs_REVERSED:
                nx, ny, nz = -nx, -ny, -nz
            if abs(nz) > 1e-6:        # only the side walls (normal ⟂ Z)
                continue
            try:
                draft.Add(f, pull, ang, neutral, True)
            except Exception:
                continue
            if draft.AddDone():
                added += 1
        if added == 0:
            return prism, False
        draft.Build()
        if draft.IsDone():
            return draft.Shape(), True
    except Exception:
        pass
    return prism, False


@skill(
    name="sketch_extrude",
    category="create",
    level="atomic",
    summary="FROM-SCRATCH: extrude a closed 2D profile into a solid. The profile "
            "is a SketchSpec — for an ARBITRARY arc profile use kind='composite' "
            "with line/arc/spline segments forming a closed loop (arc="
            "{kind:'arc',start,end,radius,ccw}); also accepts circle/rectangle/"
            "polygon/slot/ellipse/bspline_closed/… Extrudes along axis (default "
            "+Z = sketch-plane normal) by height_mm; optional draft_deg tapers the "
            "walls. RE-free (no u_axis/section_origin/box_shift); minimal call is "
            "{sketch, height_mm}. is_solid gated on volume>0.",
    selector_kinds=[],
    history_rules={"output_faces": HistoryRule.GENERATED_NEW},
    produces_features=["sketch_solid"],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.profile_not_closed", "fm.degenerate_profile",
                   "fm.extrude_produced_null"],
    cost_hint=0.3,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class SketchExtrude(SkillBase):
    class Args(BaseModel):
        sketch: SketchSpec = Field(
            description="A closed 2D profile. For an ARBITRARY arc profile use "
                        "kind='composite' with segments of kind line/arc/spline "
                        "forming a closed loop (seg[i].end==seg[i+1].start, "
                        "last.end==first.start); arc={kind:'arc',start:[x,y],"
                        "end:[x,y],radius:r,ccw:true}. Also accepts circle/"
                        "rectangle/rounded_rect/polygon/slot/ellipse/"
                        "bspline_closed/parametric/polar/lissajous.")
        height_mm: float = Field(gt=0, le=100000.0,
                                description="Prism length along the axis.")
        axis: Literal["X", "Y", "Z"] = Field(
            default="Z",
            description="World sweep direction. Default +Z = the sketch-plane normal.")
        draft_deg: float = Field(
            default=0.0, ge=-30.0, le=30.0,
            description="Per-side wall taper about the BASE plane; >0 walls "
                        "converge toward the top. 0 = straight prism (proven path).")
        origin_mm: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="World placement of the sketch's local origin (prism base). "
                        "Identity by default.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _build_planar_face,
            _extrude_face_along_z,
        )
        sketch = args.sketch
        if isinstance(sketch, dict):
            sketch = TypeAdapter(SketchSpec).validate_python(sketch)

        try:
            face = _build_planar_face(sketch)
            prism = _extrude_face_along_z(face, args.height_mm, 1)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.profile_not_closed: sketch→face/extrude failed: "
                             f"{type(exc).__name__}: {exc}")

        draft_applied = False
        if args.draft_deg != 0.0:
            prism, draft_applied = _apply_draft(prism, args.axis, args.draft_deg)

        shape = _place_in_world(prism, args.axis, tuple(args.origin_mm))

        vol = _volume(shape)
        if shape is None or shape.IsNull() or vol <= _EPS_VOL:
            raise ValueError(
                f"fm.degenerate_profile: extruded volume {vol:.6g}mm³ ≈ 0 — the "
                "profile is degenerate or self-intersecting (zero net area).")

        return SkillResult(
            body=Part(shape),
            history=EntityHistoryMap(rules={"output_faces": HistoryRule.GENERATED_NEW}),
            extras={"sketch_solid": {
                "kind": "extrude",
                "profile_kind": getattr(sketch, "kind", "?"),
                "is_solid": True,
                "volume_mm3": round(vol, 3),
                "bbox_mm": _bbox_mm(shape),
                "axis": args.axis,
                "height_mm": args.height_mm,
                "draft_deg": args.draft_deg,
                "draft_applied": draft_applied,
                "origin_mm": list(args.origin_mm),
                "grade": "constructed",
            }},
        )
