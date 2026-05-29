"""extrude_pocket_to_curved_floor — atomic.

Carve a pocket whose floor is a curved (NURBS) surface, not a flat plane.

Approach:
    1. Resolve the target face (planar ±Z; non-Z faces raise NotImplementedError).
    2. Build the sketch wire → face using the standard `_build_planar_face`.
    3. Place the sketch face at the target face center.
    4. Extrude the sketch DEEPLY (200 mm) along the face inward normal, giving
       a `prism_tool` that is taller than the pocket should be.
    5. Build a curved floor surface by fitting a NURBS surface through the
       user-supplied `floor_surface_grid` (world coordinates).
    6. Thicken the floor surface OUTWARD (away from the body, i.e., in the
       opposite direction of the prism's inward extrusion) by 100 mm. This
       yields a `floor_block` slab whose top side is the curved floor.
    7. `prism_tool − floor_block` → a pocket-shaped solid whose far end is
       cropped at the curved floor.
    8. `body − that` → the body with a curved-floor pocket.

floor_surface_grid is in **world coordinates**. It should be positioned so
it crosses the prism somewhere in the pocket region. If it lies entirely
above or below the prism the cut will be a no-op (post-condition fires).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec


# Deep prism length (mm) — large enough to cover any reasonable phone wall.
_PRISM_DEPTH_MM = 200.0
# Floor thickening length (mm) — large enough to fully cap the prism.
_FLOOR_THICKEN_MM = 100.0


@skill(
    name="extrude_pocket_to_curved_floor",
    category="modify/pocket",
    level="atomic",
    summary="Carve a pocket whose floor is a curved (NURBS) surface instead "
            "of a flat plane. Floor surface is fit through a user-supplied "
            "rectangular grid of 3D points in WORLD coordinates.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":      HistoryRule.MODIFIED_INHERIT,
        "pocket_walls":     HistoryRule.GENERATED_NEW,
        "curved_floor":     HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
    ],
    produces_features=["pocket", "curved_floor"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_5axis": {"min_wall_mm": 0.5, "min_fillet_r_mm": 0.3,
                       "extras": {"freeform_floor": True}},
    },
    failure_modes=[
        "fm.floor_grid_outside_prism",
        "fm.bspline_fit_failed",
        "fm.cut_failed",
    ],
    cost_hint=0.45,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class ExtrudePocketToCurvedFloor(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        sketch: SketchSpec
        floor_surface_grid: list[list[tuple[float, float, float]]]
        degree_u: int = Field(default=3, ge=3, le=8)
        degree_v: int = Field(default=3, ge=3, le=8)
        floor_blend_r_mm: float = Field(default=0.0, ge=0.0, le=10.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.create.bspline_surface import (
            _face_from_surface,
            _fit_bspline_surface,
            _thicken_face,
        )
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _build_planar_face,
            _extrude_face_along_z,
            _face_plane_normal,
            _translate_shape,
        )
        from phone_designer.skills.modify_pocket.extrude_pocket_blended import (
            _fillet_edges_near_z,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"extrude_pocket_to_curved_floor: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        center, normal = _face_plane_normal(target_face)
        nz = normal[2]
        if abs(nz) <= 0.95:
            raise NotImplementedError(
                "extrude_pocket_to_curved_floor: non-Z face not supported "
                "(face-local coord transform needed)"
            )

        # 1. Build planar sketch face in local XY.
        planar_face = _build_planar_face(args.sketch)

        # 2. Deep prism. Direction: opposite to face normal (pocket grows
        #    into the body). For top face (nz>0), prism extrudes -Z.
        if nz > 0:
            prism = _extrude_face_along_z(
                planar_face, _PRISM_DEPTH_MM, direction_sign=-1,
            )
        else:
            prism = _extrude_face_along_z(
                planar_face, _PRISM_DEPTH_MM, direction_sign=+1,
            )
        prism = _translate_shape(prism, center[0], center[1], center[2])

        # 3. Build curved floor surface.
        try:
            surface = _fit_bspline_surface(
                args.floor_surface_grid,
                args.degree_u, args.degree_v, tol3d=1e-3,
            )
        except Exception as e:
            raise RuntimeError(
                f"extrude_pocket_to_curved_floor: floor surface fit failed: {e}"
            ) from e
        floor_face = _face_from_surface(surface, tol=1e-6)

        # 4. Thicken floor surface OUTWARD (away from the body interior).
        #    For top face (nz>0) the pocket extrudes -Z, so the body interior
        #    is below the floor surface — we want the floor block on the -Z
        #    side. BRepOffset_MakeOffset with negative thickness offsets in
        #    the opposite direction of the face's normal. We try +thickness
        #    first; if the resulting cut leaves the pocket too short (no
        #    volume change), the caller can negate the floor grid.
        #
        #    Simpler: thicken by a signed value chosen by `nz`.
        thicken_signed = (
            -_FLOOR_THICKEN_MM if nz > 0 else +_FLOOR_THICKEN_MM
        )
        try:
            floor_block = _thicken_face(
                floor_face, thicken_signed, tol=1e-3,
            )
        except Exception as e:
            # Fallback: try the opposite sign in case the surface normal
            # orientation is the opposite of what we assumed.
            try:
                floor_block = _thicken_face(
                    floor_face, -thicken_signed, tol=1e-3,
                )
            except Exception:
                raise RuntimeError(
                    f"extrude_pocket_to_curved_floor: failed to thicken "
                    f"floor surface in either direction: {e}"
                ) from e

        # 5. prism_minus_floor: the prism with everything past the curved
        #    floor sliced off — leaves a finite-depth pocket tool.
        cut1 = BRepAlgoAPI_Cut(prism, floor_block)
        cut1.Build()
        if not cut1.IsDone():
            raise RuntimeError(
                "extrude_pocket_to_curved_floor: prism − floor_block cut failed"
            )
        pocket_tool = cut1.Shape()

        # 6. Final cut: body − pocket_tool.
        cut2 = BRepAlgoAPI_Cut(shape, pocket_tool)
        cut2.Build()
        if not cut2.IsDone():
            raise RuntimeError(
                "extrude_pocket_to_curved_floor: body − pocket_tool cut failed"
            )
        new_shape = cut2.Shape()

        # 7. Optional fillet near floor.
        if args.floor_blend_r_mm > 0:
            # Find the floor grid average Z and fillet edges near that plane.
            # NOTE: this is a coarse heuristic — for true curved-floor blends
            # we'd need to identify the floor face explicitly.
            zs = [p[2] for row in args.floor_surface_grid for p in row]
            avg_z = sum(zs) / len(zs)
            new_shape, _ = _fillet_edges_near_z(
                new_shape, avg_z, args.floor_blend_r_mm,
            )

        history = EntityHistoryMap(
            rules={
                "target_face":  HistoryRule.MODIFIED_INHERIT,
                "pocket_walls": HistoryRule.GENERATED_NEW,
                "curved_floor": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
