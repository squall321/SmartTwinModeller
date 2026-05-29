"""curvature_map — atomic, read-only.

For each face that matches the given selector, sample a UV grid and compute
local curvature (Gaussian / mean / principal) via OCCT ``BRepLProp_SLProps``.

LLM agent 가 "이 dome 의 곡률이 얼마인가 / 이 freeform face 가 균일 곡률인가"
같은 질문에 답할 때 사용. 또는 manufacturing DFM (예: min curvature radius
check) 의 입력으로.

extras schema:
    {
      "curvature_kind": "gaussian" | "mean" | "principal",
      "samples_per_face": int,
      "face_count": int,
      "faces": [
        {"index": i, "samples": [
            {"uv": [u, v], "xyz": [x, y, z],
             "gaussian": K, "mean": H,
             "min_curvature": k_min, "max_curvature": k_max},
            ...
        ]},
        ...
      ]
    }

body 는 변경하지 않는다 (post ``body_present``).
"""
from __future__ import annotations

import math
from typing import Any, Literal

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


def _uv_grid_count(samples_per_face: int) -> int:
    """samples_per_face → grid side length (round up sqrt)."""
    n = max(1, int(samples_per_face))
    side = int(math.ceil(math.sqrt(n)))
    return max(1, side)


def _sample_face(face, samples_per_face: int) -> list[dict[str, Any]]:
    """Sample UV grid on face and compute curvature at each point."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps

    adaptor = BRepAdaptor_Surface(face)
    u0 = adaptor.FirstUParameter()
    u1 = adaptor.LastUParameter()
    v0 = adaptor.FirstVParameter()
    v1 = adaptor.LastVParameter()

    side = _uv_grid_count(samples_per_face)
    samples: list[dict[str, Any]] = []
    tol = 1e-6

    for i in range(side):
        for j in range(side):
            # Center each cell — avoids degenerate parameter edges
            fu = (i + 0.5) / side
            fv = (j + 0.5) / side
            u = u0 + (u1 - u0) * fu
            v = v0 + (v1 - v0) * fv
            try:
                slp = BRepLProp_SLProps(adaptor, u, v, 2, tol)
                p = slp.Value()
                xyz = (p.X(), p.Y(), p.Z())
                if slp.IsCurvatureDefined():
                    gaussian = float(slp.GaussianCurvature())
                    mean = float(slp.MeanCurvature())
                    kmin = float(slp.MinCurvature())
                    kmax = float(slp.MaxCurvature())
                else:
                    gaussian = 0.0
                    mean = 0.0
                    kmin = 0.0
                    kmax = 0.0
            except Exception:
                xyz = (0.0, 0.0, 0.0)
                gaussian = 0.0
                mean = 0.0
                kmin = 0.0
                kmax = 0.0

            samples.append({
                "uv": [round(u, 6), round(v, 6)],
                "xyz": [round(c, 4) for c in xyz],
                "gaussian": round(gaussian, 8),
                "mean": round(mean, 8),
                "min_curvature": round(kmin, 8),
                "max_curvature": round(kmax, 8),
            })
    return samples


@skill(
    name="curvature_map",
    category="inspect",
    level="atomic",
    summary="Sample a UV grid on each matched face and compute local "
            "Gaussian / mean / principal curvature via BRepLProp_SLProps. "
            "Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["curvature_map"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class CurvatureMap(SkillBase):
    class Args(BaseModel):
        face_selector: dict
        samples_per_face: int = Field(default=25, ge=1, le=10000)
        curvature_kind: Literal["gaussian", "mean", "principal"] = "gaussian"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_faces

        shape = _occt_shape(body)
        sel = _coerce_selector(args.face_selector)
        faces = resolve_faces(shape, sel, body=body)

        face_entries: list[dict[str, Any]] = []
        for i, f in enumerate(faces):
            try:
                samples = _sample_face(f, args.samples_per_face)
            except Exception:
                samples = []
            face_entries.append({
                "index": i,
                "samples": samples,
            })

        extras = {
            "curvature_kind": args.curvature_kind,
            "samples_per_face": args.samples_per_face,
            "face_count": len(faces),
            "faces": face_entries,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
