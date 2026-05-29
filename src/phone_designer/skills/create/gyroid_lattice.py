"""gyroid_lattice — macro create. Triply-periodic minimal surface (TPMS) lattice.

Gyroid implicit equation:
    F(x,y,z) = sin(x')·cos(y') + sin(y')·cos(z') + sin(z')·cos(x')
where (x', y', z') = 2π · (x, y, z) / period_mm.

A "thickened" gyroid is the region {|F(x,y,z)| ≤ ε}. We approximate it by
sampling on a `resolution³` grid over the bbox and placing a sphere of
radius `wall_thickness_mm/2` at every zero-crossing point detected on grid
edges (linear interpolation). The result is a beaded surface mesh that
visually and topologically resembles the gyroid surface while keeping the
implementation independent of scikit-image (which is not part of the
project's dep set).

If/when `scikit-image` is added, a proper `marching_cubes` path can be
swapped in here without changing the public arg schema.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, field_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _gyroid(x: float, y: float, z: float, period: float) -> float:
    k = 2.0 * math.pi / period
    xp, yp, zp = k * x, k * y, k * z
    return (math.sin(xp) * math.cos(yp)
            + math.sin(yp) * math.cos(zp)
            + math.sin(zp) * math.cos(xp))


def _sphere(center, radius: float):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
    from OCP.gp import gp_Pnt

    maker = BRepPrimAPI_MakeSphere(gp_Pnt(center[0], center[1], center[2]), radius)
    maker.Build()
    if maker.IsDone():
        return maker.Shape()
    return None


def _compound_of(shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    for s in shapes:
        builder.Add(comp, s)
    return comp


def _zero_crossings_along_edges(samples, bbox_min, bbox_max, res):
    """Yield interpolated zero-crossing points along all grid edges.

    `samples` is a flat list of length res³ with samples[i*res*res + j*res + k]
    = F(grid[i,j,k]). bbox_min/max give the grid extents.
    """
    dx = (bbox_max[0] - bbox_min[0]) / (res - 1)
    dy = (bbox_max[1] - bbox_min[1]) / (res - 1)
    dz = (bbox_max[2] - bbox_min[2]) / (res - 1)

    def pos(i, j, k):
        return (bbox_min[0] + i * dx,
                bbox_min[1] + j * dy,
                bbox_min[2] + k * dz)

    def val(i, j, k):
        return samples[i * res * res + j * res + k]

    def interp(p1, v1, p2, v2):
        # Linear interpolation to zero. v1, v2 of opposite sign.
        if abs(v1 - v2) < 1e-12:
            return p1
        t = v1 / (v1 - v2)
        return (p1[0] + t * (p2[0] - p1[0]),
                p1[1] + t * (p2[1] - p1[1]),
                p1[2] + t * (p2[2] - p1[2]))

    crossings = []
    # X-edges
    for i in range(res - 1):
        for j in range(res):
            for k in range(res):
                v1 = val(i, j, k)
                v2 = val(i + 1, j, k)
                if (v1 > 0) != (v2 > 0):
                    crossings.append(interp(pos(i, j, k), v1, pos(i + 1, j, k), v2))
    # Y-edges
    for i in range(res):
        for j in range(res - 1):
            for k in range(res):
                v1 = val(i, j, k)
                v2 = val(i, j + 1, k)
                if (v1 > 0) != (v2 > 0):
                    crossings.append(interp(pos(i, j, k), v1, pos(i, j + 1, k), v2))
    # Z-edges
    for i in range(res):
        for j in range(res):
            for k in range(res - 1):
                v1 = val(i, j, k)
                v2 = val(i, j, k + 1)
                if (v1 > 0) != (v2 > 0):
                    crossings.append(interp(pos(i, j, k), v1, pos(i, j, k + 1), v2))
    return crossings


@skill(
    name="gyroid_lattice",
    category="create",
    level="macro",
    summary="Gyroid TPMS lattice (sphere-bead approximation of the zero-isosurface). "
            "Self-supporting, periodic, used for lightweight infill and heat "
            "exchangers.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["gyroid_lattice"],
    preserves=[],
    manufacturing={
        "sla":          {"min_wall_mm": 0.4, "undercut_allowed": True},
        "powder_bed":   {"min_wall_mm": 0.5, "undercut_allowed": True},
    },
    failure_modes=["fm.lattice_empty"],
    cost_hint=0.7,
    expansion=["sphere", "boolean_union"],
    post_conditions=[PostCondition(kind="body_present")],
)
class GyroidLattice(SkillBase):
    class Args(BaseModel):
        bbox_min: tuple[float, float, float]
        bbox_max: tuple[float, float, float]
        period_mm: float = Field(gt=0, description="Spatial period of the gyroid.")
        wall_thickness_mm: float = Field(gt=0, description="Wall thickness ≈ bead diameter.")
        resolution: int = Field(default=20, ge=4, le=200,
                                 description="Sampling grid resolution per axis.")

        @field_validator("bbox_max")
        @classmethod
        def _bbox_valid(cls, v, info):
            mn = info.data.get("bbox_min")
            if mn is not None:
                for i, axis in enumerate("xyz"):
                    if v[i] <= mn[i]:
                        raise ValueError(
                            f"bbox_max[{axis}]={v[i]} must be > bbox_min[{axis}]={mn[i]}"
                        )
            return v

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        res = args.resolution
        bbox_min = tuple(args.bbox_min)
        bbox_max = tuple(args.bbox_max)
        dx = (bbox_max[0] - bbox_min[0]) / (res - 1)
        dy = (bbox_max[1] - bbox_min[1]) / (res - 1)
        dz = (bbox_max[2] - bbox_min[2]) / (res - 1)

        # Sample on grid.
        samples = [0.0] * (res * res * res)
        for i in range(res):
            x = bbox_min[0] + i * dx
            for j in range(res):
                y = bbox_min[1] + j * dy
                base = i * res * res + j * res
                for k in range(res):
                    z = bbox_min[2] + k * dz
                    samples[base + k] = _gyroid(x, y, z, args.period_mm)

        crossings = _zero_crossings_along_edges(samples, bbox_min, bbox_max, res)
        if not crossings:
            raise RuntimeError(
                f"gyroid_lattice: no zero-crossings detected — period_mm="
                f"{args.period_mm} likely larger than bbox extent; reduce period "
                f"or enlarge bbox."
            )

        radius = args.wall_thickness_mm / 2.0
        spheres = []
        for c in crossings:
            s = _sphere(c, radius)
            if s is not None:
                spheres.append(s)

        if not spheres:
            raise RuntimeError("gyroid_lattice: every sphere build failed")

        # Compound — fusing thousands of spheres is wildly expensive and not
        # worth it for a visualization/inspection-grade artifact. Volume
        # measurement still works on a compound.
        shape = _compound_of(spheres)

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(
            body=Part(shape),
            history=history,
            extras={
                "bead_count": len(spheres),
                "grid_resolution": res,
                "period_mm": float(args.period_mm),
            },
        )
