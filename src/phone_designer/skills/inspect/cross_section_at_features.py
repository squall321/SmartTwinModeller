"""cross_section_at_features — atomic, read-only.

Automatically pick interesting cutting planes (bbox midplanes, planes through
hole axes, planes through pattern centroids) and run ``cross_section`` on
each. Useful for an LLM building a quick "drawing sheet" view of a part
without having to guess plane coordinates.

extras schema::

    {
      "sections": [
        {"plane_origin": [...], "plane_normal": [...],
         "wire_count": int, "bbox": {"min": [...], "max": [...], "size": [...]},
         "reason": "bbox_mid_X" | "hole_axis_*" | "pattern_centroid"},
        ...
      ],
      "section_count": int,
    }

The actual polylines from ``cross_section`` are intentionally NOT echoed back
(they can be large) — only summary stats are returned.
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
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        try:
            BRepBndLib.Add_s(shape, bb)
        except Exception:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    if bb.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def _polyline_bbox(polylines: list) -> dict[str, list[float]]:
    if not polylines:
        return {"min": [0.0, 0.0, 0.0],
                "max": [0.0, 0.0, 0.0],
                "size": [0.0, 0.0, 0.0]}
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for pl in polylines:
        for p in pl:
            xs.append(p[0])
            ys.append(p[1])
            zs.append(p[2])
    if not xs:
        return {"min": [0.0, 0.0, 0.0],
                "max": [0.0, 0.0, 0.0],
                "size": [0.0, 0.0, 0.0]}
    return {
        "min": [round(min(xs), 4), round(min(ys), 4), round(min(zs), 4)],
        "max": [round(max(xs), 4), round(max(ys), 4), round(max(zs), 4)],
        "size": [round(max(xs) - min(xs), 4),
                 round(max(ys) - min(ys), 4),
                 round(max(zs) - min(zs), 4)],
    }


def _cylinder_axes(shape) -> list[tuple[tuple[float, float, float],
                                         tuple[float, float, float]]]:
    """Return (origin, axis_dir) for each cylindrical face, deduped."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    from phone_designer.skills._resolvers import _all_faces

    out: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    seen: set[tuple[float, float, float, float, float, float]] = set()
    for f in _all_faces(shape):
        try:
            surf = BRepAdaptor_Surface(f)
            if surf.GetType() != GeomAbs_Cylinder:
                continue
            cyl = surf.Cylinder()
            axis = cyl.Axis().Direction()
            loc = cyl.Location()
        except Exception:
            continue
        o = (loc.X(), loc.Y(), loc.Z())
        a = (axis.X(), axis.Y(), axis.Z())
        key = (round(o[0], 1), round(o[1], 1), round(o[2], 1),
               round(a[0], 2), round(a[1], 2), round(a[2], 2))
        if key in seen:
            continue
        seen.add(key)
        out.append((o, a))
    return out


def _perp_basis(axis: tuple[float, float, float]) -> tuple[float, float, float]:
    """Return any unit vector perpendicular to ``axis``."""
    ax, ay, az = axis
    # pick a non-parallel reference
    if abs(ax) < 0.9:
        ref = (1.0, 0.0, 0.0)
    else:
        ref = (0.0, 1.0, 0.0)
    # n = axis × ref
    nx = ay * ref[2] - az * ref[1]
    ny = az * ref[0] - ax * ref[2]
    nz = ax * ref[1] - ay * ref[0]
    mag = math.sqrt(nx * nx + ny * ny + nz * nz)
    if mag < 1e-9:
        return (1.0, 0.0, 0.0)
    return (nx / mag, ny / mag, nz / mag)


@skill(
    name="cross_section_at_features",
    category="inspect",
    level="atomic",
    summary="Automatically select interesting cutting planes (bbox midplanes, "
            "planes through hole axes, planes through pattern centers) and "
            "summarise each cross section. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["auto_cross_sections"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="body_present")],
)
class CrossSectionAtFeatures(SkillBase):
    class Args(BaseModel):
        num_sections: int = Field(default=3, ge=1, le=50)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.inspect.cross_section import CrossSection

        shape = _occt_shape(body)
        (mn, mx) = _bbox(shape)
        center = ((mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2)

        # Build candidate planes (origin, normal, reason).
        candidates: list[tuple[tuple[float, float, float],
                               tuple[float, float, float], str]] = []

        # 1) bbox midplanes (X, Y, Z)
        candidates.append((center, (1.0, 0.0, 0.0), "bbox_mid_X"))
        candidates.append((center, (0.0, 1.0, 0.0), "bbox_mid_Y"))
        candidates.append((center, (0.0, 0.0, 1.0), "bbox_mid_Z"))

        # 2) cylindrical hole axes — plane perpendicular to axis at the
        #    axis origin, OR plane *containing* the axis (we use containing,
        #    since slicing perpendicular to a hole axis just shows the hole).
        try:
            cyls = _cylinder_axes(shape)
        except Exception:
            cyls = []
        for i, (o, a) in enumerate(cyls):
            # plane containing the axis: normal must be ⟂ to axis.
            n = _perp_basis(a)
            candidates.append((o, n, f"hole_axis_{i}"))

        # 3) pattern centroid — average of cylinder origins (if ≥ 2 holes).
        if len(cyls) >= 2:
            ox = sum(c[0][0] for c in cyls) / len(cyls)
            oy = sum(c[0][1] for c in cyls) / len(cyls)
            oz = sum(c[0][2] for c in cyls) / len(cyls)
            # cut perpendicular to dominant axis (use first hole's axis)
            candidates.append(((ox, oy, oz), cyls[0][1], "pattern_centroid"))

        # cap to num_sections
        candidates = candidates[: int(args.num_sections)]

        sections: list[dict[str, Any]] = []
        for (origin, normal, reason) in candidates:
            try:
                r = CrossSection().apply(body, {
                    "plane_origin": tuple(origin),
                    "plane_normal": tuple(normal),
                    "samples_per_edge": 20,
                })
                polylines = r.extras.get("polylines", [])
            except Exception:
                polylines = []
            sections.append({
                "plane_origin": [round(c, 4) for c in origin],
                "plane_normal": [round(c, 4) for c in normal],
                "wire_count": len(polylines),
                "bbox": _polyline_bbox(polylines),
                "reason": reason,
            })

        extras = {
            "sections": sections,
            "section_count": len(sections),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
