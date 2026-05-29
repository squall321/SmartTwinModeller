"""sanding_pass — atomic. 모든 convex edge 에 균일 미세 fillet.

물리적으로 'sanding' (사포질) 을 흉내내어 외부 (convex) edge 만 살짝 둥글린다.
오목한 (concave) 모서리는 표면 마감 sanding 으로 변하지 않으므로 건너뛴다.

판정 (convex vs concave):
    edge 의 두 인접 face A, B 의 outward normal nA, nB 와 edge tangent t 로:
      cross = nA × t                   (face A 평면 안에서 edge 와 직각, B 쪽 향함)
      sign  = sign(cross · (-nB))      (또는 등가로 -sign(cross · nB))
    convex (외부 corner): sign > 0
    concave (내부 corner): sign < 0
    coplanar/tangent: |dot(nA, nB)| ≈ 1 → skip (이미 매끄러움)
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _adjacent_faces(shape, edge) -> list:
    """edge 에 인접한 face 들 (최대 2)."""
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    from phone_designer.skills._resolvers import _all_faces

    out = []
    for f in _all_faces(shape):
        fit = TopExp_Explorer(f, TopAbs_EDGE)
        while fit.More():
            fe = TopoDS.Edge_s(fit.Current())
            if fe.IsSame(edge):
                out.append(f)
                break
            fit.Next()
        if len(out) >= 2:
            break
    return out


def _face_normal_at(face, u, v) -> tuple[float, float, float] | None:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.TopAbs import TopAbs_REVERSED

    try:
        surf = BRepAdaptor_Surface(face)
        slp = BRepLProp_SLProps(surf, u, v, 1, 1e-6)
        if not slp.IsNormalDefined():
            return None
        n = slp.Normal()
        nx, ny, nz = n.X(), n.Y(), n.Z()
        if face.Orientation() == TopAbs_REVERSED:
            nx, ny, nz = -nx, -ny, -nz
        norm = math.sqrt(nx * nx + ny * ny + nz * nz)
        if norm < 1e-9:
            return None
        return (nx / norm, ny / norm, nz / norm)
    except Exception:
        return None


def _face_uv_center(face) -> tuple[float, float]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    surf = BRepAdaptor_Surface(face)
    return (
        (surf.FirstUParameter() + surf.LastUParameter()) * 0.5,
        (surf.FirstVParameter() + surf.LastVParameter()) * 0.5,
    )


def _edge_tangent_mid(edge) -> tuple[float, float, float] | None:
    """Edge 중간점에서의 단위 tangent. (BRepAdaptor_Curve.D1 — surface 무관, ok for free edges.)"""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.gp import gp_Pnt, gp_Vec

    try:
        adaptor = BRepAdaptor_Curve(edge)
        t0 = adaptor.FirstParameter()
        t1 = adaptor.LastParameter()
        tm = (t0 + t1) * 0.5
        p = gp_Pnt()
        v = gp_Vec()
        adaptor.D1(tm, p, v)
        vx, vy, vz = v.X(), v.Y(), v.Z()
        norm = math.sqrt(vx * vx + vy * vy + vz * vz)
        if norm < 1e-9:
            return None
        return (vx / norm, vy / norm, vz / norm)
    except Exception:
        return None


def _edge_midpoint(edge):
    """gp_Pnt at edge midpoint (param-mid)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.gp import gp_Pnt, gp_Vec

    adaptor = BRepAdaptor_Curve(edge)
    tm = (adaptor.FirstParameter() + adaptor.LastParameter()) * 0.5
    p = gp_Pnt()
    v = gp_Vec()
    adaptor.D1(tm, p, v)
    return p


