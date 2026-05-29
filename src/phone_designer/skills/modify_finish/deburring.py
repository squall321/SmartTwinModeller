"""deburring — atomic. 자동 sharp-corner 검출 + 미세 fillet.

Final finish 단계 직전, 모든 sharp internal/external corner 를 자동으로
찾아 작은 R fillet 을 건다. final_fillet 보다 보수적 — 인접 면의 dihedral
deviation 이 명확히 sharp (>60°) 인 edge 만 대상으로 한다.

판정:
    edge 의 두 인접 face 의 normal 사이 각도 deviation 이 60° 초과면 sharp.
    (deviation = 0° = 면 동일 평면, 90° = 직각 corner, 180° = 면 끝.)

OCCT 한계 회피:
    - 한 edge 가 일괄 fillet maker 에서 실패해도 다른 edge 는 살린다.
    - 한 edge 도 처리 못 하면 PostConditionError 가 아니라 정상 no-op (
      allow_no_change=True). 즉 "처리할 sharp corner 가 0개" 는 fail 이 아니다.
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
    """edge 에 인접한 face 들 (IsSame 비교, 최대 2개)."""
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    from phone_designer.skills._resolvers import _all_faces

    out = []
    seen_hashes = set()
    for f in _all_faces(shape):
        fit = TopExp_Explorer(f, TopAbs_EDGE)
        found = False
        while fit.More():
            fe = TopoDS.Edge_s(fit.Current())
            if fe.IsSame(edge):
                found = True
                break
            fit.Next()
        if found:
            h = id(f)  # face is unique by IsSame already; id ok for dedup of py refs
            if h not in seen_hashes:
                seen_hashes.add(h)
                out.append(f)
                if len(out) >= 2:
                    break
    return out


def _face_normal_unit(face) -> tuple[float, float, float] | None:
    """face center 에서 단위 outward normal. 곡면이면 BRepLProp 으로 UV 중심에서."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.TopAbs import TopAbs_REVERSED

    try:
        surf = BRepAdaptor_Surface(face)
        u_mid = (surf.FirstUParameter() + surf.LastUParameter()) * 0.5
        v_mid = (surf.FirstVParameter() + surf.LastVParameter()) * 0.5
        # SLProps(surface, u, v, derivOrder, tol)
        slp = BRepLProp_SLProps(surf, u_mid, v_mid, 1, 1e-6)
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


def _edge_angle_deviation_deg(shape, edge) -> float | None:
    """Edge 의 두 인접 face 사이 dihedral 'deviation' 각도 (도 단위).

    deviation = angle between the two outward face normals.
        0°  → coplanar / smooth (tangent)
        90° → square corner (sharp)
       180° → face 가 서로 180° 마주봄 (개념적 극단)

    반환 None: 인접 face 2개 미만이거나 normal 계산 실패.
    """
    faces = _adjacent_faces(shape, edge)
    if len(faces) < 2:
        return None
    n0 = _face_normal_unit(faces[0])
    n1 = _face_normal_unit(faces[1])
    if n0 is None or n1 is None:
        return None
    dot = n0[0] * n1[0] + n0[1] * n1[1] + n0[2] * n1[2]
    # clamp
    if dot > 1.0:
        dot = 1.0
    if dot < -1.0:
        dot = -1.0
    return math.degrees(math.acos(dot))


@skill(
    name="deburring",
    category="modify/finish",
    level="atomic",
    summary="Auto-detect sharp internal/external corners (dihedral deviation > 60 deg) "
            "and apply a tiny fillet (≤ 0.2 mm). No-op if no sharp corners exist. "
            "Use as a conservative finish before final_fillet.",
    selector_kinds=[],
    history_rules={
        "deburred_edges":     HistoryRule.CONSUMED,
        "result_faces":       HistoryRule.GENERATED_NEW,
        "untouched_features": HistoryRule.MODIFIED_INHERIT,
    },
    produces_features=["deburr_fillet_faces"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_fillet_r_mm": 0.1},
        "die_cast_al":       {"min_fillet_r_mm": 0.3, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_fillet_r_mm": 0.2, "min_draft_deg": 0.5},
    },
    failure_modes=["fm.no_edges_processed"],
    cost_hint=0.15,
    # Body with no sharp corners is a legitimate no-op (already finished),
    # so allow_no_change=True.
    post_conditions=[PostCondition(kind="face_count_changed", allow_no_change=True)],
)
class Deburring(SkillBase):
    class Args(BaseModel):
        max_radius_mm: float = Field(
            default=0.2, ge=0.01, le=1.0,
            description="자동 deburr fillet 의 최대 R (작게 권장).",
        )
        min_deviation_deg: float = Field(
            default=60.0, ge=10.0, le=170.0,
            description="이 deviation 이상이면 sharp 로 간주.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        from phone_designer.skills._resolvers import _all_edges

        shape = body.wrapped if hasattr(body, "wrapped") else body

        # 1) sharp 후보 edge 추출
        sharp = []
        for e in _all_edges(shape):
            dev = _edge_angle_deviation_deg(shape, e)
            if dev is None:
                continue
            if dev >= args.min_deviation_deg:
                sharp.append(e)

        # No sharp corners → legitimate no-op (allow_no_change=True 가 보장).
        result_shape = shape
        ok_count = 0
        if sharp:
            # 2) bulk 시도 + individual fallback
            try:
                maker = BRepFilletAPI_MakeFillet(shape)
                for e in sharp:
                    maker.Add(args.max_radius_mm, e)
                maker.Build()
                if maker.IsDone():
                    result_shape = maker.Shape()
                    ok_count = len(sharp)
                else:
                    raise RuntimeError("bulk fillet IsDone=False")
            except Exception:
                ok_count = 0
                for e in sharp:
                    try:
                        one = BRepFilletAPI_MakeFillet(result_shape)
                        one.Add(args.max_radius_mm, e)
                        one.Build()
                        if one.IsDone():
                            result_shape = one.Shape()
                            ok_count += 1
                    except Exception:
                        continue

        history = EntityHistoryMap(
            rules={
                "deburred_edges":     HistoryRule.CONSUMED,
                "result_faces":       HistoryRule.GENERATED_NEW,
                "untouched_features": HistoryRule.MODIFIED_INHERIT,
            },
        )
        return SkillResult(body=Part(result_shape), history=history)
