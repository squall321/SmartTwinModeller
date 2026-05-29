"""honeycomb_pattern — macro.

Carve a hexagonal honeycomb cutout pattern into a planar face. Used for
lightweighting (waffle-back grilles), aesthetic vent patterns, or biomimetic
acoustic plates.

Args:
    face_selector: SelectorRef  — must be a planar ±Z face
    cell_size_mm: float > 0     — hexagon flat-to-flat distance (across flats)
    wall_thickness_mm: float > 0 — wall between adjacent hex cells
    depth_mm: float > 0         — depth of each hex pocket
    area_radius_mm: float | None = None — restrict cells to a circular area
                                          around the face center; None = whole
                                          face bounding box

Geometry:
    Hexagons are arranged in a row-offset hex grid with `pitch = cell_size +
    wall_thickness` (center-to-center distance between neighbors). The grid is
    tiled across the face's XY bounding box (or the disc of `area_radius_mm`).

    Each cell is a regular hexagon with flat-to-flat = cell_size_mm,
    extruded `depth_mm` into the body and subtracted.

Post: volume_decreased.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _face_bbox_xy(face) -> tuple[float, float, float, float]:
    """Return (xmin, ymin, xmax, ymax) of the face's XY bounding box."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(face, bb)
    xmin, ymin, _zmin, xmax, ymax, _zmax = bb.Get()
    return (xmin, ymin, xmax, ymax)


def _hex_vertices(cx: float, cy: float, flat_to_flat: float) -> list[tuple[float, float]]:
    """Regular hexagon with flat-to-flat = flat_to_flat, "pointy-up" orientation.

    A pointy-top hex has flats on east/west; flat-to-flat is the horizontal
    width = 2 * (corner_radius * cos(30°)) = corner_radius * sqrt(3).
    """
    r = flat_to_flat / math.sqrt(3.0)  # corner radius
    verts = []
    for k in range(6):
        # pointy-top: first vertex at top (theta=90°)
        theta = math.pi / 2.0 + k * (math.pi / 3.0)
        verts.append((cx + r * math.cos(theta), cy + r * math.sin(theta)))
    return verts


def _hex_centers(
    xmin: float, ymin: float, xmax: float, ymax: float,
    flat_to_flat: float, wall: float,
    area_center: tuple[float, float] | None,
    area_radius: float | None,
) -> list[tuple[float, float]]:
    """Generate hex pack centers covering the (xmin..xmax, ymin..ymax) rect.

    Pointy-top hexagons:
        horizontal spacing between centers (same row) = flat_to_flat + wall
        vertical spacing between rows = corner_radius * 1.5 = (ftf/√3)*1.5
            ≈ 0.866 * ftf  (when wall=0)
        Add `wall` to each spacing.
    """
    ftf = flat_to_flat
    r = ftf / math.sqrt(3.0)
    dx = ftf + wall                       # horizontal center spacing
    dy = 1.5 * r + wall * (math.sqrt(3.0) / 2.0)
    centers: list[tuple[float, float]] = []
    cx0 = (xmin + xmax) / 2.0
    cy0 = (ymin + ymax) / 2.0
    # Determine how many rows/cols to cover the rect.
    width = xmax - xmin
    height = ymax - ymin
    n_cols = int(width / dx) + 3
    n_rows = int(height / dy) + 3
    for iy in range(-n_rows, n_rows + 1):
        cy = cy0 + iy * dy
        x_offset = (dx / 2.0) if (iy % 2) else 0.0
        for ix in range(-n_cols, n_cols + 1):
            cx = cx0 + ix * dx + x_offset
            if area_radius is not None and area_center is not None:
                acx, acy = area_center
                if (cx - acx) ** 2 + (cy - acy) ** 2 > area_radius * area_radius:
                    continue
            else:
                if not (xmin <= cx <= xmax and ymin <= cy <= ymax):
                    continue
            centers.append((cx, cy))
    return centers


def _build_hex_face_xy(cx: float, cy: float, flat_to_flat: float):
    """Build a planar hex face in the XY plane (z=0)."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
    )
    from OCP.gp import gp_Pnt

    verts = _hex_vertices(cx, cy, flat_to_flat)
    poly = BRepBuilderAPI_MakePolygon()
    for vx, vy in verts:
        poly.Add(gp_Pnt(vx, vy, 0.0))
    poly.Close()
    if not poly.IsDone():
        raise RuntimeError("hex polygon failed")
    wire = poly.Wire()
    mf = BRepBuilderAPI_MakeFace(wire, True)
    if not mf.IsDone():
        raise RuntimeError("hex face failed")
    return mf.Face()


@skill(
    name="honeycomb_pattern",
    category="modify/pocket",
    level="macro",
    summary="Carve a hexagonal honeycomb cutout pattern into a planar face. "
            "Used for lightweighting (waffle-back grilles), aesthetic vent "
            "patterns, biomimetic acoustic plates.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":  HistoryRule.MODIFIED_INHERIT,
        "honeycomb_cells": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["honeycomb"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.honeycomb_too_dense",
        "fm.wall_too_thin",
    ],
    cost_hint=0.4,
    expansion=["extrude_pocket"] * 8,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class HoneycombPattern(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        cell_size_mm: float = Field(gt=0.0, le=100.0)
        wall_thickness_mm: float = Field(gt=0.0, le=20.0)
        depth_mm: float = Field(gt=0.0, le=200.0)
        area_radius_mm: float | None = Field(default=None, gt=0.0, le=500.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _extrude_face_along_z,
            _face_plane_normal,
            _translate_shape,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"honeycomb_pattern: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        center, normal = _face_plane_normal(target_face)
        nz = normal[2]
        if abs(nz) <= 0.95:
            raise NotImplementedError(
                "honeycomb_pattern: non-Z face not supported (planar ±Z only)"
            )

        # Pattern coverage region: face bbox or disc around face center.
        xmin, ymin, xmax, ymax = _face_bbox_xy(target_face)
        area_center = (center[0], center[1])
        centers = _hex_centers(
            xmin, ymin, xmax, ymax,
            args.cell_size_mm, args.wall_thickness_mm,
            area_center, args.area_radius_mm,
        )
        if not centers:
            raise RuntimeError(
                f"honeycomb_pattern: 0 cells generated "
                f"(face bbox {xmin:.2f},{ymin:.2f}..{xmax:.2f},{ymax:.2f}, "
                f"cell={args.cell_size_mm}, wall={args.wall_thickness_mm})"
            )

        # For each cell: build hex face in local XY, extrude into the body,
        # subtract.
        direction_sign = -1 if nz > 0 else +1
        new_shape = shape
        cuts_done = 0
        for (cx, cy) in centers:
            try:
                hex_face = _build_hex_face_xy(cx, cy, args.cell_size_mm)
                prism = _extrude_face_along_z(
                    hex_face, args.depth_mm, direction_sign=direction_sign,
                )
                # Position prism so its base is at the target face Z.
                prism_w = _translate_shape(prism, 0.0, 0.0, center[2])
                cut = BRepAlgoAPI_Cut(new_shape, prism_w)
                cut.Build()
                if cut.IsDone():
                    new_shape = cut.Shape()
                    cuts_done += 1
            except Exception:
                continue

        if cuts_done == 0:
            raise RuntimeError(
                f"honeycomb_pattern: 0 successful cuts from {len(centers)} cells"
            )

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "honeycomb_cells": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
