"""gdt_parallelism — atomic, read-only.

GD&T parallelism tolerance check. Resolve two planar faces and measure the
angle between their normals. Parallelism deviation is the acute-angle
distance from 0° (perfectly parallel — same direction OR opposite directions
both count as parallel for GD&T purposes, so we use ``min(angle, 180−angle)``).
A pair passes if deviation ≤ ``tolerance_deg``.

extras schema:
    {"parallelism": {
        "angle_deg": float,           # raw measured angle (0..180)
        "deviation_deg": float,       # min(angle, 180 − angle)
        "pass": bool,
        "tolerance_deg": float,
        "normal_a": [x,y,z],
        "normal_b": [x,y,z]
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


def _planar_normal(face) -> tuple[float, float, float]:
    from phone_designer.skills._resolvers import _face_normal_at_center

    n = _face_normal_at_center(face)
    if n == (0.0, 0.0, 0.0):
        raise ValueError(
            "gdt_parallelism requires planar faces (curved face has no "
            "constant normal)"
        )
    return n


def _angle_deg(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(math.acos(dot))


@skill(
    name="gdt_parallelism",
    category="inspect",
    level="atomic",
    summary="GD&T parallelism: angle between two planar face normals vs 0° "
            "(or 180°, treated as equivalent). Deviation = min(angle, 180−angle). "
            "Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["gdt_parallelism_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class GdtParallelism(SkillBase):
    class Args(BaseModel):
        face_selector_a: dict
        face_selector_b: dict
        tolerance_deg: float = Field(default=0.5, ge=0.0, le=90.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_faces

        shape = _occt_shape(body)
        sel_a = _coerce_selector(args.face_selector_a)
        sel_b = _coerce_selector(args.face_selector_b)
        faces_a = resolve_faces(shape, sel_a, body=body)
        faces_b = resolve_faces(shape, sel_b, body=body)
        if not faces_a:
            raise ValueError(
                f"gdt_parallelism: face_selector_a matched 0 faces (kind={sel_a.kind})"
            )
        if not faces_b:
            raise ValueError(
                f"gdt_parallelism: face_selector_b matched 0 faces (kind={sel_b.kind})"
            )

        n_a = _planar_normal(faces_a[0])
        n_b = _planar_normal(faces_b[0])
        ang = _angle_deg(n_a, n_b)
        deviation = min(ang, 180.0 - ang)
        passed = bool(deviation <= args.tolerance_deg)

        extras = {
            "parallelism": {
                "angle_deg": round(ang, 6),
                "deviation_deg": round(deviation, 6),
                "pass": passed,
                "tolerance_deg": float(args.tolerance_deg),
                "normal_a": [round(c, 6) for c in n_a],
                "normal_b": [round(c, 6) for c in n_b],
            }
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
