"""helix_sweep — atomic create skill (2026-07).

FROM-SCRATCH "sweep a closed 2D cross-section along a HELIX path into a solid" —
threads, springs, augers, coiled tubing, worms, scrolls. Completes the classic
sweep family alongside sketch_sweep (line+arc spine): here the spine is an
analytic helix parameterised by (coil_radius, pitch, turns), so a caller does not
have to hand-author a helical polyline.

Reuses the codebase machinery:
  * ``_build_helix_wire_local`` (helical_spring) — the helix wire, a B-spline
    interpolated through ≥16 pts/turn about +Z at the origin.
  * ``_build_planar_face`` (modify_pocket._sketch_to_solid) — the section face in
    local XY (z=0, +Z normal), any closed SketchSpec kind.
  * ``_transform_face_to_frame`` (modify_boss.swept_boss_along_curve) — orient the
    section perpendicular to the helix START tangent (MANDATORY: a mis-oriented
    section sweeps to zero/garbage volume). The start tangent is computed with
    ``BRepAdaptor_CompCurve.D1`` off the built wire (not the analytic formula) so
    the section frame matches the actual spine the sweeper consumes.
  * ``BRepOffsetAPI_MakePipeShell`` → SetMode(frenet) → Add(section,False,False) →
    Build → MakeSolid (the sketch_sweep SOLID pattern).

THE HANG TRAP (verified this session): a bare ``TopoDS_Shape`` (what
``BRepBuilderAPI_Transform(...).Shape()`` returns) fed to MakePipeShell.Add or to
BRepAdaptor_CompCurve HANGS FOREVER (it does not raise). So the helix wire and the
oriented section wire are BOTH cast back to a concrete ``TopoDS.Wire_s`` before
they touch the sweeper.

``is_solid`` is gated on volume>1e-6 via BRepGProp.VolumeProperties_s (BRepCheck
lies both ways here). Verified via Pappus' theorem: a circle section swept along a
helix has volume ≈ section_area · helix_length, where
``helix_length ≈ turns · sqrt((2π·coil_radius)² + pitch²)``. Example single-start
thread-like coil::

    {"section": {"kind": "circle", "diameter_mm": 4},
     "coil_radius_mm": 10, "pitch_mm": 6, "turns": 3}
"""
from __future__ import annotations

import math
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


def _helix_length(coil_radius: float, pitch: float, turns: float) -> float:
    """Analytic arc length of the helix (one turn advances pitch in Z and
    2π·coil_radius circumferentially): L = turns·sqrt((2π·R)² + pitch²)."""
    return turns * math.sqrt((2.0 * math.pi * coil_radius) ** 2 + pitch ** 2)


