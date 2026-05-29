"""tool_reachability — atomic, read-only CNC DFM check.

For each detected pocket on the body, verify two things from the chosen setup
direction (default +Z = top of stock):

  1. ASPECT RATIO: depth / effective_diameter <= max_aspect_ratio. A pocket
     whose depth exceeds the tool diameter by more than ``max_aspect_ratio``
     becomes a stub-end mill operation that chatters and breaks tools.
     With the default 4.0× rule a Ø6 mm cutter cannot reach below 24 mm.

  2. CLEAR APPROACH: a ray cast from above the body in the setup direction
     down to the pocket centre must not be blocked by other geometry — i.e.
     no overhanging shoulder, no opposing wall closer to the surface than the
     pocket centre. Translates an undercut as unreachable.

The body is unchanged (post_condition body_present).

extras schema:
    {
      "tool_diameter_mm": float,
      "max_aspect_ratio": float,
      "setup_direction": [sx, sy, sz],
      "pockets_total": int,
      "unreachable_features": [
        {"location": [x, y, z],
         "reason": str,                 # "aspect_ratio" | "blocked_approach"
         "depth_mm": float,
         "diameter_mm": float,
         "aspect_ratio": float},
        ...
      ],
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


def _body_bbox(shape) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def _pocket_features(shape) -> list[dict[str, Any]]:
    """Re-use find_features' heuristic pocket detector to enumerate pockets."""
    from phone_designer.skills._resolvers import _all_faces
    from phone_designer.skills.inspect.find_features import _detect_pocket

    faces = _all_faces(shape)
    body_bbox = _body_bbox(shape)
    out = []
    for f in faces:
        feat = _detect_pocket(f, body_bbox, faces)
        if feat is not None:
            out.append(feat)
    return out


def _pocket_depth_and_diameter(
    shape, pocket_center, setup_dir, body_bbox,
) -> tuple[float, float]:
    """Return (depth_mm, effective_diameter_mm) for the pocket whose floor
    centre is at ``pocket_center``, machined from above along ``setup_dir``.

    depth: distance from the body's top-extent (along -setup_dir) down to the
           pocket floor.
    diameter: 2 * shortest in-plane distance from the floor centre to the
              body's bbox interior wall, projected perpendicular to the setup
              direction. Conservative — for non-circular pockets this is the
              minimum of the in-plane bbox dimensions traversed by the pocket.
    """
    (mn, mx) = body_bbox
    sx, sy, sz = setup_dir
    # Project bbox span along setup direction to compute "top" surface plane
    # (the highest body extent in the setup direction).
    corners = [
        (mn[0], mn[1], mn[2]), (mx[0], mn[1], mn[2]),
        (mn[0], mx[1], mn[2]), (mx[0], mx[1], mn[2]),
        (mn[0], mn[1], mx[2]), (mx[0], mn[1], mx[2]),
        (mn[0], mx[1], mx[2]), (mx[0], mx[1], mx[2]),
    ]
    proj = [c[0] * sx + c[1] * sy + c[2] * sz for c in corners]
    top_proj = max(proj)
    pocket_proj = (
        pocket_center[0] * sx + pocket_center[1] * sy + pocket_center[2] * sz
    )
    depth = max(0.0, top_proj - pocket_proj)

    # Effective diameter — distance in the plane perpendicular to setup_dir
    # from pocket centre to nearest bbox wall * 2.
    # Build the two in-plane axes orthogonal to setup_dir.
    # Pick a helper axis not parallel to setup_dir.
    helper = (0.0, 0.0, 1.0)
    if abs(sx * helper[0] + sy * helper[1] + sz * helper[2]) > 0.9:
        helper = (1.0, 0.0, 0.0)
    # u = setup × helper, v = setup × u
    ux = sy * helper[2] - sz * helper[1]
    uy = sz * helper[0] - sx * helper[2]
    uz = sx * helper[1] - sy * helper[0]
    un = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
    ux, uy, uz = ux / un, uy / un, uz / un
    vx = sy * uz - sz * uy
    vy = sz * ux - sx * uz
    vz = sx * uy - sy * ux
    vn = math.sqrt(vx * vx + vy * vy + vz * vz) or 1.0
    vx, vy, vz = vx / vn, vy / vn, vz / vn

    # Project pocket centre and bbox extents into (u, v) frame, return min span.
    def proj_uv(p):
        return (
            p[0] * ux + p[1] * uy + p[2] * uz,
            p[0] * vx + p[1] * vy + p[2] * vz,
        )

    pu, pv = proj_uv(pocket_center)
    bbox_u = sorted(proj_uv(c)[0] for c in corners)
    bbox_v = sorted(proj_uv(c)[1] for c in corners)
    u_min, u_max = bbox_u[0], bbox_u[-1]
    v_min, v_max = bbox_v[0], bbox_v[-1]
    half_u = min(pu - u_min, u_max - pu)
    half_v = min(pv - v_min, v_max - pv)
    eff_diameter = 2.0 * max(0.01, min(half_u, half_v))
    return depth, eff_diameter


