"""sketch_sweep — atomic create skill (2026-07).

FROM-SCRATCH "sweep a 2D cross-section along a line+arc PATH into a solid" (a pipe,
handle, gasket frame, cable guard). Completes the classic CAD set alongside
sketch_extrude / sketch_revolve. The section is any closed SketchSpec; the PATH is
a NEW ``SweepPath`` — an OPEN chain of line/arc/spline segments (the same Segment
union sketch profiles use, so arcs are first-class + auto-published for discovery).

Reuses the codebase machinery: ``_build_planar_face`` (section face),
``_edge_line``/``_edge_arc_radius``/``_wire_bspline_open`` + ``_wire_from_edges``
(path wire), ``_transform_face_to_frame`` (orient the section to the path START
tangent — MANDATORY: a mis-oriented section sweeps to zero volume),
``BRepOffsetAPI_MakePipeShell`` + ``MakeSolid`` (the worm_thread/helical_spring
sweep pattern, upgraded to a SOLID).

HONEST — two up-front geometric guards the OCCT builders do NOT provide (verified:
OCCT builds a folded/kinked sweep at IsDone=True with a garbage or zero volume):
  * ``fm.sweep_tangent_discontinuity`` — MakePipeShell SILENTLY DROPS the swept
    volume at a sharp corner (a 90° line→arc kink yields the straight-only volume,
    a lie). So a joint whose tangent turns > 30° is refused; the path must be
    tangent-continuous (G1). Split into separate sweeps or insert a fillet arc.
  * ``fm.sweep_self_intersection`` — the helical-spring law: if the section's radial
    extent ≥ the path's smallest bend radius, the inner wall folds through the arc
    centre. Conservative up-front refusal.
``is_solid`` is gated on volume>1e-6 (BRepCheck.IsValid lies both ways here). Example
bent pipe (tangent-continuous)::

    {"section": {"kind": "circle", "diameter_mm": 6},
     "path": {"kind": "path", "plane": "XY", "segments": [
        {"kind": "line", "start": [0, 0], "end": [20, 0]},
        {"kind": "arc",  "start": [20, 0], "end": [28, 8], "radius": 8, "ccw": true},
        {"kind": "line", "start": [28, 8], "end": [28, 28]}]}}
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, model_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import EPS_CLOSE, Segment, SketchSpec

_EPS_VOL = 1e-6
_KINK_MAX_DEG = 30.0


class SweepPath(BaseModel):
    """OPEN line/arc/spline chain the section is swept along (NOT auto-closed)."""

    kind: Literal["path"] = "path"
    segments: list[Segment] = Field(
        min_length=1,
        description="Ordered OPEN chain of line/arc/spline segments; "
                    "seg[i].end==seg[i+1].start (within 1µm). NOT auto-closed. "
                    "Must be TANGENT-CONTINUOUS (no >30° kink) — a sharp corner "
                    "makes the sweep silently drop volume.")
    plane: Literal["XY", "XZ", "YZ"] = Field(
        default="XY",
        description="World plane the 2D path lives in (v1 = planar path). The "
                    "section attaches at segments[0].start.")

    @model_validator(mode="after")
    def _chain_open(self):
        segs = self.segments
        for i in range(len(segs) - 1):
            ex, ey = segs[i].end
            sx, sy = segs[i + 1].start
            if math.hypot(ex - sx, ey - sy) > EPS_CLOSE:
                raise ValueError(
                    f"fm.sweep_path_disconnected: seg {i}.end {segs[i].end} != "
                    f"seg {i + 1}.start {segs[i + 1].start}")
        if len(segs) >= 2:
            fx, fy = segs[0].start
            lx, ly = segs[-1].end
            if math.hypot(fx - lx, fy - ly) <= EPS_CLOSE:
                raise ValueError(
                    "fm.sweep_path_closed: path is CLOSED (last.end==first.start); "
                    "a sweep spine must be OPEN (use sketch_revolve/loft instead).")
        return self


def _unit2(v):
    m = math.hypot(v[0], v[1])
    return (v[0] / m, v[1] / m) if m > 1e-12 else (0.0, 0.0)


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


def _edge_tangents(edge):
    """(entry_tan, exit_tan) 2D unit tangents at the edge's first/last param
    (direction of increasing param = start→end for our forward-built edges)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.gp import gp_Pnt, gp_Vec
    ac = BRepAdaptor_Curve(edge)
    p, d = gp_Pnt(), gp_Vec()
    ac.D1(ac.FirstParameter(), p, d)
    entry = _unit2((d.X(), d.Y()))
    p, d = gp_Pnt(), gp_Vec()
    ac.D1(ac.LastParameter(), p, d)
    exit_ = _unit2((d.X(), d.Y()))
    return entry, exit_


