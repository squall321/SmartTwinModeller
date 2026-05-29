"""bspline_surface — atomic macro create skill.

Fit a B-spline surface through a rectangular grid of 3D points. Optionally
thicken the resulting face into a solid (BRepOffset_MakeOffset with the
`Thickening=True` flag).

Args:
    point_grid: list[list[(x, y, z)]] — rows × cols, both ≥ 3. All rows must
        be the same length.
    degree_u: int = 3 — surface degree along U (rows). Range [3, 8].
    degree_v: int = 3 — surface degree along V (cols). Range [3, 8].
    thicken_mm: float | None = None — if set, thicken the surface into a
        solid of the given thickness (mm). If None, the result body is the
        bare face (treated as a Part for downstream skills, e.g., as a
        cutter surface).

Internal helper `_fit_bspline_surface(grid, degu, degv)` returns the raw
OCCT `Geom_BSplineSurface` and is reused by `extrude_pocket_to_curved_floor`.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# ── shared NURBS surface helpers ─────────────────────────────────────────────


def _validate_point_grid(grid: list[list[tuple[float, float, float]]]) -> tuple[int, int]:
    """Validate `grid` is a rectangular ≥3×≥3 grid. Return (rows, cols)."""
    if not grid:
        raise ValueError("point_grid is empty")
    rows = len(grid)
    cols = len(grid[0])
    if rows < 3:
        raise ValueError(f"point_grid needs ≥3 rows (got {rows})")
    if cols < 3:
        raise ValueError(f"point_grid needs ≥3 cols (got {cols})")
    for i, row in enumerate(grid):
        if len(row) != cols:
            raise ValueError(
                f"point_grid row {i} has {len(row)} cols; expected {cols}"
            )
        for j, p in enumerate(row):
            if len(p) != 3:
                raise ValueError(
                    f"point_grid[{i}][{j}] expected (x,y,z) — got {p!r}"
                )
    return rows, cols


def _fit_bspline_surface(
    grid: list[list[tuple[float, float, float]]],
    degree_u: int = 3,
    degree_v: int = 3,
    tol3d: float = 1e-3,
):
    """Fit a B-spline surface through `grid`. Returns the OCCT Geom_BSplineSurface.

    Uses `GeomAPI_PointsToBSplineSurface` with DegMin=degree_u, DegMax=8
    (and similarly we allow OCCT to pick V within [degree_v, 8]). Continuity
    C2 by default. The `degree_u` / `degree_v` args set the *minimum* degree;
    OCCT may pick higher if the grid demands it.
    """
    from OCP.GeomAbs import GeomAbs_Shape
    from OCP.GeomAPI import GeomAPI_PointsToBSplineSurface
    from OCP.gp import gp_Pnt
    from OCP.TColgp import TColgp_Array2OfPnt

    rows, cols = _validate_point_grid(grid)

    pts = TColgp_Array2OfPnt(1, rows, 1, cols)
    for i, row in enumerate(grid, start=1):
        for j, (x, y, z) in enumerate(row, start=1):
            pts.SetValue(i, j, gp_Pnt(float(x), float(y), float(z)))

    deg_min = max(3, min(int(degree_u), int(degree_v)))
    try:
        fitter = GeomAPI_PointsToBSplineSurface(
            pts,
            deg_min,
            8,
            GeomAbs_Shape.GeomAbs_C2,
            tol3d,
        )
    except Exception as e:
        raise RuntimeError(
            f"bspline_surface: GeomAPI_PointsToBSplineSurface failed "
            f"(rows={rows}, cols={cols}, deg_min={deg_min}): {e}"
        ) from e
    surface = fitter.Surface()
    if surface is None:
        raise RuntimeError("bspline_surface: fitter.Surface() returned None")
    return surface


def _face_from_surface(surface, tol: float = 1e-6):
    """Build a TopoDS_Face from a Geom_Surface (full UV domain)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    mf = BRepBuilderAPI_MakeFace(surface, tol)
    if not mf.IsDone():
        raise RuntimeError("bspline_surface: MakeFace failed")
    return mf.Face()


def _thicken_face(face, thickness_mm: float, tol: float = 1e-3):
    """Offset a single face into a solid of the given thickness.

    Uses BRepOffset_MakeOffset with Thickening=True. Returns the resulting
    TopoDS_Shape. Raises a clear error on failure (thickening can fail when
    the surface has high curvature relative to thickness, or when the offset
    self-intersects).
    """
    from OCP.BRepOffset import BRepOffset_MakeOffset, BRepOffset_Mode
    from OCP.GeomAbs import GeomAbs_JoinType

    maker = BRepOffset_MakeOffset()
    maker.Initialize(
        face,
        float(thickness_mm),
        tol,
        BRepOffset_Mode.BRepOffset_Skin,
        False,                                  # Intersection
        False,                                  # SelfInter
        GeomAbs_JoinType.GeomAbs_Arc,
        True,                                   # Thickening
        False,                                  # RemoveIntEdges
    )
    try:
        maker.MakeOffsetShape()
    except Exception as e:
        raise RuntimeError(
            f"bspline_surface: thicken via BRepOffset_MakeOffset raised: {e}"
        ) from e
    if not maker.IsDone():
        raise RuntimeError(
            f"bspline_surface: thicken (BRepOffset_MakeOffset) did not "
            f"complete (thickness={thickness_mm} mm)"
        )
    return maker.Shape()


# ── skill ─────────────────────────────────────────────────────────────────


@skill(
    name="bspline_surface",
    category="create",
    level="macro",
    summary="Create a NURBS (B-spline) surface fit through a rectangular grid "
            "of 3D points. Optionally thicken into a solid. Used as a freeform "
            "shell, lens-blank surface, or as input to curved-floor pockets.",
    selector_kinds=[],
    history_rules={"output_faces": HistoryRule.GENERATED_NEW},
    produces_features=["bspline_surface"],
    preserves=[],
    manufacturing={
        "cnc_5axis": {"min_wall_mm": 0.5, "extras": {"freeform": True}},
    },
    failure_modes=[
        "fm.bspline_fit_failed",
        "fm.bspline_thicken_failed",
        "fm.grid_too_small",
    ],
    cost_hint=0.4,
    expansion=["fit_bspline_surface", "make_face_from_surface", "thicken_face"],
    post_conditions=[PostCondition(kind="body_present")],
)
class BSplineSurface(SkillBase):
    class Args(BaseModel):
        point_grid: list[list[tuple[float, float, float]]] = Field(
            description="rows × cols grid of (x,y,z), both ≥ 3",
        )
        degree_u: int = Field(default=3, ge=3, le=8)
        degree_v: int = Field(default=3, ge=3, le=8)
        thicken_mm: float | None = Field(default=None)

        @model_validator(mode="after")
        def _grid_ok(self):
            _validate_point_grid(self.point_grid)
            if self.thicken_mm is not None and self.thicken_mm <= 0:
                raise ValueError(
                    f"thicken_mm must be > 0 (got {self.thicken_mm})"
                )
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        surface = _fit_bspline_surface(
            args.point_grid, args.degree_u, args.degree_v, tol3d=1e-3,
        )
        face = _face_from_surface(surface, tol=1e-6)

        if args.thicken_mm is not None:
            shape = _thicken_face(face, args.thicken_mm, tol=1e-3)
            result_body = Part(shape)
        else:
            # Wrap the bare face as a Part — downstream skills that need a
            # solid will fail loudly; skills that just want a surface (e.g.,
            # cutter for curved-floor pockets) can use body.wrapped.
            result_body = Part(face)

        history = EntityHistoryMap(
            rules={"output_faces": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=result_body, history=history)
