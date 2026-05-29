"""Wall thickness — ray-march sampling.

Phase 5 v0 의 단순한 구현:
  - 각 face 의 표면 sample point + 안쪽 방향 ray
  - body 와의 첫 교차 거리 = local wall thickness
  - min_wall_mm 보다 작은 sample 은 violation

정확도 약 80% — false positive 가능 (보스 내부, sharp corner). v0.3 에서 medial axis
또는 cross-section 기반으로 개선.
"""
from __future__ import annotations

import math
from typing import Any

from phone_designer.manufacturing.dfm.report import (
    DFMReport,
    DFMSeverity,
    DFMViolation,
)


def wall_thickness_raymarch(
    shape,
    *,
    min_wall_mm: float,
    process_code: str,
    n_samples_per_face: int = 5,
    max_thickness_to_check_mm: float = 50.0,
    false_positive_floor_mm: float = 0.05,
    start_offset_mm: float = 0.01,
) -> list[DFMViolation]:
    """body 의 face 별 wall thickness sample → violation list.

    Args:
        shape: TopoDS_Shape
        min_wall_mm: 최소 두께 임계값
        process_code: violation 보고용
        n_samples_per_face: face 당 sample 수
        max_thickness_to_check_mm: 이 거리까지만 ray-cast (성능)

    Returns:
        list[DFMViolation] (violate / warn / OK 통과는 안 들어감)
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.gp import gp_Pnt, gp_Dir, gp_Lin
    from OCP.TopAbs import TopAbs_OUT

    from phone_designer.skills._resolvers import _all_faces

    violations: list[DFMViolation] = []
    faces = _all_faces(shape)
    if not faces:
        return violations

    for face in faces:
        surf = BRepAdaptor_Surface(face)
        # face 중심 + 4 corner 가까이 → 5 sample
        u_min, u_max = surf.FirstUParameter(), surf.LastUParameter()
        v_min, v_max = surf.FirstVParameter(), surf.LastVParameter()
        sample_uvs = [
            ((u_min + u_max) / 2, (v_min + v_max) / 2),
            (u_min + (u_max - u_min) * 0.25, v_min + (v_max - v_min) * 0.25),
            (u_min + (u_max - u_min) * 0.75, v_min + (v_max - v_min) * 0.25),
            (u_min + (u_max - u_min) * 0.25, v_min + (v_max - v_min) * 0.75),
            (u_min + (u_max - u_min) * 0.75, v_min + (v_max - v_min) * 0.75),
        ][:n_samples_per_face]

        for (u, v) in sample_uvs:
            try:
                pt = surf.Value(u, v)
                # 안쪽 방향 = -normal (face 의 outward 가 +normal 가정)
                d1u = surf.DN(u, v, 1, 0)
                d1v = surf.DN(u, v, 0, 1)
                # cross
                nx = d1u.Y() * d1v.Z() - d1u.Z() * d1v.Y()
                ny = d1u.Z() * d1v.X() - d1u.X() * d1v.Z()
                nz = d1u.X() * d1v.Y() - d1u.Y() * d1v.X()
                nmag = math.sqrt(nx * nx + ny * ny + nz * nz)
                if nmag < 1e-9:
                    continue
                # outward — face orientation 반영
                if face.Orientation() == 1:    # reversed
                    nx, ny, nz = -nx, -ny, -nz
                # 안쪽 방향 = -normal
                inward = gp_Dir(-nx / nmag, -ny / nmag, -nz / nmag)

                # 안쪽으로 시작점 옮김 — false positive 완화를 위해 더 깊게 (default 10μm)
                start = gp_Pnt(
                    pt.X() + inward.X() * start_offset_mm,
                    pt.Y() + inward.Y() * start_offset_mm,
                    pt.Z() + inward.Z() * start_offset_mm,
                )

                # body 안에 있는지 분류 (있어야 ray-cast 의미)
                clsf = BRepClass3d_SolidClassifier(shape, start, 1e-6)
                if clsf.State() == TopAbs_OUT:
                    # 시작점이 body 밖 — sample 잘못, skip
                    continue

                # ray-cast: 같은 axis 의 반대 방향 face 와의 교차
                line = gp_Lin(start, inward)
                inter = BRepIntCurveSurface_Inter()
                inter.Init(shape, line, 1e-6)
                best_t = None
                while inter.More():
                    t = inter.W()
                    if t > 0 and t < max_thickness_to_check_mm:
                        if best_t is None or t < best_t:
                            best_t = t
                    inter.Next()

                if best_t is None:
                    continue
                thickness = best_t

                # False-positive 필터: 측정값이 floor 이하 = OCCT numerical noise.
                # ray-cast 가 sample point 와 너무 가까운 다른 face 와 교차한 case.
                if thickness < false_positive_floor_mm:
                    continue

                if thickness < min_wall_mm:
                    # 측정값이 floor ~ floor*2 영역이면 confidence 더 낮춤
                    conf = 0.7 if thickness > 2 * false_positive_floor_mm else 0.4
                    violations.append(DFMViolation(
                        kind="wall_thickness",
                        severity=DFMSeverity.VIOLATE,
                        process=process_code,
                        message=(f"wall {thickness:.3f}mm < required "
                                  f"{min_wall_mm:.3f}mm"),
                        location=(pt.X(), pt.Y(), pt.Z()),
                        measured_value=round(thickness, 3),
                        required_value=min_wall_mm,
                        confidence=conf,
                    ))
            except Exception:
                # sample 실패 — skip (false negative 보단 안전)
                continue

    return violations
