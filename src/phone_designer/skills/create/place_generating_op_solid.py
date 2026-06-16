"""place_generating_op_solid — atomic create skill (PILLAR FREEFORM, 2026-06-16).

Build the EDITABLE parametric base solid recovered by
``inspect/classify_generating_op``: a REVOLVE of a recovered meridian profile, a
LOFT through N recovered sections, or a SWEEP of a constant section along a path.
Unlike ``place_freeform_solid`` (which re-solidifies the imported boundary —
near-lossless but NOT editable) this rebuilds the body FROM the recovered
generating-op parameters, so the body is parametric: change ``profile_scale`` (or
the sketches/path) and the rebuilt solid moves coherently.

This is a CREATE skill (base step). The modify_boss family (revolve_boss /
loft_boss_between_sketches / swept_boss_along_curve) all FUSE onto an existing
body via a face_selector, so they cannot be a from-scratch base; this skill
reuses their EXACT geometry cores (``_build_revolved_solid``, ThruSections,
MakePipe) to emit the solid directly, then lands it in the box frame via
``box_shift`` (-cx, -cy, -zmin) so it coincides with the placeholder box base it
replaces.

``profile_scale`` (default 1.0 — identity) uniformly scales the recovered
profile/section 2D coords about their own origin BEFORE building. This is the
editability hook: ``profile_scale=1.2`` grows the revolve meridian radius 1.2×
and the rebuilt body's volume/bbox move parametrically — the whole point of a
parametric reconstruction.

post_conditions = [body_present]. Same create-skill contract as ``box`` /
``extrude_profile_world`` / ``place_freeform_solid``.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _scale_sketch_2d(sketch: dict, s: float) -> dict:
    """Return a copy of an executable sketch dict with every 2D coord ×``s``.

    Handles the three loop kinds recover_section_loop / classify_generating_op
    emit (polygon | bspline_closed | composite). Identity at s == 1.0.
    """
    if s == 1.0:
        return dict(sketch)
    out = dict(sketch)
    kind = sketch.get("kind")
    if kind == "polygon":
        out["vertices"] = [(x * s, y * s) for (x, y) in sketch["vertices"]]
    elif kind == "bspline_closed":
        out["fit_points"] = [(x * s, y * s) for (x, y) in sketch["fit_points"]]
    elif kind == "composite":
        new_segs = []
        for seg in sketch["segments"]:
            ns = dict(seg)
            if "start" in seg and "end" in seg:
                ns["start"] = (seg["start"][0] * s, seg["start"][1] * s)
                ns["end"] = (seg["end"][0] * s, seg["end"][1] * s)
                if "radius" in seg:
                    ns["radius"] = seg["radius"] * s
            elif "points" in seg:
                ns["points"] = [(p[0] * s, p[1] * s) for p in seg["points"]]
            new_segs.append(ns)
        out["segments"] = new_segs
    return out


def _translate(shape, dx: float, dy: float, dz: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec

    if not (dx or dy or dz):
        return shape
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(dx, dy, dz))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def build_revolve_solid(
    profile_sketch: dict,
    axis_origin: tuple[float, float, float],
    axis_dir: tuple[float, float, float],
    angle_deg: float = 360.0,
    profile_scale: float = 1.0,
):
    """Revolve the (scaled) meridian profile about the axis. Reuses
    revolve_boss / revolve_pocket's exact ``_build_revolved_solid`` core."""
    from pydantic import TypeAdapter

    from phone_designer.skills.modify_pocket._sketch import SketchSpec
    from phone_designer.skills.modify_pocket.revolve_pocket import (
        _build_revolved_solid,
    )

    sk = _scale_sketch_2d(profile_sketch, float(profile_scale))
    spec = TypeAdapter(SketchSpec).validate_python(sk)
    return _build_revolved_solid(spec, tuple(axis_origin), tuple(axis_dir), float(angle_deg))


def _planar_wire_at(sketch: dict, cx: float, cy: float, z: float, profile_scale: float):
    """Build a planar outer wire from a section sketch, re-centred at (cx, cy)
    and lifted to z. Reuses the pocket/loft ``_build_planar_face`` builder."""
    from pydantic import TypeAdapter
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    from phone_designer.skills.modify_pocket._sketch import SketchSpec
    from phone_designer.skills.modify_pocket._sketch_to_solid import _build_planar_face

    sk = _scale_sketch_2d(sketch, float(profile_scale))
    spec = TypeAdapter(SketchSpec).validate_python(sk)
    face = _build_planar_face(spec)
    it = TopExp_Explorer(face, TopAbs_WIRE)
    if not it.More():
        raise RuntimeError("place_generating_op_solid: section face has no wire")
    wire = TopoDS.Wire_s(it.Current())
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(float(cx), float(cy), float(z)))
    return TopoDS.Wire_s(BRepBuilderAPI_Transform(wire, t, True).Shape())