def _is_convex_edge(shape, edge) -> bool:
    """edge 가 외부 (convex) corner 인가?

    판정: edge 의 중점 P 에서 두 인접 face 의 outward normal 의 음수 평균
    방향으로 epsilon 만큼 이동한 sample point 가 solid 의 INSIDE 면 convex.
    (직관: convex corner 는 안쪽으로 들어가면 solid; concave 는 바깥쪽으로
    빠지면 solid.)

    실패 / coplanar / 곡면 매끄러운 edge → False (sanding 대상 아님).
    """
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    faces = _adjacent_faces(shape, edge)
    if len(faces) < 2:
        return False
    u0, v0 = _face_uv_center(faces[0])
    u1, v1 = _face_uv_center(faces[1])
    nA = _face_normal_at(faces[0], u0, v0)
    nB = _face_normal_at(faces[1], u1, v1)
    if nA is None or nB is None:
        return False

    # 매끄러운 (tangent / coplanar) edge — sanding 대상 아님.
    dot_n = nA[0] * nB[0] + nA[1] * nB[1] + nA[2] * nB[2]
    if abs(dot_n) > 0.999:
        return False

    try:
        p = _edge_midpoint(edge)
    except Exception:
        return False

    # avg inward = -(nA + nB)/2 — sample point 가 solid 내부면 convex.
    eps = 0.005   # 5μm — OCCT linear tolerance 보다 크고 형상보다 훨씬 작음.
    avg_inx = -(nA[0] + nB[0]) * 0.5
    avg_iny = -(nA[1] + nB[1]) * 0.5
    avg_inz = -(nA[2] + nB[2]) * 0.5
    sample = gp_Pnt(
        p.X() + eps * avg_inx,
        p.Y() + eps * avg_iny,
        p.Z() + eps * avg_inz,
    )
    try:
        cls = BRepClass3d_SolidClassifier(shape)
        cls.Perform(sample, 1e-7)
        return cls.State() == TopAbs_IN
    except Exception:
        return False


@skill(
    name="sanding_pass",
    category="modify/finish",
    level="atomic",
    summary="Apply a uniform tiny fillet (default 0.1 mm) to ALL convex edges, "
            "as if sanding the outside corners. Concave edges and smooth/tangent "
            "edges are skipped. No-op if the body has no convex edges.",
    selector_kinds=[],
    history_rules={
        "convex_edges":   HistoryRule.CONSUMED,
        "result_faces":   HistoryRule.GENERATED_NEW,
        "concave_edges":  HistoryRule.MODIFIED_INHERIT,
    },
    produces_features=["sanding_fillet_faces"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_fillet_r_mm": 0.05},
        "die_cast_al":       {"min_fillet_r_mm": 0.1, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_fillet_r_mm": 0.1, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.no_edges_processed"],
    cost_hint=0.3,
    # If no convex edges (e.g., sphere/cylinder) — legitimate no-op.
    post_conditions=[PostCondition(kind="face_count_changed", allow_no_change=True)],
)
class SandingPass(SkillBase):
    class Args(BaseModel):
        radius_mm: float = Field(
            default=0.1, ge=0.01, le=0.5,
            description="모든 convex edge 에 일괄 적용할 미세 R (sanding 흉내).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        from phone_designer.skills._resolvers import _all_edges

        shape = body.wrapped if hasattr(body, "wrapped") else body

        convex = [e for e in _all_edges(shape) if _is_convex_edge(shape, e)]

        result_shape = shape
        ok_count = 0
        if convex:
            # bulk
            try:
                maker = BRepFilletAPI_MakeFillet(shape)
                for e in convex:
                    maker.Add(args.radius_mm, e)
                maker.Build()
                if maker.IsDone():
                    result_shape = maker.Shape()
                    ok_count = len(convex)
                else:
                    raise RuntimeError("bulk sanding fillet IsDone=False")
            except Exception:
                ok_count = 0
                for e in convex:
                    try:
                        one = BRepFilletAPI_MakeFillet(result_shape)
                        one.Add(args.radius_mm, e)
                        one.Build()
                        if one.IsDone():
                            result_shape = one.Shape()
                            ok_count += 1
                    except Exception:
                        continue

        history = EntityHistoryMap(
            rules={
                "convex_edges":   HistoryRule.CONSUMED,
                "result_faces":   HistoryRule.GENERATED_NEW,
                "concave_edges":  HistoryRule.MODIFIED_INHERIT,
            },
        )
        return SkillResult(body=Part(result_shape), history=history)
