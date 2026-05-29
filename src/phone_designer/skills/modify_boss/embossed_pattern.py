"""embossed_pattern — macro.

Apply a repeating raised pattern (grip dots, parallel lines, checker squares)
to a planar face. Standard for tactile grip zones on phone backs, controller
grips, watch crowns.

Args:
    face_selector: SelectorRef   — planar ±Z face
    pattern: "dots" | "lines" | "checker"
    pitch_mm: float = 1.0        — center-to-center spacing
    feature_size_mm: float = 0.5 — diameter (dots) / width (lines / checker squares)
    height_mm: float = 0.2       — raised height
    area_radius_mm: float | None = None — restrict pattern to a circular area
                                          around the face center; None = whole face

Implementation:
    Build a small extruded shape per pattern point and fuse with the body.

Post: volume_increased.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _face_bbox_xy(face) -> tuple[float, float, float, float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(face, bb)
    xmin, ymin, _zmin, xmax, ymax, _zmax = bb.Get()
    return (xmin, ymin, xmax, ymax)


def _in_area(cx: float, cy: float,
             face_cx: float, face_cy: float,
             area_radius: float | None,
             xmin: float, ymin: float, xmax: float, ymax: float) -> bool:
    if area_radius is not None:
        return (cx - face_cx) ** 2 + (cy - face_cy) ** 2 <= area_radius * area_radius
    return xmin <= cx <= xmax and ymin <= cy <= ymax


def _generate_centers(
    pattern: str,
    face_cx: float, face_cy: float,
    xmin: float, ymin: float, xmax: float, ymax: float,
    pitch: float,
    area_radius: float | None,
) -> list[tuple[float, float, int]]:
    """Return list of (cx, cy, kind_index) for the pattern centers.

    `kind_index` is used by "checker" to skip half the squares: 0 = place,
    1 = skip. For other patterns it's always 0.
    """
    out: list[tuple[float, float, int]] = []
    width = xmax - xmin
    height = ymax - ymin
    n_cols = int(width / pitch) + 2
    n_rows = int(height / pitch) + 2
    cx0 = (xmin + xmax) / 2.0
    cy0 = (ymin + ymax) / 2.0

    if pattern == "dots":
        for iy in range(-n_rows, n_rows + 1):
            for ix in range(-n_cols, n_cols + 1):
                cx = cx0 + ix * pitch
                cy = cy0 + iy * pitch
                if _in_area(cx, cy, face_cx, face_cy, area_radius,
                            xmin, ymin, xmax, ymax):
                    out.append((cx, cy, 0))
        return out

    if pattern == "checker":
        # Only place a square when (ix + iy) is even
        for iy in range(-n_rows, n_rows + 1):
            for ix in range(-n_cols, n_cols + 1):
                if (ix + iy) % 2:
                    continue
                cx = cx0 + ix * pitch
                cy = cy0 + iy * pitch
                if _in_area(cx, cy, face_cx, face_cy, area_radius,
                            xmin, ymin, xmax, ymax):
                    out.append((cx, cy, 0))
        return out

    if pattern == "lines":
        # One line per row; we generate (cx0, cy) markers and the caller
        # treats them as line midpoints.
        for iy in range(-n_rows, n_rows + 1):
            cy = cy0 + iy * pitch
            if not (ymin <= cy <= ymax):
                continue
            cx = cx0
            if _in_area(cx, cy, face_cx, face_cy, area_radius,
                        xmin, ymin, xmax, ymax):
                out.append((cx, cy, 0))
        return out

    return out


def _build_dot_face_xy(cx: float, cy: float, diameter: float):
    """Circle (face) of `diameter` at (cx, cy) in z=0 plane."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    axis = gp_Ax2(gp_Pnt(cx, cy, 0.0), gp_Dir(0, 0, 1))
    circ = gp_Circ(axis, diameter / 2.0)
    me = BRepBuilderAPI_MakeEdge(circ)
    if not me.IsDone():
        raise RuntimeError("dot edge failed")
    mw = BRepBuilderAPI_MakeWire()
    mw.Add(me.Edge())
    if not mw.IsDone():
        raise RuntimeError("dot wire failed")
    mf = BRepBuilderAPI_MakeFace(mw.Wire(), True)
    if not mf.IsDone():
        raise RuntimeError("dot face failed")
    return mf.Face()