def build_loft_solid(
    section_sketches: list[dict],
    profile_scale: float = 1.0,
):
    """Loft through the recovered stacked sections (BRepOffsetAPI_ThruSections,
    same core as loft_boss_between_sketches). Each entry carries its own
    ``sketch`` + ``z_along_axis_mm`` + ``center_xy`` — sections are stacked along
    local +Z at their recorded axis station so a tapering body rebuilds true to
    its varying outline."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections

    if len(section_sketches) < 2:
        raise ValueError("place_generating_op_solid: loft needs >= 2 sections")
    loft = BRepOffsetAPI_ThruSections(True, False, 1e-6)  # IsSolid, IsRuled
    for entry in section_sketches:
        cx, cy = entry.get("center_xy") or (0.0, 0.0)
        z = float(entry.get("z_along_axis_mm") or 0.0)
        wire = _planar_wire_at(entry["sketch"], float(cx), float(cy), z, profile_scale)
        loft.AddWire(wire)
    loft.Build()
    return loft.Shape()


def build_sweep_solid(
    profile_sketch: dict,
    path_points: list[tuple[float, float, float]],
    path_type: str = "polyline",
    profile_scale: float = 1.0,
):
    """Sweep the constant (scaled) section along the path (BRepOffsetAPI_MakePipe,
    same core as swept_boss_along_curve)."""
    from pydantic import TypeAdapter
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe

    from phone_designer.skills.modify_pocket._sketch import SketchSpec
    from phone_designer.skills.modify_pocket._sketch_to_solid import _build_planar_face
    from phone_designer.skills.modify_boss.swept_boss_along_curve import (
        _build_path_wire,
        _initial_tangent,
        _transform_face_to_frame,
    )

    sk = _scale_sketch_2d(profile_sketch, float(profile_scale))
    spec = TypeAdapter(SketchSpec).validate_python(sk)
    profile_face = _build_planar_face(spec)
    pts = [tuple(float(c) for c in p) for p in path_points]
    origin = pts[0]
    tangent = _initial_tangent(pts, path_type)
    oriented = _transform_face_to_frame(profile_face, origin, tangent)
    path_wire = _build_path_wire(pts, path_type)
    pipe = BRepOffsetAPI_MakePipe(path_wire, oriented)
    pipe.Build()
    if not pipe.IsDone():
        raise RuntimeError("place_generating_op_solid: MakePipe failed")
    return pipe.Shape()


def build_generating_op_solid(
    kind: str,
    *,
    profile_sketch: dict | None = None,
    section_sketches: list[dict] | None = None,
    axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    axis_dir: tuple[float, float, float] = (0.0, 0.0, 1.0),
    angle_deg: float = 360.0,
    path_points: list | None = None,
    path_type: str = "polyline",
    profile_scale: float = 1.0,
    box_shift: tuple[float, float, float] = (0.0, 0.0, 0.0),
):
    """Build the recovered generating-op solid (world frame), then shift into
    the box frame. Shared by the @skill and the planner's in-process A/B."""
    if kind == "revolve":
        if not profile_sketch:
            raise ValueError("place_generating_op_solid: revolve needs profile_sketch")
        solid = build_revolve_solid(
            profile_sketch, axis_origin, axis_dir, angle_deg, profile_scale,
        )
    elif kind == "loft":
        if not section_sketches:
            raise ValueError("place_generating_op_solid: loft needs section_sketches")
        solid = build_loft_solid(section_sketches, profile_scale)
    elif kind == "sweep":
        if not (profile_sketch and path_points):
            raise ValueError(
                "place_generating_op_solid: sweep needs profile_sketch + path_points"
            )
        solid = build_sweep_solid(profile_sketch, path_points, path_type, profile_scale)
    else:
        raise ValueError(f"place_generating_op_solid: unknown kind {kind!r}")

    if solid is None or solid.IsNull():
        raise RuntimeError(f"place_generating_op_solid: {kind} produced a null solid")
    sx, sy, sz = (float(c) for c in box_shift)
    return _translate(solid, sx, sy, sz)


@skill(
    name="place_generating_op_solid",
    category="create",
    level="atomic",
    summary="Build a PARAMETRIC base solid from a recovered generating op: a "
            "REVOLVE of a recovered meridian profile, a LOFT through N recovered "
            "sections, or a SWEEP of a constant section along a path. Editable "
            "(profile_scale tunes it) — the rebuild-from-catalog counterpart of "
            "place_freeform_solid's re-solidified shell.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["generating_op_solid"],
    preserves=[],
    manufacturing={
        "cnc_3axis":   {"min_wall_mm": 0.5},
        "turning":     {"min_wall_mm": 0.3},
    },
    failure_modes=["fm.generating_op_build_failed"],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class PlaceGeneratingOpSolid(SkillBase):
    class Args(BaseModel):
        kind: Literal["revolve", "loft", "sweep"]
        profile_sketch: dict | None = Field(
            default=None,
            description="Recovered meridian (revolve) or constant section "
                        "(sweep) — an executable polygon/composite/bspline_closed "
                        "sketch dict from classify_generating_op.",
        )
        section_sketches: list[dict] | None = Field(
            default=None,
            description="Recovered loft stations: list of {sketch, "
                        "z_along_axis_mm, center_xy}.",
        )
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_dir: tuple[float, float, float] = (0.0, 0.0, 1.0)
        angle_deg: float = Field(default=360.0, gt=0.0, le=360.0)
        path_points: list | None = Field(
            default=None,
            description="Sweep path: list of 3D world points.",
        )
        path_type: Literal["polyline", "bspline"] = "polyline"
        profile_scale: float = Field(
            default=1.0, gt=0.0,
            description="Uniform 2D scale of the recovered profile/sections "
                        "about their origin BEFORE building — the parametric "
                        "edit hook. 1.0 is identity.",
        )
        box_shift: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="world→box translation (-cx, -cy, -zmin) so the base "
                        "lands in the box frame. (0,0,0) keeps world coords.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        solid = build_generating_op_solid(
            args.kind,
            profile_sketch=args.profile_sketch,
            section_sketches=args.section_sketches,
            axis_origin=tuple(args.axis_origin),
            axis_dir=tuple(args.axis_dir),
            angle_deg=args.angle_deg,
            path_points=args.path_points,
            path_type=args.path_type,
            profile_scale=args.profile_scale,
            box_shift=tuple(args.box_shift),
        )
        if solid is None or solid.IsNull():
            raise RuntimeError("place_generating_op_solid: produced a null solid")
        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(solid), history=history)
