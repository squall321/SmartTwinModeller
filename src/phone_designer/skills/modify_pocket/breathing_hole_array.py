"""breathing_hole_array — atomic. Acoustic-mesh / vent hole pattern.

Drills a hex-packed array of small through-holes inside a region defined by a
2D `region_sketch` on a planar face. Used for speaker meshes, mic vents, and
breathing membranes where many small through-holes are needed inside a
specific outline (often a rounded rectangle or ellipse).

The region sketch is built as a planar face (via the existing `_build_planar_face`
helper), then the hex grid is generated from its bounding box and clipped to
points actually inside the region face (via BRepClass_FaceClassifier). For each
in-region point a cylindrical tool is cut from the body.

v1 supports planar ±Z target faces.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._resolvers import (
    _face_center,
    _face_normal_at_center,
    resolve_faces,
)
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec


@skill(
    name="breathing_hole_array",
    category="modify/pocket",
    level="atomic",
    summary="Hex-packed array of small through-holes clipped to a region sketch on a "
            "planar face. Used for speaker meshes, mic vents, breathing membranes. "
            "v1 supports planar +Z/-Z faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":        HistoryRule.MODIFIED_INHERIT,
        "breathing_hole_set": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.hole_diameter_lt_spacing",
    ],
    produces_features=["breathing_hole_array", "acoustic_mesh_pattern"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4,
                              "extras": {"min_drill_mm": 0.5}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_small_holes": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.hole_diameter_geq_spacing",
        "fm.region_outside_face",
        "fm.no_points_inside_region",
    ],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class BreathingHoleArray(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        region_sketch: SketchSpec = Field(
            description="2D region (in face-local coords; center at face center) "
                        "inside which to place breathing holes."
        )
        hole_diameter_mm: float = Field(gt=0, le=10)
        spacing_mm: float = Field(gt=0, le=20,
                                   description="hex packing center-to-center spacing")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRep import BRep_Tool
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepClass import BRepClass_FaceClassifier
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Pnt2d
        from OCP.TopAbs import TopAbs_OUT

        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _build_planar_face,
        )

        if args.hole_diameter_mm >= args.spacing_mm:
            raise ValueError(
                f"breathing_hole_array: hole_diameter_mm ({args.hole_diameter_mm}) "
                f">= spacing_mm ({args.spacing_mm}) — holes would overlap"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"breathing_hole_array: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]

        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "breathing_hole_array v1: planar +Z/-Z target faces only"
            )

        # Build region face in local XY (z=0) — same coord system as the
        # face-local args.
        region_face_local = _build_planar_face(args.region_sketch)

        # Get bbox of region in its local XY plane.
        bbox = Bnd_Box()
        BRepBndLib.Add_s(region_face_local, bbox)
        xmin, ymin, _, xmax, ymax, _ = bbox.Get()

        # Build hex-packing of candidate points (local xy).
        row_step = args.spacing_mm * math.sqrt(3) / 2.0
        candidates: list[tuple[float, float]] = []
        nx = int((xmax - xmin) / args.spacing_mm) + 2
        ny = int((ymax - ymin) / row_step) + 2
        for iy in range(ny):
            y = ymin + iy * row_step
            offset = (args.spacing_mm / 2.0) if (iy % 2 == 1) else 0.0
            for ix in range(nx):
                x = xmin + offset + ix * args.spacing_mm
                if xmin <= x <= xmax and ymin <= y <= ymax:
                    candidates.append((x, y))

        # Get the (u,v) surface of the region face. It's a plane z=0; (u,v)=(x,y).
        # Use BRepClass_FaceClassifier to test inside/outside.
        classifier = BRepClass_FaceClassifier()
        # Use a small tolerance (0.5 * hole radius) so points right at the
        # boundary aren't dropped.
        tol = args.hole_diameter_mm * 0.25
        inside_local: list[tuple[float, float]] = []
        for (x, y) in candidates:
            classifier.Perform(region_face_local, gp_Pnt2d(x, y), tol)
            if classifier.State() != TopAbs_OUT:
                inside_local.append((x, y))

        if not inside_local:
            raise RuntimeError(
                f"breathing_hole_array: 0 hex-points fall inside region — "
                f"spacing={args.spacing_mm}, region bbox=({xmin:.2f},{ymin:.2f})-"
                f"({xmax:.2f},{ymax:.2f})"
            )

        # Convert local points to world points on the face plane and build the
        # union of cylindrical tools, then cut.
        z_sign = 1.0 if normal[2] > 0 else -1.0
        # Make cylinders long enough to traverse any reasonable body — 200 mm —
        # but start them outside the face so we cleanly clip the entry edge.
        overshoot = 1.0
        cyl_height = 200.0 + overshoot
        cyl_r = args.hole_diameter_mm / 2.0

        new_shape = shape
        for (lx, ly) in inside_local:
            wx = center[0] + lx
            wy = center[1] + ly
            # Cylinder grows along -normal direction (into the body).
            # Base point: a hair above the face so the cut produces a clean
            # entry edge.
            base_z = center[2] + z_sign * overshoot
            direction = gp_Dir(0.0, 0.0, -z_sign)
            ax = gp_Ax2(gp_Pnt(wx, wy, base_z), direction)
            mk = BRepPrimAPI_MakeCylinder(ax, cyl_r, cyl_height)
            mk.Build()
            if not mk.IsDone():
                raise RuntimeError(
                    f"breathing_hole_array: cylinder build failed at ({wx},{wy})"
                )
            cut = BRepAlgoAPI_Cut(new_shape, mk.Shape())
            cut.Build()
            if not cut.IsDone():
                raise RuntimeError(
                    f"breathing_hole_array: boolean cut failed at ({wx},{wy})"
                )
            new_shape = cut.Shape()

        # silence unused-import lint (BRep_Tool reserved for future v2 surface use)
        _ = BRep_Tool

        history = EntityHistoryMap(
            rules={
                "target_face":        HistoryRule.MODIFIED_INHERIT,
                "breathing_hole_set": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
