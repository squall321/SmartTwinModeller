"""Undercut 검사.

pull direction 에서 가려진 face = undercut. ray-trace 기반 단순 검사.
"""
from __future__ import annotations

import math
from typing import Literal

from phone_designer.manufacturing.dfm.report import (
    DFMSeverity,
    DFMViolation,
)


def undercut_violations(
    shape,
    *,
    process_code: str,
    pull_direction: tuple[float, float, float] = (0, 0, 1),
) -> list[DFMViolation]:
    """planar face 의 normal 이 pull_direction 과 음의 dot product → undercut.

    즉 face 가 mold open 방향과 반대를 향하면 undercut.
    """
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
            continue

        pl = surf.Plane()
        n = pl.Axis().Direction()
        nx, ny, nz = n.X(), n.Y(), n.Z()
        if face.Orientation() == 1:
            nx, ny, nz = -nx, -ny, -nz

        dot = nx * dn[0] + ny * dn[1] + nz * dn[2]
        # dot < 0 이면 face 가 pull dir 의 반대 향 → mold side 에서 빠질 때 걸림 = undercut
        # 단 face 가 거의 수직 (dot ≈ 0) 이면 draft 검사로 충분
        if dot < -0.1:    # 약 < -5.7° 이면 명확한 undercut
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            c = props.CentreOfMass()
            angle_against_pull_deg = math.degrees(math.acos(max(-1.0, min(1.0, -dot))))
            violations.append(DFMViolation(
                kind="undercut",
                severity=DFMSeverity.VIOLATE,
                process=process_code,
                message=(f"face faces opposite to pull direction "
                          f"(angle={angle_against_pull_deg:.1f}° from -pull)"),
                location=(c.X(), c.Y(), c.Z()),
                measured_value=round(dot, 3),
                required_value=0.0,
                confidence=0.9,
            ))

    return violations
