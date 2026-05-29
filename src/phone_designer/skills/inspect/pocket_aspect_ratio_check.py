"""pocket_aspect_ratio_check — atomic, read-only DFM check.

For every detected pocket, compute depth / min_in_plane_dimension. Pockets
with a ratio above ``max_aspect_ratio`` chatter the cutter, deflect tool
shanks and trap chips — the classic "deep slot" DFM violation.

Compared to ``tool_reachability``, this skill is independent of any specific
tool: it answers "is the pocket itself geometrically too deep for its width?"
which a planner uses to choose between machining setups, EDM, or a redesign.

extras schema:
    {
      "max_aspect_ratio": float,
      "pocket_count": int,
      "pocket_aspect_violations": [
        {"pocket_id": int,
         "ratio": float,
         "depth_mm": float,
         "min_dimension_mm": float,
         "location": [x, y, z],
         "recommendation": str},
        ...
      ],
    }

Body is unchanged (post_condition body_present).
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


def _body_bbox(shape):
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


def _pocket_dimensions(face, body_bbox) -> tuple[float, float, float]:
    """Return (depth_mm, min_in_plane_mm, max_in_plane_mm) for a planar pocket
    base face.

    depth: distance from face centre to body's bbox surface along the face
           normal (the pocket is cut INTO the body opposite to the outward
           normal, so depth = projection of normal onto bbox extent).
    in-plane: bbox of the face itself measured perpendicular to its normal.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    from phone_designer.skills._resolvers import (
        _face_center, _face_normal_at_center,
    )

    n = _face_normal_at_center(face)
    c = _face_center(face)
    (mn, mx) = body_bbox

    # Face's own bbox → in-plane width estimates.
    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(face, bb)
    except Exception:
        BRepBndLib.Add_s(face, bb)
    if bb.IsVoid():
        face_span = (0.0, 0.0, 0.0)
    else:
        fxmin, fymin, fzmin, fxmax, fymax, fzmax = bb.Get()
        face_span = (fxmax - fxmin, fymax - fymin, fzmax - fzmin)

    # Depth — project body bbox extent along face normal sign, take distance
    # from face centre to the opposite extent of the body (i.e. the body's
    # "opening side" of the pocket).
    nx, ny, nz = n
    if nx > 0:
        opp_x = mx[0]
    elif nx < 0:
        opp_x = mn[0]
    else:
        opp_x = c[0]
    if ny > 0:
        opp_y = mx[1]
    elif ny < 0:
        opp_y = mn[1]
    else:
        opp_y = c[1]
    if nz > 0:
        opp_z = mx[2]
    elif nz < 0:
        opp_z = mn[2]
    else:
        opp_z = c[2]
    depth_vec = (opp_x - c[0], opp_y - c[1], opp_z - c[2])
    depth = abs(depth_vec[0] * nx + depth_vec[1] * ny + depth_vec[2] * nz)

    # In-plane dims = the two largest of face_span that are NOT aligned with
    # the normal. Since we have body-axis normals (planar faces produced by
    # extrude_pocket), the in-plane dims are the two non-normal axes.
    axes = []
    if abs(nx) < 0.9:
        axes.append(face_span[0])
    if abs(ny) < 0.9:
        axes.append(face_span[1])
    if abs(nz) < 0.9:
        axes.append(face_span[2])

    if not axes:
        return depth, 0.0, 0.0
    min_in_plane = min(axes)
    max_in_plane = max(axes)
    return depth, min_in_plane, max_in_plane


@skill(
    name="pocket_aspect_ratio_check",
    category="inspect",
    level="atomic",
    summary="For each detected pocket compute depth / min_in_plane_dimension "
            "and flag pockets above the threshold. Tool-independent — answers "
            "'is the pocket geometry itself too deep for its width?'. "
            "Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["pocket_aspect_report"],
    preserves=["body_topology"],
    manufacturing={
        "cnc_3axis": {"extras": {"max_aspect_ratio": "args.max_aspect_ratio"}},
    },
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class PocketAspectRatioCheck(SkillBase):
    class Args(BaseModel):
        max_aspect_ratio: float = Field(
            default=4.0, gt=0.0, le=20.0,
            description="depth/min_dimension threshold; pockets above are "
                        "flagged for redesign or alternative process.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces
        from phone_designer.skills.inspect.find_features import _detect_pocket

        shape = _occt_shape(body)
        faces = _all_faces(shape)
        body_bbox = _body_bbox(shape)

        violations: list[dict[str, Any]] = []
        pocket_count = 0

        for idx, f in enumerate(faces):
            feat = _detect_pocket(f, body_bbox, faces)
            if feat is None:
                continue
            pocket_count += 1
            depth, min_dim, _max_dim = _pocket_dimensions(f, body_bbox)
            if min_dim <= 1e-6:
                continue
            ratio = depth / min_dim
            if ratio > args.max_aspect_ratio:
                if ratio > args.max_aspect_ratio * 2.0:
                    rec = (
                        "Pocket far exceeds aspect ratio — consider EDM, "
                        "redesign as a through-cut, or split into two setups."
                    )
                else:
                    rec = (
                        "Widen pocket or reduce depth; alternatively switch "
                        "to a long-flute end mill with stub-length first pass."
                    )
                violations.append({
                    "pocket_id": idx,
                    "ratio": round(ratio, 4),
                    "depth_mm": round(depth, 4),
                    "min_dimension_mm": round(min_dim, 4),
                    "location": feat["center"],
                    "recommendation": rec,
                })

        extras = {
            "max_aspect_ratio": args.max_aspect_ratio,
            "pocket_count": pocket_count,
            "pocket_aspect_violations": violations,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
