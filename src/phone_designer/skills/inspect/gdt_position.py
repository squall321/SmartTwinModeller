"""gdt_position — atomic, read-only.

GD&T position (true position) tolerance check for a hole axis. The matched
face must be cylindrical (hole or boss surface). The actual XY position of
the hole axis is compared against the nominal ``target_xy`` and the radial
deviation is computed:

    deviation_mm = sqrt((axis_x − target_x)² + (axis_y − target_y)²)

ASME Y14.5 convention: the position tolerance value is a diameter, so a hole
passes if ``2 × deviation_mm ≤ tolerance_mm``. ``tolerance_mm`` here is the
DIAMETRAL position-tolerance zone (the standard interpretation). We also
report ``deviation_mm`` (radial) for clarity.

Strategy:
    1. Resolve face_selector → first cylindrical face.
    2. Pull analytic cylinder axis (origin + direction) from OCCT.
    3. Project axis origin to z = target_z plane (or use as-is for XY only).
    4. Compute radial offset in XY plane vs target.

extras schema:
    {"position": {
        "deviation_mm": float,           # radial XY offset
        "diametral_zone_mm": float,      # 2 * radial offset
        "pass": bool,
        "tolerance_mm": float,
        "actual_xy": [x,y],
        "target_xy": [x,y],
        "axis_direction": [x,y,z]
     }}
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


def _cylinder_axis(face) -> tuple[tuple[float, float, float],
                                    tuple[float, float, float],
                                    float]:
    """Extract (axis_origin, axis_dir_unit, radius) from a cylindrical face.

    Raises ValueError if face is not a cylinder.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cylinder:
        raise ValueError(
            "gdt_position requires a cylindrical face (the hole/boss surface)"
        )
    cyl = surf.Cylinder()
    ax = cyl.Axis().Direction()
    loc = cyl.Location()
    r = float(cyl.Radius())
    return (
        (loc.X(), loc.Y(), loc.Z()),
        (ax.X(), ax.Y(), ax.Z()),
        r,
    )


@skill(
    name="gdt_position",
    category="inspect",
    level="atomic",
    summary="GD&T true-position: actual hole-axis XY vs target_xy. Deviation "
            "is radial offset; diametral zone = 2 × offset, compared to "
            "tolerance_mm (ASME Y14.5 diameter zone). Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["gdt_position_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class GdtPosition(SkillBase):
    class Args(BaseModel):
        face_selector: dict
        target_xy: tuple[float, float]
        tolerance_mm: float = Field(default=0.1, ge=0.0,
                                     description="Diametral position-tolerance zone (ASME Y14.5).")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_faces

        shape = _occt_shape(body)
        sel = _coerce_selector(args.face_selector)
        faces = resolve_faces(shape, sel, body=body)
        if not faces:
            raise ValueError(
                f"gdt_position: face_selector matched 0 faces (kind={sel.kind})"
            )
        face = faces[0]

        origin, axis, radius = _cylinder_axis(face)
        ox, oy, _oz = origin
        tx, ty = float(args.target_xy[0]), float(args.target_xy[1])

        dx = ox - tx
        dy = oy - ty
        # If the axis is not vertical (Z-aligned), we still report XY offset at
        # the face's location point — this is the standard "actual position" of
        # the axis at its defining anchor, which OCCT gives us.
        radial = math.sqrt(dx * dx + dy * dy)
        diametral = 2.0 * radial
        passed = bool(diametral <= args.tolerance_mm)

        extras = {
            "position": {
                "deviation_mm": round(radial, 6),
                "diametral_zone_mm": round(diametral, 6),
                "pass": passed,
                "tolerance_mm": float(args.tolerance_mm),
                "actual_xy": [round(ox, 6), round(oy, 6)],
                "target_xy": [tx, ty],
                "axis_direction": [round(c, 6) for c in axis],
                "hole_radius_mm": round(radius, 6),
            }
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
