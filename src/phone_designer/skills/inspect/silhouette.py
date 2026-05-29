"""silhouette — atomic, read-only.

view_direction (camera→body) 으로부터 본 윤곽선 projection.

본 구현은 ``brute projection`` — 모든 edge 를 view plane (perpendicular to
view_direction) 위로 정사영하여 2D polyline 으로 반환. HLRBRep_Algo API 가
OCP 버전마다 시그니처가 다르고 setup 이 까다로워서, LLM hint 용도로는 모든
edge 를 보여주는 정사영이 더 안정적이다.

한계:
    - hidden vs visible edge 를 구분하지 않는다 — 모든 edge 가 그대로 나옴.
    - 같은 위치의 edge 들이 2D 에서 겹쳐 보일 수 있다.

result.extras schema:
    {"view_direction": [x,y,z],
     "polylines_2d": [[(u,v), (u,v), ...], ...],
     "extent_uv": [u_min, v_min, u_max, v_max]}
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


def _build_uv_basis(
    view_direction: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """view_direction 에 수직한 (u_axis, v_axis) unit basis 두 개 반환."""
    nx, ny, nz = view_direction
    n_len = math.sqrt(nx * nx + ny * ny + nz * nz)
    if n_len < 1e-12:
        raise ValueError("view_direction must be non-zero")
    nx, ny, nz = nx / n_len, ny / n_len, nz / n_len

    # 임의의 reference vector 가 view 와 평행하지 않도록 선택
    if abs(nz) < 0.9:
        ref = (0.0, 0.0, 1.0)
    else:
        ref = (1.0, 0.0, 0.0)

    # u = normalize( ref × n )  (단, n 은 view direction 의 반대 방향이 camera->scene
    # 이라도 결과는 같다 — 우리는 u,v 만 필요)
    ux = ref[1] * nz - ref[2] * ny
    uy = ref[2] * nx - ref[0] * nz
    uz = ref[0] * ny - ref[1] * nx
    u_len = math.sqrt(ux * ux + uy * uy + uz * uz)
    if u_len < 1e-12:
        # ref 가 n 과 평행 — 다른 ref 로
        ref = (0.0, 1.0, 0.0)
        ux = ref[1] * nz - ref[2] * ny
        uy = ref[2] * nx - ref[0] * nz
        uz = ref[0] * ny - ref[1] * nx
        u_len = math.sqrt(ux * ux + uy * uy + uz * uz)
    ux, uy, uz = ux / u_len, uy / u_len, uz / u_len

    # v = n × u
    vx = ny * uz - nz * uy
    vy = nz * ux - nx * uz
    vz = nx * uy - ny * ux

    return (ux, uy, uz), (vx, vy, vz)


def _project_point(
    p: tuple[float, float, float],
    u_axis: tuple[float, float, float],
    v_axis: tuple[float, float, float],
) -> tuple[float, float]:
    """3D 점을 (u, v) 로 정사영."""
    return (
        p[0] * u_axis[0] + p[1] * u_axis[1] + p[2] * u_axis[2],
        p[0] * v_axis[0] + p[1] * v_axis[1] + p[2] * v_axis[2],
    )


def _sample_edge_3d(edge, samples: int) -> list[tuple[float, float, float]]:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_UniformAbscissa

    curve = BRepAdaptor_Curve(edge)
    n = max(2, int(samples))
    pts: list[tuple[float, float, float]] = []
    try:
        algo = GCPnts_UniformAbscissa(curve, n)
        if algo.IsDone() and algo.NbPoints() >= 2:
            for i in range(1, algo.NbPoints() + 1):
                u = algo.Parameter(i)
                p = curve.Value(u)
                pts.append((p.X(), p.Y(), p.Z()))
            return pts
    except Exception:
        pts = []

    u_first = curve.FirstParameter()
    u_last = curve.LastParameter()
    if u_last <= u_first:
        return []
    for i in range(n):
        u = u_first + (u_last - u_first) * (i / (n - 1))
        p = curve.Value(u)
        pts.append((p.X(), p.Y(), p.Z()))
    return pts


@skill(
    name="silhouette",
    category="inspect",
    level="atomic",
    summary="Project all body edges onto the plane perpendicular to "
            "view_direction (camera → body). Returns 2D polylines per edge "
            "(brute projection — hidden/visible not distinguished). "
            "Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["silhouette_polylines_2d"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class Silhouette(SkillBase):
    class Args(BaseModel):
        view_direction: tuple[float, float, float] = (0.0, 0.0, -1.0)
        samples_per_edge: int = Field(default=20, ge=2, le=500)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_edges

        shape = _occt_shape(body)
        u_axis, v_axis = _build_uv_basis(args.view_direction)

        edges = _all_edges(shape)
        polylines: list[list[tuple[float, float]]] = []
        u_min = math.inf
        u_max = -math.inf
        v_min = math.inf
        v_max = -math.inf

        for e in edges:
            try:
                pts3d = _sample_edge_3d(e, args.samples_per_edge)
            except Exception:
                pts3d = []
            if not pts3d:
                continue
            pts2d = [_project_point(p, u_axis, v_axis) for p in pts3d]
            polylines.append([(round(uv[0], 4), round(uv[1], 4)) for uv in pts2d])
            for uv in pts2d:
                if uv[0] < u_min:
                    u_min = uv[0]
                if uv[0] > u_max:
                    u_max = uv[0]
                if uv[1] < v_min:
                    v_min = uv[1]
                if uv[1] > v_max:
                    v_max = uv[1]

        if not polylines:
            extent = [0.0, 0.0, 0.0, 0.0]
        else:
            extent = [round(u_min, 4), round(v_min, 4),
                      round(u_max, 4), round(v_max, 4)]

        extras = {
            "view_direction": [round(c, 4) for c in args.view_direction],
            "polylines_2d": polylines,
            "extent_uv": extent,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