_PLANE_ROT = {"XY": None, "XZ": ("X", 90.0), "YZ": ("Y", -90.0)}


def _rotate_to_plane(shape, plane: str):
    """Rotate the XY-built spine into the chosen world plane (XY = identity)."""
    rot = _PLANE_ROT[plane]
    if rot is None:
        return shape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf
    from OCP.TopoDS import TopoDS
    ax_name, deg = rot
    axd = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0)}[ax_name]
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*axd)), math.radians(deg))
    # .Shape() returns a bare TopoDS_Shape; downstream BRepAdaptor_CompCurve and
    # MakePipeShell REQUIRE a concrete TopoDS_Wire — cast back (else a hang/raise).
    return TopoDS.Wire_s(BRepBuilderAPI_Transform(shape, t, True).Shape())


@skill(
    name="sketch_sweep",
    category="create",
    level="atomic",
    summary="FROM-SCRATCH: sweep a closed 2D cross-section along a line+arc PATH "
            "into a solid (pipe/handle/gasket). section is any closed SketchSpec; "
            "path is a SweepPath (OPEN chain of line/arc/spline segments, "
            "tangent-continuous). Reuses MakePipeShell→MakeSolid. Refuses a kinked "
            "path (fm.sweep_tangent_discontinuity — MakePipeShell silently drops "
            "volume at a >30° corner) and a section wider than the bend radius "
            "(fm.sweep_self_intersection). is_solid gated on volume>0.",
    selector_kinds=[],
    history_rules={"output_faces": HistoryRule.GENERATED_NEW},
    produces_features=["sketch_solid"],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.sweep_path_disconnected", "fm.sweep_path_closed",
                   "fm.sweep_tangent_discontinuity", "fm.sweep_self_intersection",
                   "fm.sweep_failed", "fm.sweep_degenerate"],
    cost_hint=0.4,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class SketchSweep(SkillBase):
    class Args(BaseModel):
        section: SketchSpec = Field(
            description="CLOSED 2D cross-section swept along the path. Any closed "
                        "SketchSpec kind (circle/rectangle/polygon/composite(line/"
                        "arc/spline)/…). Built in local XY, auto-oriented "
                        "perpendicular to the path START tangent.")
        path: SweepPath = Field(
            description="OPEN line+arc(+spline) polyline the section sweeps along. "
                        "{kind:'path', segments:[{kind:'line',start,end}|{kind:"
                        "'arc',start,end,radius,ccw}], plane}. Tangent-continuous.")
        orientation: Literal["frenet", "fixed"] = Field(
            default="frenet",
            description="frenet = section stays perpendicular to the path tangent "
                        "(follows the curve); fixed = constant orientation (fixed "
                        "trihedron — sections stay parallel, so volume = area × "
                        "net advance along the start tangent; a path turning ≥90° "
                        "from the start tangent is refused, fm.sweep_degenerate).")
        origin_mm: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="World translation of the swept solid. Identity by default.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell

        from phone_designer.skills.create.sketch_extrude import _place_in_world
        from phone_designer.skills.modify_boss.swept_boss_along_curve import (
            _transform_face_to_frame,
        )
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _build_planar_face,
            _edge_arc_radius,
            _edge_line,
            _wire_bspline_open,
            _wire_from_edges,
        )
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_WIRE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        section = args.section
        if isinstance(section, dict):
            section = TypeAdapter(SketchSpec).validate_python(section)
        path = args.path
        if isinstance(path, dict):
            path = TypeAdapter(SweepPath).validate_python(path)

        from OCP.gp import gp_Pnt

        # ── build the path EDGES (XY) in order ────────────────────────────────
        # NOTE: _edge_line takes gp_Pnt (calls MakeEdge(p1,p2) directly), whereas
        # _edge_arc_radius takes 2D (x,y) tuples — so wrap line endpoints here.
        edges = []
        for s in path.segments:
            if s.kind == "line":
                edges.append(_edge_line(gp_Pnt(s.start[0], s.start[1], 0.0),
                                        gp_Pnt(s.end[0], s.end[1], 0.0)))
            elif s.kind == "arc":
                edges.append(_edge_arc_radius(s.start, s.end, s.radius, s.ccw))
            else:  # spline — explode its wire into edges
                w = _wire_bspline_open(list(s.points))
                ex = TopExp_Explorer(w, TopAbs_EDGE)
                while ex.More():
                    edges.append(TopoDS.Edge_s(ex.Current()))
                    ex.Next()
        # ── GUARD 1: tangent continuity (kink) — MakePipeShell silently drops
        # volume at a sharp corner, so refuse >30° joints up front. ────────────
        tans = []
        for e in edges:
            try:
                tans.append(_edge_tangents(e))
            except Exception:
                tans.append(((0.0, 0.0), (0.0, 0.0)))
        for i in range(len(tans) - 1):
            ex = tans[i][1]
            en = tans[i + 1][0]
            dot = max(-1.0, min(1.0, ex[0] * en[0] + ex[1] * en[1]))
            kink = math.degrees(math.acos(dot)) if (ex != (0.0, 0.0) and en != (0.0, 0.0)) else 0.0
            if kink > _KINK_MAX_DEG:
                raise ValueError(
                    f"fm.sweep_tangent_discontinuity: joint {i} turns {kink:.1f}° "
                    f"> {_KINK_MAX_DEG:.0f}°; MakePipeShell silently drops swept "
                    "volume at a sharp corner — insert a fillet arc so the segment "
                    "tangents match, or split into separate sweeps.")
        # fixed orientation keeps the section normal at the START tangent; if a
        # later path tangent turns ≥90° from it, the parallel sections travel
        # WITHIN their own plane and the swept wall collapses to zero thickness
        # (OCCT still "succeeds" with a degenerate flap) — refuse up front.
        # Line/arc tangent angles are monotone per edge, so endpoint checks
        # catch any perpendicular crossing.
        if args.orientation == "fixed":
            t0 = tans[0][0]
            for i, (en, ex) in enumerate(tans):
                for t in (en, ex):
                    if t == (0.0, 0.0):
                        continue
                    if t[0] * t0[0] + t[1] * t0[1] <= 1e-9:
                        raise ValueError(
                            f"fm.sweep_degenerate: orientation='fixed' but the "
                            f"path tangent at edge {i} turns ≥90° from the start "
                            "tangent — parallel sections collapse to a zero-"
                            "thickness wall there; use orientation='frenet' or "
                            "split the path.")

        # ── GUARD 2: self-intersection (section wider than the bend radius) ───
        # Radial extent is measured about the section's LOCAL ORIGIN (raw bbox
        # min/max), NOT the bbox half-SIZE: _transform_face_to_frame attaches
        # local (0,0) to the spine, so a center_x/y_mm offset rides the whole
        # section off the spine and its true reach includes that offset. For an
        # origin-centred section this equals the old half-size. Conservative by
        # design — the planar guard has no per-arc bend-centre side info, so an
        # offset toward the OUTER side is refused too.
        sec_face0 = _build_planar_face(section)
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        sbb = Bnd_Box()
        try:
            BRepBndLib.AddOptimal_s(sec_face0, sbb)
        except Exception:
            BRepBndLib.Add_s(sec_face0, sbb)
        sxmin, symin, _szmin, sxmax, symax, _szmax = sbb.Get()
        sec_extent = max(abs(sxmin), abs(sxmax), abs(symin), abs(symax))
        bend_radii = [s.radius for s in path.segments if s.kind == "arc"]
        if bend_radii and sec_extent >= min(bend_radii) - 1e-9:
            raise ValueError(
                f"fm.sweep_self_intersection: section radial extent "
                f"{sec_extent:.3f}mm ≥ smallest path bend radius {min(bend_radii):.3f}"
                "mm — the inner wall folds through the arc centre.")

        # ── spine wire (rotate to the chosen plane) ───────────────────────────
        try:
            spine = _wire_from_edges(edges)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.sweep_failed: path wire build failed: "
                             f"{type(exc).__name__}: {exc}")
        spine = _rotate_to_plane(spine, path.plane)

        # ── orient the section to the spine START tangent (mandatory) ─────────
        from OCP.BRepAdaptor import BRepAdaptor_CompCurve
        from OCP.gp import gp_Pnt, gp_Vec
        cc = BRepAdaptor_CompCurve(spine)
        p, d = gp_Pnt(), gp_Vec()
        cc.D1(cc.FirstParameter(), p, d)
        tmag = math.sqrt(d.X() ** 2 + d.Y() ** 2 + d.Z() ** 2)
        if tmag < 1e-12:
            raise ValueError("fm.sweep_degenerate: path start tangent is null.")
        tangent = (d.X() / tmag, d.Y() / tmag, d.Z() / tmag)
        oriented = _transform_face_to_frame(
            sec_face0, (p.X(), p.Y(), p.Z()), tangent)
        sec_wire = TopoDS.Wire_s(TopExp_Explorer(oriented, TopAbs_WIRE).Current())

        # ── sweep → SOLID ─────────────────────────────────────────────────────
        try:
            ps = BRepOffsetAPI_MakePipeShell(spine)
            if args.orientation == "fixed":
                # TRUE constant orientation = the fixed-trihedron overload
                # SetMode(gp_Ax2) ("all sections will be parallel" per OCCT).
                # SetMode(False) would be CORRECTED-FRENET — the section still
                # rotates with the tangent, byte-identical to 'frenet' on a
                # bent path (verified), i.e. NOT fixed.
                from OCP.gp import gp_Ax2, gp_Dir
                ps.SetMode(gp_Ax2(gp_Pnt(p.X(), p.Y(), p.Z()),
                                  gp_Dir(*tangent)))
            else:
                ps.SetMode(True)  # frenet trihedron
            ps.Add(sec_wire, False, False)
            ps.Build()
            if not ps.IsDone() or not ps.MakeSolid():
                raise RuntimeError("MakePipeShell IsDone/MakeSolid False")
            shape = ps.Shape()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.sweep_failed: MakePipeShell failed: "
                             f"{type(exc).__name__}: {exc}")

        shape = _place_in_world(shape, "Z", tuple(args.origin_mm))
        vol = _volume(shape)
        if shape is None or shape.IsNull() or vol <= _EPS_VOL:
            raise ValueError(
                f"fm.sweep_degenerate: swept volume {vol:.6g}mm³ ≈ 0.")

        # path length for the report.
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        lp = GProp_GProps()
        BRepGProp.LinearProperties_s(spine, lp)
        return SkillResult(
            body=Part(shape),
            history=EntityHistoryMap(rules={"output_faces": HistoryRule.GENERATED_NEW}),
            extras={"sketch_solid": {
                "kind": "sweep",
                "section_kind": getattr(section, "kind", "?"),
                "is_solid": True,
                "volume_mm3": round(vol, 3),
                "bbox_mm": _bbox_mm(shape),
                "path_len_mm": round(float(lp.Mass()), 3),
                "n_segments": len(path.segments),
                "plane": path.plane,
                "orientation": args.orientation,
                "origin_mm": list(args.origin_mm),
                "grade": "constructed",
            }},
        )
