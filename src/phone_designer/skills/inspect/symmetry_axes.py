"""symmetry_axes — atomic, read-only.

body 가 X / Y / Z 평면에 대해 (근사) 대칭인지 검사. 휴리스틱:
    각 face 의 center 를 candidate plane 으로 반사(mirror)했을 때, body 내부에
    같은 area 의 face 가 ``tolerance_mm`` 안에 있는가? 90% 이상 face 가
    그런 partner 를 가지면 그 축에 대해 대칭으로 판단.

각 candidate ("X" / "Y" / "Z") 의 의미:
    - "X" → x = body_center_x 평면을 가로지른 대칭 (x ↔ 반사)
    - "Y" → y = body_center_y 평면
    - "Z" → z = body_center_z 평면

result.extras schema:
    {"symmetric_axes": ["X","Z",...],
     "scores": {"X": 0.95, "Y": 0.3, "Z": 1.0},
     "tolerance_mm": float,
     "body_center": [x,y,z]}
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


AxisLit = Literal["X", "Y", "Z"]


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _body_center(shape) -> tuple[float, float, float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        try:
            BRepBndLib.Add_s(shape, bb)
        except Exception:
            return (0.0, 0.0, 0.0)
    if bb.IsVoid():
        return (0.0, 0.0, 0.0)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)


def _mirror(c: tuple[float, float, float],
            axis: AxisLit,
            origin: tuple[float, float, float]) -> tuple[float, float, float]:
    if axis == "X":
        return (2 * origin[0] - c[0], c[1], c[2])
    if axis == "Y":
        return (c[0], 2 * origin[1] - c[1], c[2])
    return (c[0], c[1], 2 * origin[2] - c[2])


def _score_axis(
    face_infos: list[tuple[tuple[float, float, float], float]],
    axis: AxisLit,
    body_center: tuple[float, float, float],
    tol_pos: float,
    tol_area_rel: float = 0.1,
) -> float:
    """face_infos = [(center, area), ...]. axis 에 대한 대칭성 점수 (0..1)."""
    if not face_infos:
        return 0.0
    n = len(face_infos)
    matched = 0
    for (c, a) in face_infos:
        mc = _mirror(c, axis, body_center)
        # face 가 본인이 대칭평면 위에 있으면 자기 자신과 매칭 (mc ≈ c)
        for (c2, a2) in face_infos:
            d = ((c2[0] - mc[0]) ** 2
                 + (c2[1] - mc[1]) ** 2
                 + (c2[2] - mc[2]) ** 2) ** 0.5
            if d > tol_pos:
                continue
            # area 비교 — 둘 중 하나가 0 이면 area=0 인 face 무시
            denom = max(a, a2, 1e-9)
            if abs(a - a2) / denom > tol_area_rel:
                continue
            matched += 1
            break
    return matched / n


@skill(
    name="symmetry_axes",
    category="inspect",
    level="atomic",
    summary="Heuristically detect approximate symmetry planes of the body "
            "(X = x_center plane, etc.). Returns axes where ≥90% of faces "
            "have a mirror partner. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["symmetry_summary"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class SymmetryAxes(SkillBase):
    class Args(BaseModel):
        axis_candidates: list[AxisLit] = Field(
            default_factory=lambda: ["X", "Y", "Z"]
        )
        tolerance_mm: float = Field(default=0.5, gt=0, le=100.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            _all_faces, _face_area, _face_center,
        )

        shape = _occt_shape(body)
        faces = _all_faces(shape)
        bc = _body_center(shape)

        face_infos: list[tuple[tuple[float, float, float], float]] = []
        for f in faces:
            try:
                c = _face_center(f)
            except Exception:
                continue
            try:
                a = _face_area(f)
            except Exception:
                a = 0.0
            face_infos.append((c, a))

        scores: dict[str, float] = {}
        symmetric: list[str] = []
        for ax in args.axis_candidates:
            s = _score_axis(face_infos, ax, bc, tol_pos=args.tolerance_mm)
            scores[ax] = round(s, 4)
            if s >= 0.9:
                symmetric.append(ax)

        extras = {
            "symmetric_axes": symmetric,
            "scores": scores,
            "tolerance_mm": args.tolerance_mm,
            "body_center": [round(c, 4) for c in bc],
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