def _build_square_face_xy(cx: float, cy: float, side: float):
    """Square face (`side` × `side`) centered at (cx, cy) in z=0 plane."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
    )
    from OCP.gp import gp_Pnt

    h = side / 2.0
    poly = BRepBuilderAPI_MakePolygon()
    poly.Add(gp_Pnt(cx - h, cy - h, 0.0))
    poly.Add(gp_Pnt(cx + h, cy - h, 0.0))
    poly.Add(gp_Pnt(cx + h, cy + h, 0.0))
    poly.Add(gp_Pnt(cx - h, cy + h, 0.0))
    poly.Close()
    if not poly.IsDone():
        raise RuntimeError("square polygon failed")
    mf = BRepBuilderAPI_MakeFace(poly.Wire(), True)
    if not mf.IsDone():
        raise RuntimeError("square face failed")
    return mf.Face()


def _build_line_face_xy(cy: float, xmin: float, xmax: float, width: float):
    """Thin rectangle face: x ∈ [xmin, xmax], y ∈ [cy - width/2, cy + width/2]."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
    )
    from OCP.gp import gp_Pnt

    h = width / 2.0
    poly = BRepBuilderAPI_MakePolygon()
    poly.Add(gp_Pnt(xmin, cy - h, 0.0))
    poly.Add(gp_Pnt(xmax, cy - h, 0.0))
    poly.Add(gp_Pnt(xmax, cy + h, 0.0))
    poly.Add(gp_Pnt(xmin, cy + h, 0.0))
    poly.Close()
    if not poly.IsDone():
        raise RuntimeError("line polygon failed")
    mf = BRepBuilderAPI_MakeFace(poly.Wire(), True)
    if not mf.IsDone():
        raise RuntimeError("line face failed")
    return mf.Face()


@skill(
    name="embossed_pattern",
    category="modify/boss",
    level="macro",
    summary="Repeating raised pattern (grip dots, parallel lines, checker "
            "squares) on a planar face. Standard for tactile grip zones on "
            "phone backs, controller grips, watch crowns.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":      HistoryRule.MODIFIED_INHERIT,
        "embossed_features": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.height_positive",
    ],
    produces_features=["embossed_pattern"],
    preserves=["outer_envelope"],
    manufacturing={
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
        "cnc_3axis":         {"min_wall_mm": 0.5},
    },
    failure_modes=[
        "fm.feature_size_exceeds_pitch",
        "fm.pattern_outside_face",
    ],
    cost_hint=0.35,
    expansion=["extrude_plateau"] * 12,
    post_conditions=[PostCondition(kind="volume_increased")],
)
class EmbossedPattern(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        pattern: Literal["dots", "lines", "checker"] = "dots"
        pitch_mm: float = Field(default=1.0, gt=0.0, le=50.0)
        feature_size_mm: float = Field(default=0.5, gt=0.0, le=50.0)
        height_mm: float = Field(default=0.2, gt=0.0, le=20.0)
        area_radius_mm: float | None = Field(default=None, gt=0.0, le=500.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _extrude_face_along_z,
            _face_plane_normal,
            _translate_shape,
        )

        if args.feature_size_mm >= args.pitch_mm:
            raise ValueError(
                f"embossed_pattern: feature_size_mm ({args.feature_size_mm}) "
                f">= pitch_mm ({args.pitch_mm}) — features would overlap"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"embossed_pattern: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        center, normal = _face_plane_normal(target_face)
        nz = normal[2]
        if abs(nz) <= 0.95:
            raise NotImplementedError(
                "embossed_pattern: non-Z face not supported (planar ±Z only)"
            )

        xmin, ymin, xmax, ymax = _face_bbox_xy(target_face)
        centers = _generate_centers(
            args.pattern, center[0], center[1],
            xmin, ymin, xmax, ymax,
            args.pitch_mm,
            args.area_radius_mm,
        )
        if not centers:
            raise RuntimeError(
                f"embossed_pattern: 0 centers generated "
                f"(bbox {xmin:.2f},{ymin:.2f}..{xmax:.2f},{ymax:.2f}, "
                f"pitch={args.pitch_mm})"
            )

        # Outward extrusion: along +Z if face normal is +Z, else -Z.
        direction_sign = +1 if nz > 0 else -1
        new_shape = shape
        fuses_done = 0
        for (cx, cy, _kind) in centers:
            try:
                if args.pattern == "dots":
                    f_face = _build_dot_face_xy(cx, cy, args.feature_size_mm)
                elif args.pattern == "checker":
                    f_face = _build_square_face_xy(cx, cy, args.feature_size_mm)
                else:   # lines
                    # Span line across the bbox limits clipped to area_radius
                    if args.area_radius_mm is not None:
                        dy = cy - center[1]
                        rem2 = args.area_radius_mm * args.area_radius_mm - dy * dy
                        if rem2 < 0:
                            continue
                        half = math.sqrt(rem2)
                        lx_min = center[0] - half
                        lx_max = center[0] + half
                    else:
                        lx_min, lx_max = xmin, xmax
                    if lx_max - lx_min < 1e-6:
                        continue
                    f_face = _build_line_face_xy(
                        cy, lx_min, lx_max, args.feature_size_mm,
                    )
                prism = _extrude_face_along_z(
                    f_face, args.height_mm, direction_sign=direction_sign,
                )
                prism_w = _translate_shape(prism, 0.0, 0.0, center[2])
                fuse = BRepAlgoAPI_Fuse(new_shape, prism_w)
                fuse.Build()
                if fuse.IsDone():
                    new_shape = fuse.Shape()
                    fuses_done += 1
            except Exception:
                continue

        if fuses_done == 0:
            raise RuntimeError(
                f"embossed_pattern: 0 successful fuses from {len(centers)} centers"
            )

        history = EntityHistoryMap(
            rules={
                "target_face":      HistoryRule.MODIFIED_INHERIT,
                "embossed_features": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
