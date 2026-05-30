"""auto_dimension — atomic, read-only.

Detect "key" dimensions of a body that a drafter or LLM would otherwise have
to enumerate by hand:

  * overall bbox (length / width / height)
  * minimum wall thickness (delegates to ``inspect_wall_thickness``)
  * main hole spacings (pairwise distances between cylindrical hole axes)
  * shaft / hole diameters and (cylinder) heights

Each dimension is reported as a row::

    {"name": "overall_length_mm", "value": 80.0, "unit": "mm",
     "tolerance": "±0.1", "kind": "bbox"}

A simple default tolerance scheme is applied (±0.1 for sub-millimetric care
features, ±0.05 for diameters of holes/shafts, ±0.2 for overall envelope) so
the consumer can render a quick drawing without engineering judgement.

Read-only — ``body_present`` post-condition.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel

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


def _cylinder_features(shape) -> list[dict[str, Any]]:
    """Return per-cylindrical-face info: axis dir, axis_origin, radius, height.

    Height is approximated as the bbox extent of the face along its own axis.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    from phone_designer.skills._resolvers import _all_faces, _face_center

    out: list[dict[str, Any]] = []
    for f in _all_faces(shape):
        try:
            surf = BRepAdaptor_Surface(f)
            if surf.GetType() != GeomAbs_Cylinder:
                continue
            cyl = surf.Cylinder()
        except Exception:
            continue
        try:
            axis = cyl.Axis().Direction()
            loc = cyl.Location()
            r = float(cyl.Radius())
        except Exception:
            continue

        # Estimate cylinder face height by projecting bbox onto its axis.
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        bb = Bnd_Box()
        try:
            BRepBndLib.Add_s(f, bb)
        except Exception:
            pass
        if bb.IsVoid():
            height = 0.0
        else:
            xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
            # project diagonals onto axis
            corners = [
                (xmin, ymin, zmin), (xmax, ymin, zmin),
                (xmin, ymax, zmin), (xmax, ymax, zmin),
                (xmin, ymin, zmax), (xmax, ymin, zmax),
                (xmin, ymax, zmax), (xmax, ymax, zmax),
            ]
            ax = (axis.X(), axis.Y(), axis.Z())
            projs = [c[0] * ax[0] + c[1] * ax[1] + c[2] * ax[2] for c in corners]
            height = float(max(projs) - min(projs))

        try:
            center = _face_center(f)
        except Exception:
            center = (loc.X(), loc.Y(), loc.Z())

        out.append({
            "radius_mm": r,
            "height_mm": height,
            "axis": (axis.X(), axis.Y(), axis.Z()),
            "axis_origin": (loc.X(), loc.Y(), loc.Z()),
            "center": center,
        })
    return out


def _line_distance(o1, d1, o2, d2) -> float:
    """Closest distance between two infinite lines in 3D."""
    # cross d1 x d2
    cx = d1[1] * d2[2] - d1[2] * d2[1]
    cy = d1[2] * d2[0] - d1[0] * d2[2]
    cz = d1[0] * d2[1] - d1[1] * d2[0]
    cmag = math.sqrt(cx * cx + cy * cy + cz * cz)
    if cmag < 1e-9:
        # parallel — perpendicular distance from o2 to line through o1
        w = (o2[0] - o1[0], o2[1] - o1[1], o2[2] - o1[2])
        # |w - (w·d1)d1|
        wd = w[0] * d1[0] + w[1] * d1[1] + w[2] * d1[2]
        px = w[0] - wd * d1[0]
        py = w[1] - wd * d1[1]
        pz = w[2] - wd * d1[2]
        return math.sqrt(px * px + py * py + pz * pz)
    w = (o2[0] - o1[0], o2[1] - o1[1], o2[2] - o1[2])
    return abs(w[0] * cx + w[1] * cy + w[2] * cz) / cmag


