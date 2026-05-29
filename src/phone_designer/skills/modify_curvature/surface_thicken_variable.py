"""surface_thicken_variable — create skill. Thicken a fitted NURBS surface
with a per-point thickness, producing a closed solid.

Unlike `bspline_surface(thicken_mm=...)` which offsets the whole surface by
the same scalar, this skill builds the offset surface by displacing each
control point along the surface normal by an individually-specified amount.
Useful for variable-wall NURBS shells: e.g., a phone back-cover whose
center is 1.5 mm thick but tapers to 0.8 mm at the edges.

Args:
    point_grid:    list[list[(x,y,z)]] — rows × cols control grid (≥3×3)
    thickness_map: list[list[float]]   — same shape; positive thickness at
                                         each control point (mm)
    degree_u:      int = 3
    degree_v:      int = 3

Behavior:
    1. Fit a NURBS surface through `point_grid` (reused helper from
       `bspline_surface`).
    2. For each control point compute the surface normal at the corresponding
       UV (using the grid's row/col index → parametric domain mapping).
    3. Offset each control point along its normal by the per-point thickness.
    4. Fit a SECOND NURBS surface through the offset points.
    5. Loft a solid between the two surfaces (ThruSections=True/IsSolid).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _validate_grids(
    point_grid: list[list[tuple[float, float, float]]],
    thickness_map: list[list[float]],
) -> tuple[int, int]:
    if not point_grid:
        raise ValueError("point_grid is empty")
    rows = len(point_grid)
    cols = len(point_grid[0])
    if rows < 3:
        raise ValueError(f"point_grid needs ≥3 rows (got {rows})")
    if cols < 3:
        raise ValueError(f"point_grid needs ≥3 cols (got {cols})")
    for i, row in enumerate(point_grid):
        if len(row) != cols:
            raise ValueError(
                f"point_grid row {i} has {len(row)} cols; expected {cols}"
            )
        for j, p in enumerate(row):
            if len(p) != 3:
                raise ValueError(
                    f"point_grid[{i}][{j}] expected (x,y,z) — got {p!r}"
                )
    if len(thickness_map) != rows:
        raise ValueError(
            f"thickness_map has {len(thickness_map)} rows; expected {rows}"
        )
    for i, row in enumerate(thickness_map):
        if len(row) != cols:
            raise ValueError(
                f"thickness_map row {i} has {len(row)} cols; expected {cols}"
            )
        for j, t in enumerate(row):
            if not (t > 0):
                raise ValueError(
                    f"thickness_map[{i}][{j}] must be > 0 (got {t})"
                )
    return rows, cols


def _normal_at_uv(surface, u: float, v: float) -> tuple[float, float, float]:
    """Unit surface normal at parameter (u, v) via cross product of partials."""
    from OCP.gp import gp_Pnt, gp_Vec

    pnt = gp_Pnt()
    d1u = gp_Vec()
    d1v = gp_Vec()
    surface.D1(u, v, pnt, d1u, d1v)
    n = d1u.Crossed(d1v)
    L = n.Magnitude()
    if L < 1e-12:
        # Degenerate — return +Z as a sane default. Tests cover non-degenerate
        # grids so this branch is just defensive.
        return (0.0, 0.0, 1.0)
    return (n.X() / L, n.Y() / L, n.Z() / L)


def _uniform_clamped_knots(n_poles: int, degree: int) -> tuple[list[float], list[int]]:
    """Build a clamped uniform knot vector for `n_poles` control points and
    given `degree`. Knot range is [0, 1]. Returns (unique_knots, multiplicities).
    """
    n_inner = n_poles - degree - 1
    knots: list[float] = [0.0]
    mults: list[int] = [degree + 1]
    if n_inner > 0:
        for k in range(1, n_inner + 1):
            knots.append(k / (n_inner + 1))
            mults.append(1)
    knots.append(1.0)
    mults.append(degree + 1)
    return knots, mults


def _surface_from_poles(
    grid: list[list[tuple[float, float, float]]],
    degree_u: int,
    degree_v: int,
):
    """Build a Geom_BSplineSurface whose CONTROL POLES are exactly `grid`.

    Unlike `_fit_bspline_surface` (which interpolates the points and may
    produce wildly-overshooting control poles), this construction keeps the
    control polygon = input grid, so the surface's bounding box is tight.

    Used for the offset surface in `surface_thicken_variable` so that the
    resulting loft solid's bbox matches the displaced grid's extent.
    """
    from OCP.Geom import Geom_BSplineSurface
    from OCP.gp import gp_Pnt
    from OCP.TColgp import TColgp_Array2OfPnt
    from OCP.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal

    rows = len(grid)
    cols = len(grid[0])
    # Degrees must satisfy degree <= n_poles - 1.
    deg_u = min(int(degree_u), rows - 1)
    deg_v = min(int(degree_v), cols - 1)

    poles = TColgp_Array2OfPnt(1, rows, 1, cols)
    for i, row in enumerate(grid, start=1):
        for j, (x, y, z) in enumerate(row, start=1):
            poles.SetValue(i, j, gp_Pnt(float(x), float(y), float(z)))

    uknots, umults = _uniform_clamped_knots(rows, deg_u)
    vknots, vmults = _uniform_clamped_knots(cols, deg_v)

    u_arr = TColStd_Array1OfReal(1, len(uknots))
    for i, k in enumerate(uknots, start=1):
        u_arr.SetValue(i, k)
    um_arr = TColStd_Array1OfInteger(1, len(umults))
    for i, m in enumerate(umults, start=1):
        um_arr.SetValue(i, m)
    v_arr = TColStd_Array1OfReal(1, len(vknots))
    for i, k in enumerate(vknots, start=1):
        v_arr.SetValue(i, k)
    vm_arr = TColStd_Array1OfInteger(1, len(vmults))
    for i, m in enumerate(vmults, start=1):
        vm_arr.SetValue(i, m)

    return Geom_BSplineSurface(poles, u_arr, v_arr, um_arr, vm_arr, deg_u, deg_v)


@skill(
    name="surface_thicken_variable",
    category="create",
    level="macro",
    summary="Fit a NURBS surface through a point grid and thicken it into a "
            "solid by displacing each control point along its surface normal "
            "by a per-point thickness. Used for freeform shells whose wall "
            "thickness varies — e.g., a phone back cover thicker in the "
            "middle, thinner at the rim.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["variable_thickness_shell"],
    preserves=[],
    manufacturing={
        "cnc_5axis":         {"min_wall_mm": 0.5, "extras": {"freeform": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"variable_wall": True}},
    },
    failure_modes=[
        "fm.bspline_fit_failed",
        "fm.thicken_loft_failed",
        "fm.thickness_grid_mismatch",
    ],
    cost_hint=0.55,
    expansion=[
        "fit_base_surface",
        "compute_per_point_normal",
        "fit_offset_surface",
        "loft_between_surfaces",
    ],
    post_conditions=[PostCondition(kind="body_present")],
)
class SurfaceThickenVariable(SkillBase):
    class Args(BaseModel):
        point_grid: list[list[tuple[float, float, float]]] = Field(
            description="rows × cols control grid (≥3×3)",
        )
        thickness_map: list[list[float]] = Field(
            description="rows × cols positive thickness in mm",
        )
        degree_u: int = Field(default=3, ge=3, le=8)
        degree_v: int = Field(default=3, ge=3, le=8)

        @model_validator(mode="after")
        def _grids_ok(self):
            _validate_grids(self.point_grid, self.thickness_map)
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections

        from phone_designer.skills.create.bspline_surface import (
            _fit_bspline_surface,
            _face_from_surface,
        )

        rows, cols = _validate_grids(args.point_grid, args.thickness_map)

        # 1. Fit base surface by interpolation — the surface passes through
        #    the input grid so the per-grid-point normals computed in step 3
        #    correspond to physical grid locations.
        base_surface = _fit_bspline_surface(
            args.point_grid, args.degree_u, args.degree_v, tol3d=1e-3,
        )

        # 2. Parametric domain — map grid (i,j) to (u_i, v_j).
        u_min, u_max, v_min, v_max = base_surface.Bounds()

        # 3. Build offset grid: each control point pushed along its surface
        #    normal by thickness_map[i][j].
        offset_grid: list[list[tuple[float, float, float]]] = []
        for i, row in enumerate(args.point_grid):
            u = u_min + (u_max - u_min) * (i / (rows - 1)) if rows > 1 else u_min
            offset_row: list[tuple[float, float, float]] = []
            for j, (px, py, pz) in enumerate(row):
                v = v_min + (v_max - v_min) * (j / (cols - 1)) if cols > 1 else v_min
                nx, ny, nz = _normal_at_uv(base_surface, u, v)
                t = float(args.thickness_map[i][j])
                offset_row.append((px + nx * t, py + ny * t, pz + nz * t))
            offset_grid.append(offset_row)

        # 4. Build the offset surface using the displaced points as the
        #    CONTROL POLES (not as interpolation targets). Interpolating
        #    a varying-Z grid through `_fit_bspline_surface` produces
        #    wildly-oscillating control poles to enforce C2 continuity,
        #    which inflates the loft's bounding box far beyond the actual
        #    geometric extent. Direct-pole construction guarantees the
        #    surface's control polygon stays inside the offset grid's
        #    physical extent, so the resulting solid's bbox is tight.
        offset_surface = _surface_from_poles(
            offset_grid, args.degree_u, args.degree_v,
        )

        # 5. Build faces from both surfaces and loft between their outer
        #    boundary wires. ThruSections(isSolid=True) closes the side caps.
        base_face = _face_from_surface(base_surface, tol=1e-6)
        offset_face = _face_from_surface(offset_surface, tol=1e-6)

        def _outer_wire(face):
            from OCP.TopAbs import TopAbs_WIRE
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopoDS import TopoDS
            it = TopExp_Explorer(face, TopAbs_WIRE)
            if not it.More():
                raise RuntimeError(
                    "surface_thicken_variable: face has no wire"
                )
            return TopoDS.Wire_s(it.Current())

        base_wire = _outer_wire(base_face)
        offset_wire = _outer_wire(offset_face)

        loft = BRepOffsetAPI_ThruSections(True, False, 1e-6)  # IsSolid, IsRuled
        loft.AddWire(base_wire)
        loft.AddWire(offset_wire)
        try:
            loft.Build()
        except Exception as e:
            raise RuntimeError(
                f"surface_thicken_variable: ThruSections.Build raised: {e}"
            ) from e
        if not loft.IsDone():
            raise RuntimeError(
                "surface_thicken_variable: ThruSections IsDone=False"
            )

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(loft.Shape()), history=history)