def _approach_clear(shape, pocket_center, setup_dir, body_bbox) -> bool:
    """True if a ray from far outside the body, along -setup_dir, lands on a
    point in the body whose projection-along-setup matches the pocket centre.

    In effect: walk DOWN from above and check that the first surface we hit is
    the pocket's own floor (or a face co-linear with the pocket centre). If
    we hit something significantly above the pocket Z first, the approach is
    blocked (overhang / undercut feature in the way).
    """
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

    (mn, mx) = body_bbox
    sx, sy, sz = setup_dir
    pocket_proj = (
        pocket_center[0] * sx + pocket_center[1] * sy + pocket_center[2] * sz
    )
    span = max(
        mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2], 1.0,
    )
    # Start far above along +setup_dir.
    start_offset = span * 5.0
    start = gp_Pnt(
        pocket_center[0] + sx * start_offset,
        pocket_center[1] + sy * start_offset,
        pocket_center[2] + sz * start_offset,
    )
    direction = gp_Dir(-sx, -sy, -sz)
    line = gp_Lin(start, direction)
    try:
        inter = BRepIntCurveSurface_Inter()
        inter.Init(shape, line, 1e-6)
    except Exception:
        # Cannot test → be conservative and call it BLOCKED so the planner
        # is alerted.
        return False

    # Find smallest positive t (= first hit when travelling DOWN). The
    # parameter is the distance from start along direction.
    best_t = None
    while inter.More():
        t = inter.W()
        if t > 0:
            if best_t is None or t < best_t:
                best_t = t
        inter.Next()
    if best_t is None:
        # No hit — pocket centre is outside the body; treat as reachable.
        return True

    # Convert hit parameter back to a projection along setup_dir.
    # hit_point = start + t * direction
    hx = start.X() - sx * best_t
    hy = start.Y() - sy * best_t
    hz = start.Z() - sz * best_t
    hit_proj = hx * sx + hy * sy + hz * sz
    # Reachable when first hit projection is at or below the pocket centre
    # (we entered the pocket and hit its floor) within a generous tolerance.
    tol = 0.5  # mm
    return hit_proj <= pocket_proj + tol


@skill(
    name="tool_reachability",
    category="inspect",
    level="atomic",
    summary="For each detected pocket, verify the cutter can physically reach "
            "it from the chosen setup direction: depth/diameter <= "
            "max_aspect_ratio AND the approach ray is not blocked by other "
            "geometry. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["tool_reachability_report"],
    preserves=["body_topology"],
    manufacturing={
        "cnc_3axis": {"extras": {
            "max_aspect_ratio": "args.max_aspect_ratio",
            "tool_diameter_mm": "args.tool_diameter_mm",
        }},
    },
    failure_modes=["fm.raycast_failed"],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class ToolReachability(SkillBase):
    class Args(BaseModel):
        tool_diameter_mm: float = Field(
            gt=0.0, le=50.0,
            description="Diameter of the cutter being checked (mm).",
        )
        max_aspect_ratio: float = Field(
            default=4.0, gt=0.0, le=20.0,
            description="Allowed depth/diameter ratio. Above this the tool "
                        "deflects / chatters / breaks.",
        )
        setup_direction: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 1.0),
            description="Unit vector pointing OUT of the part along the tool "
                        "approach axis. Default +Z (top of stock).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        shape = _occt_shape(body)
        body_bbox = _body_bbox(shape)

        # Normalise setup direction.
        sx, sy, sz = args.setup_direction
        n = math.sqrt(sx * sx + sy * sy + sz * sz)
        if n < 1e-9:
            sx, sy, sz = 0.0, 0.0, 1.0
        else:
            sx, sy, sz = sx / n, sy / n, sz / n

        pockets = _pocket_features(shape)
        unreachable: list[dict[str, Any]] = []

        for pk in pockets:
            center = tuple(pk["center"])
            depth, eff_diameter = _pocket_depth_and_diameter(
                shape, center, (sx, sy, sz), body_bbox,
            )
            # Use the SMALLER of tool diameter and effective diameter as
            # denominator — the binding constraint.
            denom = min(args.tool_diameter_mm, eff_diameter)
            aspect = depth / denom if denom > 1e-6 else math.inf

            reason = None
            if aspect > args.max_aspect_ratio:
                reason = "aspect_ratio"
            elif not _approach_clear(shape, center, (sx, sy, sz), body_bbox):
                reason = "blocked_approach"

            if reason is not None:
                unreachable.append({
                    "location": [round(c, 4) for c in center],
                    "reason": reason,
                    "depth_mm": round(depth, 4),
                    "diameter_mm": round(eff_diameter, 4),
                    "aspect_ratio": round(aspect, 4),
                })

        extras = {
            "tool_diameter_mm": args.tool_diameter_mm,
            "max_aspect_ratio": args.max_aspect_ratio,
            "setup_direction": [round(sx, 4), round(sy, 4), round(sz, 4)],
            "pockets_total": len(pockets),
            "unreachable_features": unreachable,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