@skill(
    name="auto_dimension",
    category="inspect",
    level="atomic",
    summary="Detect key dimensions automatically (overall bbox, wall "
            "thicknesses, hole spacings, shaft/hole diameters and heights). "
            "Each entry has name/value/unit/tolerance. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["key_dimensions"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="body_present")],
)
class AutoDimension(SkillBase):
    class Args(BaseModel):
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
        shape = _occt_shape(body)
        (mn, mx) = _bbox(shape)
        L = mx[0] - mn[0]
        W = mx[1] - mn[1]
        H = mx[2] - mn[2]

        key: list[dict[str, Any]] = []

        # overall envelope
        key.append({"name": "overall_length_mm", "value": round(L, 4),
                    "unit": "mm", "tolerance": "±0.2", "kind": "bbox"})
        key.append({"name": "overall_width_mm", "value": round(W, 4),
                    "unit": "mm", "tolerance": "±0.2", "kind": "bbox"})
        key.append({"name": "overall_height_mm", "value": round(H, 4),
                    "unit": "mm", "tolerance": "±0.2", "kind": "bbox"})

        # wall thickness — delegate to inspect_wall_thickness for the
        # global minimum / mean. Best-effort; ignore failures.
        try:
            from phone_designer.skills.inspect.inspect_wall_thickness import (
                InspectWallThickness,
            )
            wt = InspectWallThickness().apply(body, {"samples_per_face": 16})
            rep = wt.extras.get("wall_thickness", {})
            gmin = float(rep.get("global_min", 0.0) or 0.0)
            gmean = float(rep.get("global_mean", 0.0) or 0.0)
            if gmin > 0:
                key.append({
                    "name": "min_wall_thickness_mm",
                    "value": round(gmin, 4),
                    "unit": "mm",
                    "tolerance": "±0.1",
                    "kind": "wall",
                })
            if gmean > 0:
                key.append({
                    "name": "mean_wall_thickness_mm",
                    "value": round(gmean, 4),
                    "unit": "mm",
                    "tolerance": "±0.1",
                    "kind": "wall",
                })
        except Exception:
            pass

        # cylinders → diameters, heights, pairwise axis distances
        try:
            cyls = _cylinder_features(shape)
        except Exception:
            cyls = []

        # Dedup cylinders by (round(radius, 3), round axis_origin to 0.1).
        seen: set[tuple[float, float, float, float, float]] = set()
        unique: list[dict[str, Any]] = []
        for c in cyls:
            key_t = (
                round(c["radius_mm"], 3),
                round(c["axis_origin"][0], 1),
                round(c["axis_origin"][1], 1),
                round(c["axis_origin"][2], 1),
                round(abs(c["axis"][0]) + abs(c["axis"][1]) * 2
                      + abs(c["axis"][2]) * 4, 2),
            )
            if key_t in seen:
                continue
            seen.add(key_t)
            unique.append(c)

        for i, c in enumerate(unique):
            d = 2.0 * c["radius_mm"]
            key.append({
                "name": f"hole_{i}_diameter_mm",
                "value": round(d, 4),
                "unit": "mm",
                "tolerance": "±0.05",
                "kind": "diameter",
                "axis_origin": [round(v, 4) for v in c["axis_origin"]],
                "axis": [round(v, 4) for v in c["axis"]],
            })
            if c["height_mm"] > 0:
                key.append({
                    "name": f"hole_{i}_length_mm",
                    "value": round(c["height_mm"], 4),
                    "unit": "mm",
                    "tolerance": "±0.1",
                    "kind": "length",
                })

        # pairwise axis-to-axis spacing
        for i in range(len(unique)):
            for j in range(i + 1, len(unique)):
                a = unique[i]
                b = unique[j]
                try:
                    dist = _line_distance(
                        a["axis_origin"], a["axis"],
                        b["axis_origin"], b["axis"],
                    )
                except Exception:
                    continue
                if dist < 1e-6:
                    continue
                key.append({
                    "name": f"hole_spacing_{i}_{j}_mm",
                    "value": round(dist, 4),
                    "unit": "mm",
                    "tolerance": "±0.1",
                    "kind": "spacing",
                })

        extras = {
            "key_dimensions": key,
            "bbox": {
                "min": [round(c, 4) for c in mn],
                "max": [round(c, 4) for c in mx],
                "size": [round(L, 4), round(W, 4), round(H, 4)],
            },
            "cylinder_count": len(unique),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
