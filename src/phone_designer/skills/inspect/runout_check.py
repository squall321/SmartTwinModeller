"""runout_check — atomic, read-only.

GD&T circular & total runout (ISO 1101 / ASME Y14.5). Given a rotating
axis (typically the part's datum axis) and a surface selector:

  1. Sample N points on the matched face.
  2. Project each point onto the axis and compute its perpendicular
     distance r_i (the actual radius at that sample).
  3. For each AXIAL slice (sample binned by axial coordinate) compute
     ``r_max(slice) - r_min(slice)`` — that is the **circular runout**
     for that slice. The circular runout reported is the maximum across
     all slices (worst circular sweep).
  4. ``Total runout`` is the full FIM (Full Indicator Movement) across
     all samples: ``max(r_i) - min(r_i)``.

extras schema:
    {"runout": {
        "value_mm": float,        # = total (worst-case)
        "circular": float,        # max per-slice (r_max - r_min)
        "total": float,           # max(r) - min(r) across all samples
        "sample_count": int,
        "axis_origin": [x,y,z],
        "axis_direction": [x,y,z]
     }}

body unchanged (post ``body_present``).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _norm_axis(axis: tuple[float, float, float]) -> tuple[float, float, float]:
    mag = math.sqrt(axis[0] ** 2 + axis[1] ** 2 + axis[2] ** 2)
    if mag < 1e-12:
        raise ValueError("runout axis direction must be non-zero")
    return (axis[0] / mag, axis[1] / mag, axis[2] / mag)


def _sample_face(face, n_samples: int) -> list[tuple[float, float, float]]:
    """Sample a roughly sqrt(N)×sqrt(N) UV grid of 3D points on a face."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    adaptor = BRepAdaptor_Surface(face)
    u0, u1 = adaptor.FirstUParameter(), adaptor.LastUParameter()
    v0, v1 = adaptor.FirstVParameter(), adaptor.LastVParameter()
    side = max(2, int(math.ceil(math.sqrt(max(1, n_samples)))))
    pts: list[tuple[float, float, float]] = []
    for i in range(side):
        for j in range(side):
            fu = (i + 0.5) / side
            fv = (j + 0.5) / side
            u = u0 + (u1 - u0) * fu
            v = v0 + (v1 - v0) * fv
            try:
                p = adaptor.Value(u, v)
                pts.append((p.X(), p.Y(), p.Z()))
            except Exception:
                continue
    return pts


@skill(
    name="runout_check",
    category="inspect",
    level="atomic",
    summary="GD&T circular & total runout (ISO 1101) — sample points on a "
            "surface, project to a rotating axis, compute per-slice "
            "(r_max−r_min) and global FIM. Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["runout_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class RunoutCheck(SkillBase):
    class Args(BaseModel):
        rotating_axis: tuple[
            tuple[float, float, float], tuple[float, float, float]
        ] = Field(
            description="((origin_xyz), (direction_xyz))",
        )
        surface_selector: dict
        samples: int = Field(default=64, ge=8, le=10000)
        slice_count: int = Field(default=8, ge=2, le=64)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_faces

        shape = _occt_shape(body)
        sel = _coerce_selector(args.surface_selector)
        faces = resolve_faces(shape, sel, body=body)
        if not faces:
            raise ValueError(
                f"runout_check: surface_selector matched 0 faces "
                f"(kind={sel.kind})"
            )

        origin = (
            float(args.rotating_axis[0][0]),
            float(args.rotating_axis[0][1]),
            float(args.rotating_axis[0][2]),
        )
        axis_dir = _norm_axis((
            float(args.rotating_axis[1][0]),
            float(args.rotating_axis[1][1]),
            float(args.rotating_axis[1][2]),
        ))

        # Collect sample radii + axial coordinates across all matched faces.
        radii: list[float] = []
        axial: list[float] = []
        per_face_target = max(4, args.samples // len(faces))
        for f in faces:
            pts = _sample_face(f, per_face_target)
            for px, py, pz in pts:
                vx = px - origin[0]
                vy = py - origin[1]
                vz = pz - origin[2]
                # t = (v · axis_dir) → axial coordinate along axis
                t = vx * axis_dir[0] + vy * axis_dir[1] + vz * axis_dir[2]
                # perpendicular component
                rx = vx - t * axis_dir[0]
                ry = vy - t * axis_dir[1]
                rz = vz - t * axis_dir[2]
                r = math.sqrt(rx * rx + ry * ry + rz * rz)
                radii.append(r)
                axial.append(t)

        if not radii:
            raise ValueError("runout_check: no usable samples on selected surfaces")

        total = max(radii) - min(radii)

        # Bin samples by axial coordinate into ``slice_count`` slices.
        t_min, t_max = min(axial), max(axial)
        span = t_max - t_min
        if span < 1e-9:
            # All samples are at the same axial position → circular == total.
            circular = total
        else:
            slices: list[list[float]] = [[] for _ in range(args.slice_count)]
            for t, r in zip(axial, radii):
                idx = int((t - t_min) / span * args.slice_count)
                if idx >= args.slice_count:
                    idx = args.slice_count - 1
                slices[idx].append(r)
            slice_runouts = [
                (max(s) - min(s)) for s in slices if len(s) >= 2
            ]
            circular = max(slice_runouts) if slice_runouts else total

        extras = {
            "runout": {
                "value_mm": round(total, 6),
                "circular": round(circular, 6),
                "total": round(total, 6),
                "sample_count": len(radii),
                "axis_origin": [round(c, 6) for c in origin],
                "axis_direction": [round(c, 6) for c in axis_dir],
            }
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
