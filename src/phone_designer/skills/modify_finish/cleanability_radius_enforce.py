"""cleanability_radius_enforce — atomic.

Medical-device DFM: every internal (concave) corner must have a fillet radius
≥ min_internal_r_mm so it can be cleaned / wiped down without trapping debris
or biofilm (ISO 13485, IEC 60601-1 hygienic design guidance).

This skill:
  1. Walks every edge in the body.
  2. Skips convex (outside) corners — wiping does not get hung up on them.
  3. For each CONCAVE edge that is currently sharp (radius effectively 0,
     i.e. a hard interior corner), adds a fillet of radius `min_internal_r_mm`.
  4. Best-effort: edges that fail individually are skipped so a single
     problematic corner does not abort the whole sweep.

Concavity test is reused from sanding_pass — we apply the *inverse* logic so
sharp concave edges (interior crevices) are the ones we round.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _is_concave_edge(shape, edge) -> bool:
    """Concave (interior corner) edge.

    Test by ring-sampling: take N points on a tiny circle around the edge
    midpoint, perpendicular to the edge tangent, and count how many lie
    INSIDE the solid.

    Geometry:
      - At a CONVEX external corner, the material occupies an acute (<180°)
        dihedral, so < 50% of ring samples are IN.
      - At a CONCAVE internal corner, the material occupies a reflex (>180°)
        dihedral, so > 50% of ring samples are IN.

    Tangent / coplanar edges (faces nearly parallel) are excluded — they are
    not "corners" and need no cleanability fillet.
    """
    import math

    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    from phone_designer.skills.modify_finish.sanding_pass import (
        _adjacent_faces,
        _edge_midpoint,
        _edge_tangent_mid,
        _face_normal_at,
        _face_uv_center,
    )

    faces = _adjacent_faces(shape, edge)
    if len(faces) < 2:
        return False
    u0, v0 = _face_uv_center(faces[0])
    u1, v1 = _face_uv_center(faces[1])
    nA = _face_normal_at(faces[0], u0, v0)
    nB = _face_normal_at(faces[1], u1, v1)
    if nA is None or nB is None:
        return False

    dot_n = nA[0] * nB[0] + nA[1] * nB[1] + nA[2] * nB[2]
    if abs(dot_n) > 0.999:
        # tangent / coplanar — no sharp corner.
        return False

    try:
        p = _edge_midpoint(edge)
    except Exception:
        return False

    t = _edge_tangent_mid(edge)
    if t is None:
        return False

    # Build an orthonormal basis (u, v) perpendicular to t. Use nA projected
    # perpendicular to t as the first axis, then v = t × u.
    tx, ty, tz = t
    # u = nA - (nA·t) t, normalised
    dot_nA_t = nA[0] * tx + nA[1] * ty + nA[2] * tz
    ux = nA[0] - dot_nA_t * tx
    uy = nA[1] - dot_nA_t * ty
    uz = nA[2] - dot_nA_t * tz
    ulen = math.sqrt(ux * ux + uy * uy + uz * uz)
    if ulen < 1e-9:
        return False
    ux, uy, uz = ux / ulen, uy / ulen, uz / ulen
    # v = t × u
    vx = ty * uz - tz * uy
    vy = tz * ux - tx * uz
    vz = tx * uy - ty * ux

    eps = 0.005
    n_samples = 16
    in_count = 0
    valid = 0
    try:
        cls = BRepClass3d_SolidClassifier(shape)
        for i in range(n_samples):
            theta = 2.0 * math.pi * i / n_samples
            cs = math.cos(theta)
            sn = math.sin(theta)
            sx = p.X() + eps * (cs * ux + sn * vx)
            sy = p.Y() + eps * (cs * uy + sn * vy)
            sz = p.Z() + eps * (cs * uz + sn * vz)
            cls.Perform(gp_Pnt(sx, sy, sz), 1e-7)
            st = cls.State()
            if st == TopAbs_IN:
                in_count += 1
            valid += 1
    except Exception:
        return False

    if valid == 0:
        return False
    return in_count > valid * 0.55


@skill(
    name="cleanability_radius_enforce",
    category="modify/finish",
    level="atomic",
    summary="Find sharp internal (concave) corners and auto-fillet them to the "
            "given minimum radius, so the part can be wiped down for medical / "
            "food-contact cleanability. Convex edges and already-rounded edges "
            "are skipped.",
    selector_kinds=[],
    history_rules={
        "concave_sharp_edges": HistoryRule.CONSUMED,
        "result_faces":        HistoryRule.GENERATED_NEW,
        "convex_edges":        HistoryRule.MODIFIED_INHERIT,
    },
    produces_features=["cleanability_fillet_faces"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_fillet_r_mm": "args.min_internal_r_mm",
                              "extras": {"cleanability": "wipe_down",
                                         "standard_ref": "IEC 60601-1"}},
        "die_cast_al":       {"min_fillet_r_mm": "args.min_internal_r_mm",
                              "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_fillet_r_mm": "args.min_internal_r_mm",
                              "min_draft_deg": 0.5,
                              "extras": {"medical_grade": True}},
    },
    failure_modes=["fm.no_edges_processed", "fm.fillet_self_intersection"],
    cost_hint=0.4,
    # If the body already has no sharp concave edges, this is a legitimate
    # no-op. allow_no_change preserves the case.
    post_conditions=[PostCondition(kind="face_count_changed", allow_no_change=True)],
)
class CleanabilityRadiusEnforce(SkillBase):
    class Args(BaseModel):
        min_internal_r_mm: float = Field(
            default=1.0, ge=0.1, le=10.0,
            description="Minimum allowed internal corner radius for cleanability. "
                        "Concave edges currently sharper than this get auto-filleted.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        from phone_designer.skills._resolvers import _all_edges

        shape = body.wrapped if hasattr(body, "wrapped") else body

        concave = [e for e in _all_edges(shape) if _is_concave_edge(shape, e)]

        result_shape = shape
        ok_count = 0
        if concave:
            # Bulk first, fall back to per-edge.
            try:
                maker = BRepFilletAPI_MakeFillet(shape)
                for e in concave:
                    maker.Add(args.min_internal_r_mm, e)
                maker.Build()
                if maker.IsDone():
                    result_shape = maker.Shape()
                    ok_count = len(concave)
                else:
                    raise RuntimeError("bulk cleanability fillet IsDone=False")
            except Exception:
                ok_count = 0
                for e in concave:
                    try:
                        one = BRepFilletAPI_MakeFillet(result_shape)
                        one.Add(args.min_internal_r_mm, e)
                        one.Build()
                        if one.IsDone():
                            result_shape = one.Shape()
                            ok_count += 1
                    except Exception:
                        continue

        history = EntityHistoryMap(
            rules={
                "concave_sharp_edges": HistoryRule.CONSUMED,
                "result_faces":        HistoryRule.GENERATED_NEW,
                "convex_edges":        HistoryRule.MODIFIED_INHERIT,
            },
        )
        return SkillResult(
            body=Part(result_shape),
            history=history,
            extras={
                "concave_edges_found": len(concave),
                "edges_filleted": ok_count,
                "min_internal_r_mm": args.min_internal_r_mm,
            },
        )
