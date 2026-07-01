"""sketch_loft — atomic create skill (2026-07).

FROM-SCRATCH "loft (interpolate) a solid through ≥2 ordered 2D sections at given
heights" — a tapered transition, a frustum, a circle→square blend. Completes the
classic CAD set alongside sketch_extrude / sketch_revolve / sketch_sweep.

Reuses the loft_boss_between_sketches pattern MINUS the fuse: ``_build_planar_face``
(each section face in local XY) → outer wire → ``_translate`` to the section's z →
``BRepOffsetAPI_ThruSections(isSolid=True, isRuled)`` AddWire each → Build. Returns
the lofted solid directly (no base body, no boolean — the clean from-scratch loft
that loft_boss/loft_pocket cannot do).

Sections stack along +Z (heights_mm, strictly increasing). ``ruled=False`` (default)
is a smooth C1 loft that tolerates mismatched vertex counts (circle→square);
``ruled=True`` is a faceted linear transition (cleanest when vertex counts match).
Stacking is +Z-ONLY in v1 — a non-Z stack axis is verified to produce a degenerate
solid (the sibling sketch_revolve refuses a non-Z axis for the same reason), so it
is honestly out of scope rather than a lying arg. ``is_solid`` gated on volume>1e-6.
Example frustum::

    {"sections": [{"kind": "rectangle", "length_mm": 10, "width_mm": 10},
                  {"kind": "rectangle", "length_mm": 4,  "width_mm": 4}],
     "heights_mm": [0, 10]}
"""
from __future__ import annotations

from typing import Any

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


def _outer_wire(face):
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    it = TopExp_Explorer(face, TopAbs_WIRE)
    if not it.More():
        raise ValueError("fm.loft_failed: section face has no wire")
    return TopoDS.Wire_s(it.Current())


def _translate_wire(wire, dz: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.TopoDS import TopoDS
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(0.0, 0.0, dz))
    # .Shape() returns a bare TopoDS_Shape; ThruSections.AddWire REQUIRES a
    # concrete TopoDS_Wire — an untyped shape makes Build() HANG (not raise).
    return TopoDS.Wire_s(BRepBuilderAPI_Transform(wire, t, True).Shape())


@skill(
    name="sketch_loft",
    category="create",
    level="atomic",
    summary="FROM-SCRATCH: loft (interpolate) a solid through ≥2 closed 2D "
            "sections at given heights along +Z (a frustum / tapered transition / "
            "circle→square blend). sections = list[SketchSpec] (any closed kind); "
            "heights_mm = parallel list, strictly increasing. ruled=False smooth "
            "(tolerates mismatched vertex counts) | True faceted. Reuses "
            "ThruSections(isSolid) directly (no base body / no boolean — the clean "
            "from-scratch loft). +Z stacking only. is_solid gated on volume>0.",
    selector_kinds=[],
    history_rules={"output_faces": HistoryRule.GENERATED_NEW},
    produces_features=["sketch_solid"],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.loft_height_count_mismatch", "fm.loft_heights_not_monotonic",
                   "fm.loft_failed", "fm.loft_degenerate"],
    cost_hint=0.4,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class SketchLoft(SkillBase):
    class Args(BaseModel):
        sections: list[SketchSpec] = Field(
            min_length=2,
            description="Ordered ≥2 CLOSED 2D sections lofted through in order. "
                        "Any closed SketchSpec kind. Each built in local XY, placed "
                        "on a plane at its height along +Z.")
        heights_mm: list[float] = Field(
            min_length=2,
            description="Z of each section's plane, one per section, STRICTLY "
                        "INCREASING. len must == len(sections). E.g. [0, 10].")
        ruled: bool = Field(
            default=False,
            description="False (default) = smooth C1 loft (tolerates mismatched "
                        "vertex counts, e.g. circle→square). True = ruled "
                        "(straight linear transitions; cleanest when counts match).")
        origin_mm: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="World translation of the lofted solid. Identity by default.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections

        from phone_designer.skills.create.sketch_extrude import _place_in_world
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _build_planar_face,
        )
        sections = [
            TypeAdapter(SketchSpec).validate_python(s) if isinstance(s, dict) else s
            for s in args.sections
        ]
        heights = list(args.heights_mm)

        # ── up-front cross-field guards ───────────────────────────────────────
        if len(heights) != len(sections):
            raise ValueError(
                f"fm.loft_height_count_mismatch: {len(heights)} heights_mm != "
                f"{len(sections)} sections.")
        for i in range(len(heights) - 1):
            if heights[i + 1] <= heights[i]:
                raise ValueError(
                    f"fm.loft_heights_not_monotonic: heights_mm must be strictly "
                    f"increasing; heights[{i + 1}]={heights[i + 1]} <= "
                    f"heights[{i}]={heights[i]}.")

        # ── loft via ThruSections (isSolid=True) — no fuse ────────────────────
        try:
            loft = BRepOffsetAPI_ThruSections(True, args.ruled, 1e-6)
            for sk, z in zip(sections, heights):
                wire = _outer_wire(_build_planar_face(sk))
                loft.AddWire(_translate_wire(wire, z))
            loft.Build()
            if not loft.IsDone():
                raise RuntimeError("ThruSections IsDone False")
            shape = loft.Shape()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.loft_failed: ThruSections failed: "
                             f"{type(exc).__name__}: {exc}")

        shape = _place_in_world(shape, "Z", tuple(args.origin_mm))
        vol = _volume(shape)
        if shape is None or shape.IsNull() or vol <= _EPS_VOL:
            raise ValueError(
                f"fm.loft_degenerate: lofted volume {vol:.6g}mm³ ≈ 0 — coincident "
                "sections or a degenerate profile.")

        return SkillResult(
            body=Part(shape),
            history=EntityHistoryMap(rules={"output_faces": HistoryRule.GENERATED_NEW}),
            extras={"sketch_solid": {
                "kind": "loft",
                "n_sections": len(sections),
                "section_kinds": [getattr(s, "kind", "?") for s in sections],
                "is_solid": True,
                "volume_mm3": round(vol, 3),
                "bbox_mm": _bbox_mm(shape),
                "heights_mm": heights,
                "ruled": args.ruled,
                "origin_mm": list(args.origin_mm),
                "grade": "constructed",
            }},
        )
