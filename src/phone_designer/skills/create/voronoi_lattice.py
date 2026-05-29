"""voronoi_lattice — macro create. Random 3D Voronoi lattice cellular structure.

Seed N points (deterministic via `seed`) inside the bounding box, then build a
network of cylinders (radius = wall_thickness_mm/2) along Voronoi ridge edges.
The result is a self-supporting, organic-looking lattice useful for
weight-reduced infill, heat exchangers, etc.

Behavior depending on scipy availability:
  - scipy present (preferred): compute true 3D Voronoi tessellation. For each
    pair of finite Voronoi vertices that share a ridge AND fall inside the
    bbox, drop a cylinder strut connecting them.
  - scipy absent (fallback): connect every pair of seed points whose
    distance is below a heuristic threshold (mean nearest-neighbour distance
    * 1.3). Result is a Delaunay-ish strut network — geometrically different
    but topologically related; clearly documented.

Returns a fused TopoDS_Shape (or a compound of cylinders if fusion fails).
"""
from __future__ import annotations

import math
import random
from typing import Any

from pydantic import BaseModel, Field, field_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _cylinder_between(p1, p2, radius: float):
    """Build an OCCT cylinder from p1 to p2, radius r. Returns Shape or None."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    dx, dy, dz = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length < 1e-6:
        return None
    direction = gp_Dir(dx / length, dy / length, dz / length)
    origin = gp_Pnt(p1[0], p1[1], p1[2])
    ax2 = gp_Ax2(origin, direction)
    maker = BRepPrimAPI_MakeCylinder(ax2, radius, length)
    try:
        maker.Build()
        if maker.IsDone():
            return maker.Shape()
    except Exception:
        return None
    return None


def _clip_to_bbox(p, bbox_min, bbox_max):
    """True if point p lies within bbox (inclusive)."""
    return (bbox_min[0] <= p[0] <= bbox_max[0]
            and bbox_min[1] <= p[1] <= bbox_max[1]
            and bbox_min[2] <= p[2] <= bbox_max[2])


def _fuse_shapes(shapes):
    """Fuse a list of shapes pairwise. Falls back to compound on failure."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.TopoDS import TopoDS_Compound

    if not shapes:
        builder = BRep_Builder()
        comp = TopoDS_Compound()
        builder.MakeCompound(comp)
        return comp

    if len(shapes) == 1:
        return shapes[0]

    # Try sequential fuse; if any step fails, fall back to compound.
    acc = shapes[0]
    fuse_failed = False
    for s in shapes[1:]:
        try:
            f = BRepAlgoAPI_Fuse(acc, s)
            f.Build()
            if not f.IsDone():
                fuse_failed = True
                break
            acc = f.Shape()
        except Exception:
            fuse_failed = True
            break

    if not fuse_failed:
        return acc

    # Fallback: compound (no boolean merging — overlapping struts but body_present).
    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    for s in shapes:
        builder.Add(comp, s)
    return comp


def _voronoi_struts_scipy(seeds, bbox_min, bbox_max):
    """Return list of (p1, p2) strut endpoints from scipy Voronoi."""
    from scipy.spatial import Voronoi

    vor = Voronoi(seeds)
    struts: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    seen: set[tuple[int, int]] = set()

    for ridge in vor.ridge_vertices:
        # ridge is a list of vertex indices; -1 means "vertex at infinity".
        finite = [i for i in ridge if i >= 0]
        # Connect consecutive vertices along the ridge polygon.
        for a, b in zip(finite, finite[1:] + finite[:1]):
            key = (a, b) if a < b else (b, a)
            if key in seen:
                continue
            seen.add(key)
            p1 = tuple(float(c) for c in vor.vertices[a])
            p2 = tuple(float(c) for c in vor.vertices[b])
            # Skip if either endpoint is outside bbox by a wide margin.
            if not (_clip_to_bbox(p1, bbox_min, bbox_max)
                    and _clip_to_bbox(p2, bbox_min, bbox_max)):
                continue
            struts.append((p1, p2))
    return struts


def _fallback_struts(seeds):
    """Connect every nearby seed pair (no scipy). Heuristic threshold = 1.3 *
    mean nearest-neighbour distance."""
    n = len(seeds)
    # Compute nearest-neighbour distances.
    nn = []
    for i in range(n):
        best = float("inf")
        for j in range(n):
            if i == j:
                continue
            d = math.dist(seeds[i], seeds[j])
            if d < best:
                best = d
        nn.append(best)
    mean_nn = sum(nn) / max(1, len(nn))
    threshold = mean_nn * 1.3

    struts = []
    for i in range(n):
        for j in range(i + 1, n):
            if math.dist(seeds[i], seeds[j]) <= threshold:
                struts.append((tuple(seeds[i]), tuple(seeds[j])))
    return struts


@skill(
    name="voronoi_lattice",
    category="create",
    level="macro",
    summary="Random Voronoi-cell strut lattice inside a bounding box. Uses scipy "
            "for true Voronoi when available; otherwise falls back to a "
            "nearest-neighbour strut network (documented approximation).",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["voronoi_lattice"],
    preserves=[],
    manufacturing={
        "sla":          {"min_wall_mm": 0.4, "undercut_allowed": True},
        "powder_bed":   {"min_wall_mm": 0.5, "undercut_allowed": True},
    },
    failure_modes=["fm.lattice_empty", "fm.boolean_failed"],
    cost_hint=0.6,
    expansion=["cylinder", "boolean_union"],
    post_conditions=[PostCondition(kind="body_present")],
)
class VoronoiLattice(SkillBase):
    class Args(BaseModel):
        bbox_min: tuple[float, float, float]
        bbox_max: tuple[float, float, float]
        n_seeds: int = Field(ge=5, description="Number of seed points (≥ 5).")
        wall_thickness_mm: float = Field(gt=0, description="Strut diameter.")
        seed: int = Field(default=42, description="RNG seed for reproducibility.")

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

        rng = random.Random(args.seed)
        seeds = [
            (
                rng.uniform(args.bbox_min[0], args.bbox_max[0]),
                rng.uniform(args.bbox_min[1], args.bbox_max[1]),
                rng.uniform(args.bbox_min[2], args.bbox_max[2]),
            )
            for _ in range(args.n_seeds)
        ]

        used_fallback = False
        try:
            struts = _voronoi_struts_scipy(seeds, args.bbox_min, args.bbox_max)
            if not struts:
                # Voronoi yielded no in-bbox edges (e.g., n_seeds too small) —
                # fall back rather than emit an empty lattice.
                struts = _fallback_struts(seeds)
                used_fallback = True
        except ImportError:
            struts = _fallback_struts(seeds)
            used_fallback = True

        radius = args.wall_thickness_mm / 2.0
        cyls = []
        for p1, p2 in struts:
            c = _cylinder_between(p1, p2, radius)
            if c is not None:
                cyls.append(c)

        if not cyls:
            raise RuntimeError(
                f"voronoi_lattice: produced 0 struts (n_seeds={args.n_seeds}, "
                f"bbox={args.bbox_min}→{args.bbox_max}); try more seeds."
            )

        fused = _fuse_shapes(cyls)

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(
            body=Part(fused),
            history=history,
            extras={
                "strut_count": len(cyls),
                "seed_count": len(seeds),
                "used_fallback": used_fallback,
            },
        )
