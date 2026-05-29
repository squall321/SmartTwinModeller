"""Draft angle 검사.

각 planar face 의 normal 과 pull direction 사이 각도. min_draft 이상이어야.
"""
from __future__ import annotations

import math
from typing import Literal

from phone_designer.manufacturing.dfm.report import (
    DFMSeverity,
    DFMViolation,
)


def draft_violations(
    shape,
    *,
    min_draft_deg: float,
    process_code: str,
    pull_direction: tuple[float, float, float] = (0, 0, 1),
) -> list[DFMViolation]:
    """planar face 들의 draft 각도 검사 — pull_direction 과 90°+min_draft 이내.

    Args:
        shape: TopoDS_Shape
        min_draft_deg: 최소 draft 각도 (보통 1°)
        process_code: report
        pull_direction: mold 의 pull 방향 (예: +Z)
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.GProp import GProp_GProps

    from phone_designer.skills._resolvers import _all_faces

    violations: list[DFMViolation] = []
    dx, dy, dz = pull_direction
    dnorm = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dnorm < 1e-9:
        return violations
    dn = (dx / dnorm, dy / dnorm, dz / dnorm)

    for face in _all_faces(shape):
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() != GeomAbs_Plane:
            # 곡면은 draft 자동 안전 (단 cylindrical 측면이 pull dir 평행이면 draft=0)
            continue

        pl = surf.Plane()
        n = pl.Axis().Direction()
        nx, ny, nz = n.X(), n.Y(), n.Z()
        if face.Orientation() == 1:    # reversed
            nx, ny, nz = -nx, -ny, -nz

        # face center
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        c = props.CentreOfMass()

        # angle between face normal and pull_direction
        cosang = nx * dn[0] + ny * dn[1] + nz * dn[2]
        cosang = max(-1.0, min(1.0, cosang))
        angle_to_pull = math.degrees(math.acos(cosang))

        # face 가 pull dir 와 거의 평행 (angle ≈ 90°) → draft 측정 의미. 그 외 OK.
        # pull_axis face = pull dir 와 평행 → draft = 90 - angle_to_pull
        # 만약 angle_to_pull 가 60..90, 그러면 draft = 90 - 60 .. 0
        if angle_to_pull > 85.0 and angle_to_pull < 95.0:
            # 거의 vertical (pull dir 와 90° 근처). 이 face 가 mold side wall.
            # draft = 90 - angle_to_pull (가까울수록 draft 작음)
            draft = abs(90.0 - angle_to_pull)
            if draft < min_draft_deg:
                violations.append(DFMViolation(
                    kind="draft",
                    severity=DFMSeverity.VIOLATE,
                    process=process_code,
                    message=(f"face draft {draft:.2f}° < required "
                              f"{min_draft_deg:.2f}° (pull={pull_direction})"),
                    location=(c.X(), c.Y(), c.Z()),
                    measured_value=round(draft, 2),
                    required_value=min_draft_deg,
                    confidence=0.95,
                ))

    return violations
