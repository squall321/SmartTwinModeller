"""extrude_profile_world — atomic create skill (phase-1, 2026-06-14).

PILLAR FREEFORM base builder. Extrude a recovered outer-silhouette profile (the
executable sketch ``extract_outer_silhouette_profile`` emits) into the base
solid for box-mode reconstruction — the TRUE swept outline of a prismatic part
instead of a bbox-sized rectangular block.

Geometry
--------
The profile sketch's 2D coords live in the section plane's (u, v) in-plane
world frame, centred on ``section_origin_world``. This skill:

1. builds the planar wire/face from the sketch in canonical local XY (z=0,
   +Z normal) — reusing ``_sketch_to_solid._build_planar_face`` so the exact
   same polygon / bspline_closed / composite builders run as for pockets;
2. extrudes it along local +Z by ``height_mm`` (``BRepPrimAPI_MakePrism``);
3. rigid-transforms the prism into WORLD coords so that
     * local +Z (the extrude direction)   → the world sweep ``axis``,
     * local +X                            → the section ``u`` axis,
     * local origin                        → ``section_origin_world`` shifted
       back by ``height_mm/2`` along the axis (the section is the MID plane, so
       the prism starts half a height before it and ends half after);
4. translates by ``box_shift`` so the result lands in the SAME box frame the
   placeholder ``box`` base would have occupied (XY-centred, Z bottom at 0) —
   every downstream pocket / hole / boss step is emitted in that frame.

``box_shift`` defaults to (0, 0, 0); the planner passes the world→box shift
``(-cx, -cy, -zmin)`` so the profile base coincides with the box base it
replaces. ``axis`` is one of "X" / "Y" / "Z" (the world sweep direction).

post_conditions = [body_present]. Same create-skill contract as ``box``.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec

_AXIS_DIR: dict[str, tuple[float, float, float]] = {
    "X": (1.0, 0.0, 0.0),
    "Y": (0.0, 1.0, 0.0),
    "Z": (0.0, 0.0, 1.0),
}


def build_profile_solid(
    sketch: Any,
    height_mm: float,
    axis: str,
    u_axis: tuple[float, float, float],
    section_origin_world: tuple[float, float, float],
    box_shift: tuple[float, float, float] = (0.0, 0.0, 0.0),
):
    """Return the world/box-frame TopoDS_Shape for the extruded profile.

    Shared with the planner's revert-guard (it builds the candidate base
    solid in-process to A/B its hausdorff against the box base before the
    profile branch is committed to a plan step).
    """
    import math

    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    from phone_designer.skills.modify_pocket._sketch_to_solid import (
        _build_planar_face,
        _extrude_face_along_z,
    )

    # Coerce a raw sketch dict (planner / revert-guard path) into the proper
    # SketchSpec model so the discriminated-union wire builders match it.
    if isinstance(sketch, dict):
        from pydantic import TypeAdapter

        sketch = TypeAdapter(SketchSpec).validate_python(sketch)

    axis_key = str(axis).upper()
    if axis_key not in _AXIS_DIR:
        raise ValueError(f"extrude_profile_world: axis must be X/Y/Z, got {axis!r}")
    n = _AXIS_DIR[axis_key]

    h = float(height_mm)
    if h <= 0.0:
        raise ValueError("extrude_profile_world: height_mm must be > 0")

    # 1+2. planar face in local XY, extruded along local +Z by height.
    planar_face = _build_planar_face(sketch)
    prism = _extrude_face_along_z(planar_face, h, direction_sign=1)

    # 3. local frame → world. The section plane is the MID slice, so shift the
    #    extrude start back by h/2 along the axis from section_origin_world.
    ox, oy, oz = (float(c) for c in section_origin_world)
    start = (ox - n[0] * 0.5 * h, oy - n[1] * 0.5 * h, oz - n[2] * 0.5 * h)
    ux, uy, uz = (float(c) for c in u_axis)
    umag = math.sqrt(ux * ux + uy * uy + uz * uz)
    if umag < 1e-9:
        raise ValueError("extrude_profile_world: degenerate u_axis")
    ux, uy, uz = ux / umag, uy / umag, uz / umag

    local_ax3 = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0))
    world_ax3 = gp_Ax3(
        gp_Pnt(start[0], start[1], start[2]),
        gp_Dir(n[0], n[1], n[2]),
        gp_Dir(ux, uy, uz),
    )
    trsf = gp_Trsf()
    trsf.SetTransformation(world_ax3, local_ax3)
    placed = BRepBuilderAPI_Transform(prism, trsf, True).Shape()

    # 4. world → box-frame shift so the base coincides with the box it replaces.
    sx, sy, sz = (float(c) for c in box_shift)
    if sx or sy or sz:
        from OCP.gp import gp_Vec

        t2 = gp_Trsf()
        t2.SetTranslation(gp_Vec(sx, sy, sz))
        placed = BRepBuilderAPI_Transform(placed, t2, True).Shape()

    return placed


@skill(
    name="extrude_profile_world",
    category="create",
    level="atomic",
    summary="Extrude a recovered outer-silhouette profile (sketch + height) "
            "along a world axis into the base solid for box-mode "
            "reconstruction — the true swept outline of a prismatic part "
            "instead of a bbox rectangle.",
    selector_kinds=[],
    history_rules={"output_faces": HistoryRule.GENERATED_NEW},
    produces_features=["profile_solid"],
    preserves=[],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.profile_extrude_failed"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class ExtrudeProfileWorld(SkillBase):
    class Args(BaseModel):
        sketch: SketchSpec
        height_mm: float = Field(gt=0, le=100000)
        axis: Literal["X", "Y", "Z"] = Field(
            description="World sweep direction the profile is extruded along.",
        )
        u_axis: tuple[float, float, float] = Field(
            description="Section in-plane +X reference axis (world). Matches "
                        "the (u) returned by extract_outer_silhouette_profile.",
        )
        section_origin_world: tuple[float, float, float] = Field(
            description="World origin of the recovered MID section plane.",
        )
        box_shift: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="world→box translation (-cx, -cy, -zmin) so the base "
                        "lands in the box frame. (0,0,0) keeps world coords.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        solid = build_profile_solid(
            args.sketch,
            args.height_mm,
            args.axis,
            args.u_axis,
            args.section_origin_world,
            args.box_shift,
        )
        if solid is None or solid.IsNull():
            raise RuntimeError("extrude_profile_world: produced a null solid")
        history = EntityHistoryMap(
            rules={"output_faces": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(solid), history=history)
