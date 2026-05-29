"""hole_alignment_check — atomic, read-only.

Find every cylindrical face on the body, treat each as a "hole" (or boss
shaft), and group them by parallel axis. Within each parallel-axis group,
check coaxiality: do all holes share the same axis line (within
``position_tolerance_mm``)?

LLM agent 가 "이 body 의 hole 들이 일렬로 정렬됐냐 / through-hole 이 정말
관통하냐" 같은 검사를 할 때 사용. 또한 manufacturing DFM 의 "drill 한 번에
양면 가공 가능" 체크에 사용 가능.

알고리즘:
    1. 모든 face 중 cylindrical 만 collect — center, axis dir, radius 저장.
    2. axis 방향을 normalize 한 뒤 +/- 부호를 통일 (절댓값 큰 성분이 +).
    3. axis_tolerance_deg 내에서 parallel 한 cylinders 를 group 으로 묶음.
    4. 각 group 안에서 "axis line distance" 계산:
       point-to-line distance = |(center - ref_center) × axis_dir|
       모든 cylinder 가 동일 line 위 (position_tolerance_mm 내) 면 group
       자체가 coaxial. group 의 모든 hole 에 ``is_coaxial=True``.

extras schema:
    {"hole_count": int,
     "group_count": int,
     "groups": [
        {"axis": [x,y,z], "axis_origin": [x,y,z],
         "is_group_coaxial": bool,
         "holes": [
            {"center": [x,y,z], "radius_mm": float, "axis": [x,y,z],
             "axis_origin": [x,y,z], "is_coaxial": bool,
             "distance_from_axis_mm": float},
            ...
         ]},
        ...
     ]}

body 는 변경하지 않는다 (post ``body_present``).
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


def _normalize_axis(ax: tuple[float, float, float]) -> tuple[float, float, float]:
    """Unit-length axis, with deterministic sign (largest |component| → +)."""
    mag = math.sqrt(ax[0] ** 2 + ax[1] ** 2 + ax[2] ** 2)
    if mag < 1e-12:
        return (0.0, 0.0, 1.0)
    n = (ax[0] / mag, ax[1] / mag, ax[2] / mag)
    abs_vals = [abs(n[0]), abs(n[1]), abs(n[2])]
    i = abs_vals.index(max(abs_vals))
    if n[i] < 0:
        n = (-n[0], -n[1], -n[2])
    return n


def _collect_cylinders(shape) -> list[dict[str, Any]]:
    """모든 cylindrical face → dict list (axis, center, radius, axis_origin)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    from phone_designer.skills._resolvers import _all_faces, _face_center

    holes: list[dict[str, Any]] = []
    for f in _all_faces(shape):
        try:
            surf = BRepAdaptor_Surface(f)
            if surf.GetType() != GeomAbs_Cylinder:
                continue
            cyl = surf.Cylinder()
            ax = cyl.Axis().Direction()
            loc = cyl.Location()
            r = float(cyl.Radius())
            axis_raw = (ax.X(), ax.Y(), ax.Z())
            axis_n = _normalize_axis(axis_raw)
            c = _face_center(f)
        except Exception:
            continue
        holes.append({
            "center": c,
            "radius_mm": r,
            "axis": axis_n,
            "axis_origin": (loc.X(), loc.Y(), loc.Z()),
        })
    return holes


def _point_line_distance(
    p: tuple[float, float, float],
    line_origin: tuple[float, float, float],
    line_dir: tuple[float, float, float],
) -> float:
    """Distance from point p to infinite line (origin + t*dir, |dir|=1)."""
    dx = p[0] - line_origin[0]
    dy = p[1] - line_origin[1]
    dz = p[2] - line_origin[2]
    # cross product magnitude (since |dir|=1)
    cx = dy * line_dir[2] - dz * line_dir[1]
    cy = dz * line_dir[0] - dx * line_dir[2]
    cz = dx * line_dir[1] - dy * line_dir[0]
    return math.sqrt(cx * cx + cy * cy + cz * cz)


def _group_holes(
    holes: list[dict[str, Any]], axis_tol_deg: float,
) -> list[list[dict[str, Any]]]:
    """Group holes whose axes are parallel within ``axis_tol_deg``."""
    cos_tol = math.cos(math.radians(axis_tol_deg))
    groups: list[list[dict[str, Any]]] = []
    for h in holes:
        placed = False
        for g in groups:
            ref = g[0]["axis"]
            dot = (ref[0] * h["axis"][0]
                   + ref[1] * h["axis"][1]
                   + ref[2] * h["axis"][2])
            if dot >= cos_tol:
                g.append(h)
                placed = True
                break
        if not placed:
            groups.append([h])
    return groups


@skill(
    name="hole_alignment_check",
    category="inspect",
    level="atomic",
    summary="Find all cylindrical faces, group by parallel axes, and check "
            "coaxiality within each group. Returns per-group "
            "alignment + per-hole distance-from-axis. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["hole_alignment_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class HoleAlignmentCheck(SkillBase):
    class Args(BaseModel):
        axis_tolerance_deg: float = Field(default=1.0, ge=0.0, le=90.0)
        position_tolerance_mm: float = Field(default=0.5, ge=0.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        shape = _occt_shape(body)

        holes = _collect_cylinders(shape)
        groups = _group_holes(holes, args.axis_tolerance_deg)

        out_groups: list[dict[str, Any]] = []
        for g in groups:
            # Reference axis = first hole's axis. Reference origin = first
            # hole's axis_origin (point on the cylinder axis line).
            ref_axis = g[0]["axis"]
            ref_origin = g[0]["axis_origin"]

            hole_rows: list[dict[str, Any]] = []
            group_coaxial = True
            for h in g:
                d = _point_line_distance(h["axis_origin"], ref_origin, ref_axis)
                is_coax = d <= args.position_tolerance_mm
                if not is_coax:
                    group_coaxial = False
                hole_rows.append({
                    "center": [round(c, 4) for c in h["center"]],
                    "radius_mm": round(h["radius_mm"], 4),
                    "axis": [round(c, 4) for c in h["axis"]],
                    "axis_origin": [round(c, 4) for c in h["axis_origin"]],
                    "distance_from_axis_mm": round(d, 4),
                    "is_coaxial": bool(is_coax),
                })

            out_groups.append({
                "axis": [round(c, 4) for c in ref_axis],
                "axis_origin": [round(c, 4) for c in ref_origin],
                "is_group_coaxial": bool(group_coaxial),
                "holes": hole_rows,
            })

        extras = {
            "hole_count": len(holes),
            "group_count": len(out_groups),
            "groups": out_groups,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
