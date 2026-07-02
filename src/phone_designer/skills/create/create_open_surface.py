"""create_open_surface — atomic create skill (2026-07).

THE GATEWAY TO SURFACE-FIRST MODELING: emit an OPEN surface (a bare face / shell,
NOT a solid) that spans ≥2 ordered 2D sections at given heights along +Z. Where
``sketch_loft`` closes the transition into a watertight SOLID (ThruSections with
isSolid=True), this skill leaves the skin OPEN — the two end sections are NOT
capped — so the result is a surface body you can then thicken, trim, blend, or
sew with other surfaces. Precedent for returning a bare face: bspline_surface /
surface_blend_g1 (post_condition body_present, NOT solid).

Two modes:
  * ``lofted`` (default) — ``BRepOffsetAPI_ThruSections(isSolid=FALSE, isRuled,
    1e-6)`` AddWire each section wire → Build → the un-capped skin. Note the ONLY
    difference from sketch_loft is the isSolid flag: FALSE gives the open skin,
    TRUE gives the solid. ``ruled=False`` smooths (tolerates mismatched vertex
    counts, e.g. square→circle); ``ruled=True`` is a faceted linear transition.
  * ``ruled`` — exactly TWO sections; ``BRepFill.Shell_s(wire1, wire2)`` builds a
    single ruled shell directly between the two outer wires (the classic
    "loft between two curves" primitive, no ThruSections). The two wires MUST
    have matching edge counts — BRepFill.Shell pairs edges 1:1 and mismatches
    silently yield a partial band, so they are refused up front
    (fm.surface_ruled_incompatible_wires; use mode='lofted' instead).

Sections stack along +Z (heights_mm, strictly increasing), reusing the exact
section machinery of sketch_loft: ``_build_planar_face`` → ``_outer_wire`` →
``_translate_wire``. Verified via SurfaceProperties_s: the open skin has
surface_area > 0 (and, being un-capped, is strictly LESS than the same closed
loft's total boundary area — the two end caps are absent).

THE HANG TRAP (shared with sketch_loft/sketch_sweep): ``_translate_wire`` casts
the BRepBuilderAPI_Transform result back to a concrete ``TopoDS.Wire_s`` — a bare
TopoDS_Shape fed to ThruSections.AddWire / BRepFill.Shell_s HANGS FOREVER (not
raises). A passing (non-timeout) build proves the cast is in place. Example open
frustum skin::

    {"sections": [{"kind": "rectangle", "length_mm": 20, "width_mm": 20},
                  {"kind": "rectangle", "length_mm": 10, "width_mm": 10}],
     "heights_mm": [0, 10], "mode": "lofted"}
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.create.sketch_loft import _outer_wire, _translate_wire
from phone_designer.skills.modify_pocket._sketch import SketchSpec

_EPS_AREA = 1e-6


def _surface_area(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, g)
    return abs(float(g.Mass()))


def _bbox_mm(shape) -> list[float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:  # noqa: BLE001
        BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return [0.0, 0.0, 0.0]
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return [round(xmax - xmin, 3), round(ymax - ymin, 3), round(zmax - zmin, 3)]


def _is_solid(shape) -> bool:
    """Honestly False for an open skin. TRAP: VolumeProperties_s applies the
    divergence theorem to an OPEN shell and returns a nonzero pseudo-'mass' even
    with no enclosed volume — so a volume>0 gate would LIE here (report True for
    an un-capped shell). The topology never lies: an open surface is a SHELL/FACE
    with ZERO TopAbs_SOLID sub-shapes, so count the solids instead."""
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    ex = TopExp_Explorer(shape, TopAbs_SOLID)
    return ex.More()


@skill(
    name="create_open_surface",
    category="create",
    level="atomic",
    summary="FROM-SCRATCH surface-first gateway: emit an OPEN surface (bare "
            "face/shell, NOT a solid) spanning ≥2 closed 2D sections at given "
            "heights along +Z. mode='lofted' = ThruSections(isSolid=FALSE) "
            "un-capped skin (ruled=False smooth | True faceted); mode='ruled' = "
            "BRepFill.Shell between exactly two section wires. Unlike sketch_loft "
            "(isSolid=TRUE solid) the ends are NOT capped, so the result is a "
            "surface body to thicken/trim/blend/sew. +Z stacking only. Returns a "
            "Face/Shell (body_present, NOT solid); surface_area>0.",
    selector_kinds=[],
    history_rules={"output_faces": HistoryRule.GENERATED_NEW},
    produces_features=["open_surface"],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.surface_height_count_mismatch",
                   "fm.surface_heights_not_monotonic",
                   "fm.surface_ruled_needs_two_sections",
                   "fm.surface_ruled_incompatible_wires",
                   "fm.surface_failed", "fm.surface_degenerate"],
    cost_hint=0.35,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class CreateOpenSurface(SkillBase):
    class Args(BaseModel):
        sections: list[SketchSpec] = Field(
            min_length=2,
            description="Ordered ≥2 CLOSED 2D sections the open surface spans. "
                        "Any closed SketchSpec kind. Each built in local XY, "
                        "placed on a plane at its height along +Z. mode='ruled' "
                        "requires EXACTLY two.")
        heights_mm: list[float] = Field(
            min_length=2,
            description="Z of each section's plane, one per section, STRICTLY "
                        "INCREASING. len must == len(sections). E.g. [0, 10].")
        mode: Literal["lofted", "ruled"] = Field(
            default="lofted",
            description="'lofted' = ThruSections(isSolid=FALSE) open skin through "
                        "ALL sections (see ruled flag). 'ruled' = BRepFill.Shell "
                        "between EXACTLY two section wires (a single ruled band).")
        ruled: bool = Field(
            default=False,
            description="Only for mode='lofted'. False (default) = smooth C1 skin "
                        "(tolerates mismatched vertex counts, e.g. square→circle). "
                        "True = ruled (straight linear transitions). Ignored when "
                        "mode='ruled' (that mode is always a ruled band).")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

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
                f"fm.surface_height_count_mismatch: {len(heights)} heights_mm != "
                f"{len(sections)} sections.")
        for i in range(len(heights) - 1):
            if heights[i + 1] <= heights[i]:
                raise ValueError(
                    f"fm.surface_heights_not_monotonic: heights_mm must be strictly "
                    f"increasing; heights[{i + 1}]={heights[i + 1]} <= "
                    f"heights[{i}]={heights[i]}.")
        if args.mode == "ruled" and len(sections) != 2:
            raise ValueError(
                f"fm.surface_ruled_needs_two_sections: mode='ruled' spans EXACTLY "
                f"two sections (got {len(sections)}); use mode='lofted' for ≥3.")

        # ── build each section's translated OUTER wire (the sketch_loft path) ──
        wires = [
            _translate_wire(_outer_wire(_build_planar_face(sk)), z)
            for sk, z in zip(sections, heights)
        ]

        # ── ruled compatibility guard (BEFORE the try — its ValueError must not
        # be re-wrapped as fm.surface_failed) ─────────────────────────────────
        if args.mode == "ruled":
            # BRepFill.Shell_s pairs the two wires' edges 1:1. With mismatched
            # edge counts it silently returns a PARTIAL band (verified: rect
            # 4 edges → circle 1 edge gave a single 96.8mm² face with bbox
            # [10, 7, 10], not even spanning the sections). Refuse honestly.
            from OCP.TopAbs import TopAbs_EDGE
            from OCP.TopExp import TopExp_Explorer

            def _n_edges(w) -> int:
                n = 0
                ex = TopExp_Explorer(w, TopAbs_EDGE)
                while ex.More():
                    n += 1
                    ex.Next()
                return n

            n0, n1 = _n_edges(wires[0]), _n_edges(wires[1])
            if n0 != n1:
                raise ValueError(
                    f"fm.surface_ruled_incompatible_wires: wire edge counts "
                    f"{n0} vs {n1} — BRepFill.Shell requires matching edge "
                    "counts; use mode='lofted' (ThruSections tolerates "
                    "mismatched sections).")

        # ── build the OPEN surface ────────────────────────────────────────────
        try:
            if args.mode == "ruled":
                # BRepFill.Shell_s(wire1, wire2) → a single ruled shell band. The
                # wires MUST be concrete TopoDS_Wire (already cast by _translate_wire).
                from OCP.BRepFill import BRepFill
                shape = BRepFill.Shell_s(wires[0], wires[1])
            else:
                # ThruSections with isSolid=FALSE → un-capped skin. The ONLY change
                # from sketch_loft is this first flag (True→False): no end caps.
                from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
                skin = BRepOffsetAPI_ThruSections(False, args.ruled, 1e-6)
                for w in wires:
                    skin.AddWire(w)
                skin.Build()
                if not skin.IsDone():
                    raise RuntimeError("ThruSections IsDone False")
                shape = skin.Shape()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"fm.surface_failed: {args.mode} build failed: "
                f"{type(exc).__name__}: {exc}")

        area = _surface_area(shape)
        if shape is None or shape.IsNull() or area <= _EPS_AREA:
            raise ValueError(
                f"fm.surface_degenerate: surface area {area:.6g}mm² ≈ 0 — "
                "coincident sections or a degenerate profile.")

        return SkillResult(
            body=Part(shape),
            history=EntityHistoryMap(
                rules={"output_faces": HistoryRule.GENERATED_NEW}),
            extras={"open_surface": {
                "kind": "open_surface",
                "mode": args.mode,
                "n_sections": len(sections),
                "section_kinds": [getattr(s, "kind", "?") for s in sections],
                "is_solid": _is_solid(shape),   # honestly False — an open skin
                "surface_area_mm2": round(area, 3),
                "bbox_mm": _bbox_mm(shape),
                "heights_mm": heights,
                "ruled": args.ruled if args.mode == "lofted" else True,
                "grade": "constructed",
            }},
        )
