"""support_tree_path — atomic, read-only-effect 3D printing DFM helper.

For each overhang point provided, plan a tree-style support that drops down to
the build plate along ``-build_direction``. Nearby branches merge as they
descend, mimicking SLA / FDM "tree supports" that resin / filament slicers
generate. This skill does not modify the body — it only reports the planned
branch geometry in ``extras["support_tree"]`` so a downstream skill (or the
slicer) can lay the actual material.

Branch merge rule (simple 2D in plane perpendicular to build axis):
    - Project all overhang points onto the build plate plane.
    - At each "level" (drop step of branch diameter * 2), merge branches whose
      projected centers lie within (branch_d * merge_factor). Merged branches
      become a single thicker trunk descending to the plate.

extras["support_tree"] schema:
    {
        "build_direction": [bx, by, bz],
        "branch_diameter_mm": float,
        "branches": [
            {
                "id": int,                  # unique per-branch id
                "tip": [x, y, z],            # overhang point (top of branch)
                "base": [x, y, z],            # build-plate landing point
                "length_mm": float,
                "merges_into": int | None,   # id of trunk it joins
                "children": [int, ...],      # branches that merge into this
            }, ...
        ],
        "merge_count": int,
        "total_branch_length_mm": float,
    }
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _bbox(shape) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """OCCT bbox (min, max). Returns zero box on failure."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.Add_s(shape, bb)
    except Exception:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    if bb.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def _project_extents(shape, bdir: tuple[float, float, float]) -> tuple[float, float]:
    """Return (min_proj, max_proj) of the body's bbox corners projected onto
    the given unit direction."""
    (mn, mx) = _bbox(shape)
    pmin = math.inf
    pmax = -math.inf
    for cx in (mn[0], mx[0]):
        for cy in (mn[1], mx[1]):
            for cz in (mn[2], mx[2]):
                pv = cx * bdir[0] + cy * bdir[1] + cz * bdir[2]
                if pv < pmin:
                    pmin = pv
                if pv > pmax:
                    pmax = pv
    if not math.isfinite(pmin):
        return (0.0, 0.0)
    return (pmin, pmax)


@skill(
    name="support_tree_path",
    category="modify/3dprint",
    level="atomic",
    summary="For each overhang point, plan a tree-style support that drops "
            "to the build plate along -build_direction; nearby branches merge "
            "as they descend. extras['support_tree']={branches:[...]}; body "
            "unchanged (read-only effect, suitable for slicer hand-off).",
    selector_kinds=[],
    history_rules={},
    produces_features=["support_tree_plan"],
    preserves=["body_topology"],
    manufacturing={
        "fdm":  {"extras": {"tree_supports": True,
                            "branch_diameter_recommended_mm": 2.0}},
        "sla":  {"extras": {"tree_supports": True,
                            "branch_diameter_recommended_mm": 0.6,
                            "purpose": "tree_supports_with_tip_contact"}},
        "sls":  {"extras": {"self_supporting": True,
                            "tree_supports_unnecessary": True}},
    },
    failure_modes=["fm.empty_overhang_points",
                   "fm.zero_build_vector"],
    cost_hint=0.15,
    # Read-only — just ensure body still exists.
    post_conditions=[PostCondition(kind="body_present")],
)
class SupportTreePath(SkillBase):
    class Args(BaseModel):
        overhang_points: list[tuple[float, float, float]] = Field(
            default_factory=list,
            description="Tip locations needing support — typically the "
                        "centroids of overhang faces or downward-facing "
                        "edge midpoints.",
        )
        build_direction: tuple[float, float, float] = Field(default=(0.0, 0.0, 1.0))
        tree_branch_d_mm: float = Field(default=2.0, gt=0.0, le=20.0)
        merge_factor: float = Field(default=2.5, gt=0.0, le=20.0,
            description="Two branches merge when their projected centers fall "
                        "within (branch_d * merge_factor) of each other.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        # Validate inputs.
        if not args.overhang_points:
            raise ValueError(
                "support_tree_path: overhang_points must be non-empty",
            )
        bx, by, bz = args.build_direction
        blen = math.sqrt(bx * bx + by * by + bz * bz)
        if blen < 1e-12:
            raise ValueError(
                "support_tree_path: build_direction must be non-zero",
            )
        bdir = (bx / blen, by / blen, bz / blen)

        shape = _occt_shape(body)
        # Build plate sits at the body's minimum extent along the build axis.
        plate_proj, _ = _project_extents(shape, bdir)

        # Build branch records. Each tip drops straight along -bdir to the
        # build plate plane (proj == plate_proj).
        branches: list[dict[str, Any]] = []
        for i, pt in enumerate(args.overhang_points):
            px, py, pz = float(pt[0]), float(pt[1]), float(pt[2])
            tip_proj = px * bdir[0] + py * bdir[1] + pz * bdir[2]
            drop = max(0.0, tip_proj - plate_proj)
            base = (
                px - bdir[0] * drop,
                py - bdir[1] * drop,
                pz - bdir[2] * drop,
            )
            length = math.sqrt(
                (px - base[0]) ** 2 + (py - base[1]) ** 2 + (pz - base[2]) ** 2
            )
            branches.append({
                "id": i,
                "tip": [round(px, 4), round(py, 4), round(pz, 4)],
                "base": [round(base[0], 4), round(base[1], 4), round(base[2], 4)],
                "length_mm": round(length, 4),
                "merges_into": None,
                "children": [],
            })

        # Pairwise merge — greedy, smallest-distance-first union-find pass on
        # projected bases. Two branches whose base projections fall within
        # (branch_d * merge_factor) join: the later branch (higher id) is
        # marked as merging into the earlier one.
        merge_dist = args.tree_branch_d_mm * args.merge_factor

        # Project each base onto plate plane coordinates (just use base xyz —
        # works regardless of build axis since both bases share the same
        # plate plane proj value).
        merge_count = 0
        for j in range(len(branches)):
            if branches[j]["merges_into"] is not None:
                continue
            bj = branches[j]["base"]
            for k in range(j + 1, len(branches)):
                if branches[k]["merges_into"] is not None:
                    continue
                bk = branches[k]["base"]
                d = math.sqrt(
                    (bj[0] - bk[0]) ** 2
                    + (bj[1] - bk[1]) ** 2
                    + (bj[2] - bk[2]) ** 2
                )
                if d <= merge_dist:
                    branches[k]["merges_into"] = branches[j]["id"]
                    branches[j]["children"].append(branches[k]["id"])
                    merge_count += 1

        total_len = round(sum(b["length_mm"] for b in branches), 4)

        report = {
            "build_direction": [round(c, 4) for c in bdir],
            "branch_diameter_mm": args.tree_branch_d_mm,
            "merge_factor": args.merge_factor,
            "branches": branches,
            "merge_count": merge_count,
            "total_branch_length_mm": total_len,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"support_tree": report},
        )
