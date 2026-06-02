"""ray_trace_smoke — atomic, read-only. Single-ray optical smoke test.

Casts a single ray from ``light_source_xyz`` along ``light_direction`` against
the body and reports whether it hits the body, the first-hit distance, the
incidence angle to the hit surface, and — if the ray lands on a face that
matches ``target_face_selector`` — flags ``ray_hit.on_target=True``.

This is a *smoke* test, not a real optical solver: a single ray, single body,
no refraction, no reflection. Use it to sanity-check whether an LED placed
inside a housing actually has line-of-sight to its intended light-pipe entry
face or optical window.

extras["ray_hit"] schema:
    {
      "hit": bool,
      "distance_mm": float | None,
      "point": [x, y, z] | None,
      "face_index": int | None,
      "incidence_angle_deg": float | None,   # 0° = head-on, 90° = grazing
      "on_target": bool,                     # True if hit face is in target_face_selector
      "target_face_count": int,
    }
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="ray_trace_smoke",
    category="inspect",
    level="atomic",
    summary="Single-ray optical smoke test — cast one ray from "
            "light_source_xyz along light_direction and report hit/miss, "
            "first-hit distance, point, incidence angle, and whether the "
            "hit face is in target_face_selector. Read-only. "
            "extras['ray_hit'] holds the report.",
    selector_kinds=["faces"],
    history_rules={},
    produces_features=["ray_hit_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[
        "fm.zero_ray_direction",
        "fm.ray_source_inside_body",
    ],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class RayTraceSmoke(SkillBase):
    class Args(BaseModel):
        light_source_xyz: tuple[float, float, float] = Field(
            description="Ray origin in world (mm)."
        )
        light_direction: tuple[float, float, float] = Field(
            description="Ray direction in world. Need not be unit length; "
                        "must be non-zero."
        )
        target_face_selector: SelectorRef = Field(
            description="Faces the ray is *expected* to hit. ray_hit.on_target "
                        "is True iff the actual hit face is in this set "
                        "(matched by index, with bbox-center fallback)."
        )
        max_dist_mm: float = Field(
            default=1000.0, gt=0.0, le=10000.0,
            description="Trace cutoff distance (mm)."
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
        from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

        from phone_designer.skills._resolvers import (
            _all_faces,
            _face_center,
            _face_normal_at_center,
            resolve_faces,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body

        # ── Validate ray direction ───────────────────────────────────────────
        dx, dy, dz = args.light_direction
        dlen = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dlen < 1e-12:
            raise ValueError(
                "ray_trace_smoke: light_direction must be non-zero"
            )
        ndir = (dx / dlen, dy / dlen, dz / dlen)
        origin = args.light_source_xyz

        # ── Resolve target faces (for on_target classification) ─────────────
        try:
            target_faces = resolve_faces(shape, args.target_face_selector, body=body)
        except Exception:
            target_faces = []
        target_face_count = len(target_faces)

        # Build target signatures: TShape identity (when wrappers share it)
        # AND face-center coords (robust fallback when build123d wraps the
        # face in a transient Python object whose .TShape() differs).
        target_ids: set[int] = set()
        target_centers: list[tuple[float, float, float]] = []
        for tf in target_faces:
            try:
                target_ids.add(id(tf.TShape()))
            except Exception:
                pass
            try:
                target_centers.append(_face_center(tf))
            except Exception:
                continue

        # ── Cast the ray against all faces of the shape ──────────────────────
        try:
            line = gp_Lin(
                gp_Pnt(origin[0], origin[1], origin[2]),
                gp_Dir(ndir[0], ndir[1], ndir[2]),
            )
        except Exception as exc:
            raise ValueError(
                f"ray_trace_smoke: failed to build gp_Lin — {exc}"
            )

        inter = BRepIntCurveSurface_Inter()
        inter.Init(shape, line, 1e-7)

        best_t: float | None = None
        best_pnt: tuple[float, float, float] | None = None
        best_face = None
        while inter.More():
            t = inter.W()
            if 1e-6 < t <= args.max_dist_mm:
                if best_t is None or t < best_t:
                    best_t = t
                    p = inter.Pnt()
                    best_pnt = (p.X(), p.Y(), p.Z())
                    try:
                        best_face = inter.Face()
                    except Exception:
                        best_face = None
            inter.Next()

        hit = best_t is not None

        # ── Compute incidence angle (ray ↔ face normal) ──────────────────────
        incidence_deg: float | None = None
        on_target = False
        face_index: int | None = None
        if hit and best_face is not None:
            try:
                fn = _face_normal_at_center(best_face)
                fnorm = math.sqrt(fn[0] * fn[0] + fn[1] * fn[1] + fn[2] * fn[2])
                if fnorm > 1e-12:
                    nf = (fn[0] / fnorm, fn[1] / fnorm, fn[2] / fnorm)
                    # Incidence angle = angle between INCOMING ray and INWARD
                    # surface normal. nf is outward; use -nf as inward.
                    cos_inc = -(ndir[0] * nf[0] + ndir[1] * nf[1] + ndir[2] * nf[2])
                    cos_inc = max(-1.0, min(1.0, cos_inc))
                    incidence_deg = math.degrees(math.acos(abs(cos_inc)))
            except Exception:
                incidence_deg = None

            try:
                hit_id = id(best_face.TShape())
                on_target = hit_id in target_ids
            except Exception:
                on_target = False
            # Fallback: compare face-center distances. If the hit face's
            # center is within 1 mm of any target face center, accept it.
            if not on_target and target_centers:
                try:
                    hc = _face_center(best_face)
                    for tc in target_centers:
                        dx_ = hc[0] - tc[0]
                        dy_ = hc[1] - tc[1]
                        dz_ = hc[2] - tc[2]
                        if math.sqrt(dx_ * dx_ + dy_ * dy_ + dz_ * dz_) < 1.0:
                            on_target = True
                            break
                except Exception:
                    pass

            # face_index: try to match against all body faces.
            try:
                for i, f in enumerate(_all_faces(shape)):
                    try:
                        if id(f.TShape()) == id(best_face.TShape()):
                            face_index = i
                            break
                    except Exception:
                        continue
            except Exception:
                face_index = None

        ray_hit: dict[str, Any] = {
            "hit": hit,
            "distance_mm": round(best_t, 4) if best_t is not None else None,
            "point": (
                [round(c, 4) for c in best_pnt] if best_pnt is not None else None
            ),
            "face_index": face_index,
            "incidence_angle_deg": (
                round(incidence_deg, 3) if incidence_deg is not None else None
            ),
            "on_target": bool(on_target),
            "target_face_count": target_face_count,
        }

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"ray_hit": ray_hit},
        )