@skill(
    name="helix_sweep",
    category="create",
    level="atomic",
    summary="FROM-SCRATCH: sweep a closed 2D cross-section along a HELIX into a "
            "solid (thread / spring / auger / coiled tubing / scroll). section is "
            "any closed SketchSpec; the spine is an analytic helix "
            "(coil_radius_mm, pitch_mm, turns) about +Z. Reuses the helix wire "
            "builder + MakePipeShell→MakeSolid, orienting the section to the helix "
            "START tangent. is_solid gated on volume>0; volume matches Pappus "
            "(section_area · helix_length). Refuses a section radial extent ≥ "
            "coil_radius (fm.helix_section_exceeds_coil — the inner wall folds "
            "through the axis) and a degenerate/zero swept volume.",
    selector_kinds=[],
    history_rules={"output_faces": HistoryRule.GENERATED_NEW},
    produces_features=["sketch_solid", "helix_sweep"],
    preserves=[],
    manufacturing={
        "additive_metal": {"min_wall_mm": 0.3},
        "cnc_5axis":      {"min_wall_mm": 0.5, "extras": {"requires_5axis": True}},
    },
    failure_modes=["fm.helix_section_exceeds_coil", "fm.helix_sweep_failed",
                   "fm.helix_sweep_degenerate"],
    cost_hint=0.4,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class HelixSweep(SkillBase):
    class Args(BaseModel):
        section: SketchSpec = Field(
            description="CLOSED 2D cross-section swept along the helix. Any closed "
                        "SketchSpec kind (circle/rectangle/polygon/composite/…). "
                        "Built in local XY, auto-oriented perpendicular to the "
                        "helix START tangent. Keep its radial extent < "
                        "coil_radius_mm or the inner wall folds through the axis.")
        coil_radius_mm: float = Field(
            gt=0,
            description="Helix radius: distance from the +Z axis to the section "
                        "centre. The section is centred on the helix path.")
        pitch_mm: float = Field(
            gt=0,
            description="Axial advance (along +Z) per full turn.")
        turns: float = Field(
            gt=0,
            description="Number of full 360° turns of the helix (may be fractional).")
        origin_mm: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="World translation of the swept coil. Identity by default.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAdaptor import BRepAdaptor_CompCurve
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
        from OCP.gp import gp_Pnt, gp_Vec
        from OCP.TopAbs import TopAbs_WIRE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        from phone_designer.skills.create.helical_spring import (
            _build_helix_wire_local,
        )
        from phone_designer.skills.create.sketch_extrude import _place_in_world
        from phone_designer.skills.modify_boss.swept_boss_along_curve import (
            _transform_face_to_frame,
        )
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _build_planar_face,
        )

        section = args.section
        if isinstance(section, dict):
            section = TypeAdapter(SketchSpec).validate_python(section)

        # ── section face (local XY) + self-intersection guard ─────────────────
        sec_face0 = _build_planar_face(section)
        sec_extent = max(_bbox_mm(sec_face0)[:2]) / 2.0  # in-plane half-extent
        if sec_extent >= args.coil_radius_mm - 1e-9:
            raise ValueError(
                f"fm.helix_section_exceeds_coil: section radial half-extent "
                f"{sec_extent:.3f}mm ≥ coil_radius_mm {args.coil_radius_mm:.3f}mm — "
                "the inner wall folds through the helix axis.")

        # ── helix spine wire (about +Z at origin) ─────────────────────────────
        # _build_helix_wire_local already returns a concrete TopoDS_Wire (built
        # via MakeWire.Wire()), but cast defensively so BRepAdaptor_CompCurve /
        # MakePipeShell never see a bare shape (the hang trap).
        try:
            spine = TopoDS.Wire_s(
                _build_helix_wire_local(
                    args.coil_radius_mm, args.pitch_mm, args.turns))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.helix_sweep_failed: helix wire build failed: "
                             f"{type(exc).__name__}: {exc}")

        # ── orient the section to the spine START tangent (mandatory) ─────────
        # Use the tangent of the ACTUAL built wire (D1 off the CompCurve) rather
        # than the analytic helix derivative, so the section frame matches the
        # spine the sweeper consumes even if the interpolation drifts slightly.
        cc = BRepAdaptor_CompCurve(spine)
        p, d = gp_Pnt(), gp_Vec()
        cc.D1(cc.FirstParameter(), p, d)
        tmag = math.sqrt(d.X() ** 2 + d.Y() ** 2 + d.Z() ** 2)
        if tmag < 1e-12:
            raise ValueError("fm.helix_sweep_degenerate: helix start tangent null.")
        tangent = (d.X() / tmag, d.Y() / tmag, d.Z() / tmag)
        oriented = _transform_face_to_frame(
            sec_face0, (p.X(), p.Y(), p.Z()), tangent)
        # _transform_face_to_frame returns a bare TopoDS_Shape (a Face); pull its
        # wire and cast to a concrete TopoDS_Wire — feeding a bare shape to
        # MakePipeShell.Add HANGS forever (it does not raise).
        sec_wire = TopoDS.Wire_s(TopExp_Explorer(oriented, TopAbs_WIRE).Current())

        # ── sweep the section along the helix → SOLID ─────────────────────────
        try:
            ps = BRepOffsetAPI_MakePipeShell(spine)
            ps.SetMode(True)  # frenet — section follows the helix tangent frame
            ps.Add(sec_wire, False, False)
            ps.Build()
            if not ps.IsDone() or not ps.MakeSolid():
                raise RuntimeError("MakePipeShell IsDone/MakeSolid False")
            shape = ps.Shape()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.helix_sweep_failed: MakePipeShell failed: "
                             f"{type(exc).__name__}: {exc}")

        shape = _place_in_world(shape, "Z", tuple(args.origin_mm))
        vol = _volume(shape)
        if shape is None or shape.IsNull() or vol <= _EPS_VOL:
            raise ValueError(
                f"fm.helix_sweep_degenerate: swept volume {vol:.6g}mm³ ≈ 0.")

        helix_len = _helix_length(args.coil_radius_mm, args.pitch_mm, args.turns)
        return SkillResult(
            body=Part(shape),
            history=EntityHistoryMap(
                rules={"output_faces": HistoryRule.GENERATED_NEW}),
            extras={"sketch_solid": {
                "kind": "helix_sweep",
                "section_kind": getattr(section, "kind", "?"),
                "is_solid": True,
                "volume_mm3": round(vol, 3),
                "bbox_mm": _bbox_mm(shape),
                "helix_length_mm": round(helix_len, 3),
                "coil_radius_mm": args.coil_radius_mm,
                "pitch_mm": args.pitch_mm,
                "turns": args.turns,
                "origin_mm": list(args.origin_mm),
                "grade": "constructed",
            }},
        )
