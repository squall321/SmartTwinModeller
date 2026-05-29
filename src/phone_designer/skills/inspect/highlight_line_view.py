"""highlight_line_view — atomic, read-only Class-A inspection.

Render virtual "highlight lines" on each face under a parallel light source
parallel to ``view_direction``. The classical automotive Class-A inspection
trick: place N infinitely-distant lights aligned with the camera and look at
the bright streaks they make on the surface — discontinuities in those
streaks (cusps, kinks, sharp angle jumps) reveal G1/G2 surface defects that
are invisible in a plain shaded view.

Algorithm (cheap surrogate of the SDS / GL-based real renderer):
    For each face:
        1. Sample a 1-D path along U at several V offsets (= one "stripe").
        2. At every sample compute the reflected ray
              R = D − 2 (D·N) N
           where N is the unit surface normal and D is the (unit, parallel)
           view_direction.
        3. Look at consecutive angular differences ΔΘ between adjacent R's.
           A sharp jump (> π/8 rad ≈ 22.5°) marks a highlight-line
           breakpoint — these are the points the planner shows the user.

extras schema:
    {
      "view_direction": [x, y, z],
      "light_count": int,
      "face_count": int,
      "highlight_lines": [
        {"face_idx": i,
         "stripe_idx": j,
         "points": [{"xyz": [x,y,z], "delta_rad": float}, ...]},
        ...
      ],
    }

Body unchanged — post ``body_present``.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family: str, name: str):
    import pathlib
    import yaml
    here = pathlib.Path(__file__).resolve()
    for root in here.parents:
        cand = root / "catalogs" / family / f"{name}.yaml"
        if cand.exists():
            return yaml.safe_load(cand.read_text())
    raise FileNotFoundError(f"catalog not found: {family}/{name}.yaml")


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if n < 1e-12:
        return (0.0, 0.0, 1.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def _normal_at(adaptor, u: float, v: float) -> tuple[float, float, float] | None:
    """Surface unit normal at (u, v) — works for planes & freeforms alike."""
    from OCP.GeomAbs import GeomAbs_Plane

    try:
        if adaptor.GetType() == GeomAbs_Plane:
            n = adaptor.Plane().Axis().Direction()
            return _unit((n.X(), n.Y(), n.Z()))
        d1u = adaptor.DN(u, v, 1, 0)
        d1v = adaptor.DN(u, v, 0, 1)
        nx = d1u.Y() * d1v.Z() - d1u.Z() * d1v.Y()
        ny = d1u.Z() * d1v.X() - d1u.X() * d1v.Z()
        nz = d1u.X() * d1v.Y() - d1u.Y() * d1v.X()
        return _unit((nx, ny, nz))
    except Exception:
        return None


def _reflect(d: tuple[float, float, float], n: tuple[float, float, float]):
    """R = D − 2(D·N) N — pure mirror reflection."""
    dot = d[0] * n[0] + d[1] * n[1] + d[2] * n[2]
    return (
        d[0] - 2 * dot * n[0],
        d[1] - 2 * dot * n[1],
        d[2] - 2 * dot * n[2],
    )


def _angle_between(a, b) -> float:
    da = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
    db = math.sqrt(b[0] ** 2 + b[1] ** 2 + b[2] ** 2)
    if da < 1e-12 or db < 1e-12:
        return 0.0
    c = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (da * db)
    c = max(-1.0, min(1.0, c))
    return math.acos(c)


# Threshold for "this is a highlight-line breakpoint". 22.5° is the classical
# Class-A audit threshold — anything sharper than this is visible to the eye
# as a kink in the bright streak.
_SHARP_RAD = math.pi / 8


@skill(
    name="highlight_line_view",
    category="inspect",
    level="atomic",
    summary="Class-A highlight-line view. Parallel virtual lights along the "
            "view_direction reflect off every face — sharp angle jumps in the "
            "reflected ray between adjacent samples mark highlight-line breaks "
            "(G1/G2 discontinuities). Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["highlight_line_audit"],
    preserves=["body_topology"],
    manufacturing={
        "injection_mold_pa": {"extras": {"class_a_aware": True}},
        "die_cast_al":       {"extras": {"class_a_aware": True}},
    },
    failure_modes=[],
    cost_hint=0.35,
    post_conditions=[PostCondition(kind="body_present")],
)
class HighlightLineView(SkillBase):
    class Args(BaseModel):
        view_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        light_count: int = Field(default=8, ge=1, le=64)
        samples_per_stripe: int = Field(default=12, ge=2, le=200)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        from phone_designer.skills._resolvers import _all_faces

        shape = _occt_shape(body)
        faces = _all_faces(shape)
        d = _unit(args.view_direction)

        highlight_lines: list[dict[str, Any]] = []

        for fi, face in enumerate(faces):
            try:
                adaptor = BRepAdaptor_Surface(face)
                u0, u1 = adaptor.FirstUParameter(), adaptor.LastUParameter()
                v0, v1 = adaptor.FirstVParameter(), adaptor.LastVParameter()
            except Exception:
                continue

            for stripe in range(args.light_count):
                # one stripe per virtual light. evenly distribute V across face.
                fv = (stripe + 0.5) / args.light_count
                v = v0 + (v1 - v0) * fv

                last_R = None
                points: list[dict[str, Any]] = []
                for k in range(args.samples_per_stripe):
                    fu = (k + 0.5) / args.samples_per_stripe
                    u = u0 + (u1 - u0) * fu
                    n = _normal_at(adaptor, u, v)
                    if n is None:
                        last_R = None
                        continue
                    R = _reflect(d, n)
                    try:
                        p = adaptor.Value(u, v)
                        xyz = (p.X(), p.Y(), p.Z())
                    except Exception:
                        last_R = R
                        continue

                    delta = 0.0 if last_R is None else _angle_between(last_R, R)
                    if delta > _SHARP_RAD:
                        points.append({
                            "xyz": [round(c, 4) for c in xyz],
                            "delta_rad": round(delta, 6),
                        })
                    last_R = R

                if points:
                    highlight_lines.append({
                        "face_idx": fi,
                        "stripe_idx": stripe,
                        "points": points,
                    })

        extras = {
            "view_direction": [round(c, 6) for c in d],
            "light_count": args.light_count,
            "face_count": len(faces),
            "sharp_threshold_rad": round(_SHARP_RAD, 6),
            "highlight_lines": highlight_lines,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
