"""hlr_view — atomic, read-only. Hidden-line-removal projection (HLRBRep_Algo).

The real engineering-drawing projection: OCCT's exact HLR algorithm splits the
body's edges (plus the silhouette/outline curves of curved faces) into VISIBLE
and HIDDEN sets as seen from ``view_direction`` (camera → body). This is what
``silhouette`` (brute all-edges projection, no visibility) honestly refuses to
be. Probe-verified on the pinned OCP 7.8: box-with-hole front view →
visible 4 / hidden 9 / outline 1 (see tests/skills/test_hlr_view.py).

Frame convention
----------------
The sheet frame is (u = sheet-right, v = sheet-up), both perpendicular to
``view_direction``. ``up_hint`` picks v (orthogonalized against the view);
default up is world +Z (or +X when looking along ±Z). The HLR projector is
built with X-axis = u so the returned edges land directly in (u, v) at Z = 0 —
the 2D points are the raw HLR output, not a re-projection.

Guard (per-view; drawing_sheet relies on this NOT raising for big/odd parts)
----------------------------------------------------------------------------
Exact HLR is far heavier than tessellation, so a *face-count budget*
(``max_face_count``, default 2000) gates it. Over budget — or on ANY HLR
exception — the skill falls back to the existing brute silhouette machinery
(same (u, v) frame): all edges go to ``visible_polylines_2d``, hidden/outline
are empty, ``mode='silhouette_fallback'`` and ``label='non_cut_ready'`` (hidden
edges are mixed in, visibility unknown). The raw error is preserved in
``note`` — never masked.

result.extras schema (strict-JSON-safe; all floats finite):
    {"view_direction": [x,y,z],
     "mode": "hlr" | "silhouette_fallback",
     "label": "hlr" | "non_cut_ready",
     "visible_polylines_2d": [[(u,v), ...], ...],   # HLR VCompound
     "hidden_polylines_2d":  [[(u,v), ...], ...],   # HLR HCompound
     "outline": {"visible_polylines_2d": [...],     # OutLineVCompound
                 "hidden_polylines_2d": [...]},     # OutLineHCompound
     "n_visible": int, "n_hidden": int, "n_outline": int,
     "extent_uv": [u_min, v_min, u_max, v_max],
     "face_count": int,
     "note": str}

Known limits (honest): coincident visible/hidden 2D duplicates are NOT deduped
(a box's back-face rectangle projects onto the front one — raw HLR counts are
reported as-is); smooth/Rg1 regularity lines are not emitted in v1; there is
no hard per-view wall-clock timeout — the face-count budget is the guard.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult

# Exact HLR budget — deliberately far below the tessellation-tier
# DEFAULT_MAX_FACE_COUNT (16 000): HLRBRep_Algo does exact curve-curve
# intersections and scales much worse than a mesh pass.
DEFAULT_HLR_MAX_FACES = 2000


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _normalize(vec: tuple[float, float, float]) -> tuple[float, float, float]:
    m = math.sqrt(vec[0] ** 2 + vec[1] ** 2 + vec[2] ** 2)
    if m < 1e-12:
        raise ValueError("fm.bad_view_direction: view_direction must be non-zero")
    return (vec[0] / m, vec[1] / m, vec[2] / m)


def sheet_basis(
    view_direction: tuple[float, float, float],
    up_hint: tuple[float, float, float] | None = None,
) -> tuple[tuple[float, float, float], tuple[float, float, float],
           tuple[float, float, float]]:
    """(u=sheet-right, v=sheet-up, z=towards camera) for a camera→body dir.

    v is the up_hint orthogonalized against z; u = v × z (right-handed screen
    frame). Default up: world +Z, or +X when the view is along ±Z.
    """
    d = _normalize(view_direction)
    z = (-d[0], -d[1], -d[2])
    if up_hint is None:
        up_hint = (0.0, 0.0, 1.0) if abs(z[2]) < 0.9 else (1.0, 0.0, 0.0)
    w = up_hint
    dot = w[0] * z[0] + w[1] * z[1] + w[2] * z[2]
    vx, vy, vz = w[0] - dot * z[0], w[1] - dot * z[1], w[2] - dot * z[2]
    vm = math.sqrt(vx * vx + vy * vy + vz * vz)
    if vm < 1e-9:
        raise ValueError(
            "fm.bad_view_direction: up_hint is parallel to the view direction")
    v = (vx / vm, vy / vm, vz / vm)
    u = (v[1] * z[2] - v[2] * z[1],
         v[2] * z[0] - v[0] * z[2],
         v[0] * z[1] - v[1] * z[0])
    return u, v, z


def _count_faces(shape) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    n = 0
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        n += 1
        ex.Next()
    return n


def _compound_polylines_2d(comp, samples: int) -> list[list[tuple[float, float]]]:
    """Explode an HLR result compound into per-edge 2D polylines.

    HLRBRep_HLRToShape returns edges already transformed into the projector
    frame (X=u, Y=v, Z=depth≈0) — take (X, Y) directly.
    """
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    from phone_designer.skills.inspect.silhouette import _sample_edge_3d

    out: list[list[tuple[float, float]]] = []
    if comp is None:
        return out
    try:
        if comp.IsNull():
            return out
    except Exception:
        return out
    ex = TopExp_Explorer(comp, TopAbs_EDGE)
    while ex.More():
        edge = TopoDS.Edge_s(ex.Current())
        ex.Next()
        try:
            pts3d = _sample_edge_3d(edge, samples)
        except Exception:
            pts3d = []
        if len(pts3d) >= 2:
            out.append([(round(p[0], 4), round(p[1], 4)) for p in pts3d])
    return out


def _run_hlr(shape, u, v, z, samples: int) -> dict[str, Any]:
    """Exact HLR pass. Raises on any OCCT failure — the caller guards."""
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.HLRAlgo import HLRAlgo_Projector
    from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape

    algo = HLRBRep_Algo()
    algo.Add(shape)
    ax = gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(*z), gp_Dir(*u))
    algo.Projector(HLRAlgo_Projector(ax))
    algo.Update()
    algo.Hide()
    hts = HLRBRep_HLRToShape(algo)

    def _safe(getter):
        try:
            return getter()
        except Exception:
            return None

    return {
        "visible": _compound_polylines_2d(_safe(hts.VCompound), samples),
        "hidden": _compound_polylines_2d(_safe(hts.HCompound), samples),
        "outline_visible": _compound_polylines_2d(
            _safe(hts.OutLineVCompound), samples),
        "outline_hidden": _compound_polylines_2d(
            _safe(hts.OutLineHCompound), samples),
    }


def _brute_silhouette_2d(shape, u, v, samples: int) -> list[list[tuple[float, float]]]:
    """Brute all-edges projection in THIS view's (u, v) frame.

    Reuses the silhouette skill's sampling machinery, but projects with the
    caller's basis so fallback views stay frame-consistent with HLR views on
    the same sheet (silhouette._build_uv_basis picks a mirrored u for some
    directions).
    """
    from phone_designer.skills._resolvers import _all_edges
    from phone_designer.skills.inspect.silhouette import _sample_edge_3d

    out: list[list[tuple[float, float]]] = []
    for e in _all_edges(shape):
        try:
            pts3d = _sample_edge_3d(e, samples)
        except Exception:
            pts3d = []
        if len(pts3d) < 2:
            continue
        out.append([
            (round(p[0] * u[0] + p[1] * u[1] + p[2] * u[2], 4),
             round(p[0] * v[0] + p[1] * v[1] + p[2] * v[2], 4))
            for p in pts3d
        ])
    return out


def _extent(groups: list[list[list[tuple[float, float]]]]) -> list[float]:
    u_min = v_min = math.inf
    u_max = v_max = -math.inf
    for polys in groups:
        for poly in polys:
            for (pu, pv) in poly:
                u_min = min(u_min, pu)
                u_max = max(u_max, pu)
                v_min = min(v_min, pv)
                v_max = max(v_max, pv)
    if not math.isfinite(u_min):
        return [0.0, 0.0, 0.0, 0.0]
    return [round(u_min, 4), round(v_min, 4), round(u_max, 4), round(v_max, 4)]


@skill(
    name="hlr_view",
    category="inspect",
    level="atomic",
    summary="Hidden-line-removal projection (HLRBRep_Algo): split the body's "
            "edges + curved-face outlines into VISIBLE and HIDDEN 2D "
            "polylines as seen from view_direction — the real "
            "engineering-drawing view (unlike 'silhouette', which brute-"
            "projects every edge with no visibility). Over the face-count "
            "budget or on HLR failure it falls back to the brute silhouette "
            "with label='non_cut_ready'. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["hlr_polylines_2d"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.bad_view_direction", "fm.empty_view"],
    cost_hint=0.6,
    post_conditions=[PostCondition(kind="body_present")],
)
class HlrView(SkillBase):
    class Args(BaseModel):
        view_direction: tuple[float, float, float] = Field(
            default=(0.0, 0.0, -1.0),
            description="Camera → body direction (same convention as "
                        "'silhouette').")
        up_hint: tuple[float, float, float] | None = Field(
            default=None,
            description="World direction that should point UP on the sheet "
                        "(orthogonalized). Default: +Z, or +X for ±Z views.")
        samples_per_edge: int = Field(default=20, ge=2, le=500)
        max_face_count: int = Field(
            default=DEFAULT_HLR_MAX_FACES, ge=1,
            description="Exact-HLR face budget. Bodies above it skip HLR and "
                        "take the honest brute-silhouette fallback "
                        "(label='non_cut_ready').")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("fm.empty_view: hlr_view needs a body.")
        shape = _occt_shape(body)
        u, v, z = sheet_basis(tuple(args.view_direction),
                              tuple(args.up_hint) if args.up_hint else None)

        face_count = _count_faces(shape)
        mode = "hlr"
        label = "hlr"
        note = "HLR exact algorithm (HLRBRep_Algo)."
        buckets: dict[str, list] = {
            "visible": [], "hidden": [],
            "outline_visible": [], "outline_hidden": [],
        }

        if face_count > args.max_face_count:
            mode, label = "silhouette_fallback", "non_cut_ready"
            note = (f"face_count {face_count} > max_face_count "
                    f"{args.max_face_count} — exact HLR skipped; brute "
                    "silhouette fallback (ALL edges as 'visible', hidden "
                    "edges mixed in, visibility NOT computed).")
        else:
            try:
                buckets = _run_hlr(shape, u, v, z, args.samples_per_edge)
            except Exception as exc:  # noqa: BLE001 — per-view guard
                mode, label = "silhouette_fallback", "non_cut_ready"
                note = (f"HLR failed ({type(exc).__name__}: {exc}) — brute "
                        "silhouette fallback (ALL edges as 'visible', hidden "
                        "edges mixed in, visibility NOT computed).")
                buckets = {"visible": [], "hidden": [],
                           "outline_visible": [], "outline_hidden": []}

        if mode == "silhouette_fallback":
            buckets["visible"] = _brute_silhouette_2d(
                shape, u, v, args.samples_per_edge)

        visible = buckets["visible"]
        hidden = buckets["hidden"]
        out_v = buckets["outline_visible"]
        out_h = buckets["outline_hidden"]

        if not (visible or hidden or out_v or out_h):
            raise ValueError(
                "fm.empty_view: no projected curves in this view (empty body "
                "or degenerate projection).")

        extras = {
            "view_direction": [round(c, 4) for c in _normalize(
                tuple(args.view_direction))],
            "mode": mode,
            "label": label,
            "visible_polylines_2d": visible,
            "hidden_polylines_2d": hidden,
            "outline": {
                "visible_polylines_2d": out_v,
                "hidden_polylines_2d": out_h,
            },
            "n_visible": len(visible),
            "n_hidden": len(hidden),
            "n_outline": len(out_v) + len(out_h),
            "extent_uv": _extent([visible, hidden, out_v, out_h]),
            "face_count": face_count,
            "note": note,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
