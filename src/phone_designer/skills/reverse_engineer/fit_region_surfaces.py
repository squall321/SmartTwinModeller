"""fit_region_surfaces — atomic, read-only. Scan-to-CAD stage 2 (Phase 3-1).

Per mesh region (from mesh_segment_regions' region growing), try analytic
surface candidates in fixed order — plane → cylinder → cone → sphere → torus —
and accept the FIRST whose RMS residual over the region's triangle VERTICES is
≤ ``rms_tol_mm`` (simplicity-biased, deterministic). A region no candidate
fits is honestly ``kind='freeform_unfit'`` with the best candidate's rms
reported — never a fabricated fit.

METHOD NOTE (why not call the existing fit_* skills directly)
-------------------------------------------------------------
The existing ``fit_plane`` / ``fit_cylinder`` / ``fit_cone`` / ``fit_sphere``
/ ``fit_torus`` skills are FACE-SELECTOR based: they resolve one B-rep face
and sample a UV grid on it. A mesh region is a bag of triangles spanning
hundreds of tiny facet faces, so those skills cannot consume it. Per the
scan-to-CAD spike ruling we therefore fit **directly from the region's
triangle vertices** with module-local least squares:

* plane    — centroid + SVD smallest right-singular vector (numpy),
* cylinder — axis from the NULL direction of the area-weighted triangle-normal
             matrix (cylinder normals ⟂ axis, so the smallest singular vector
             of the normal cloud IS the axis — more robust than the
             point-covariance heuristic for short/wide cylinders), then a
             Kåsa algebraic circle fit in the plane ⟂ axis,
* sphere / cone / torus — the point-based helpers **imported** (not copied)
  from ``inspect.fit_sphere`` / ``inspect.fit_cone`` / ``inspect.fit_torus``
  (``_fit_*_from_points``), with the RMS recomputed here from the returned
  params so every candidate is judged by the same metric.

Honest grade labels (INSIDE the artifact):
  rms ≤ 1e-4 mm → "exact"; ≤ 1e-2 → "tight"; ≤ rms_tol_mm → "approx";
  above tolerance → kind "freeform_unfit", grade "unfit".

extras["fit_region_surfaces"] = {
  "regions": [{"id", "n_triangles", "n_points", "area_mm2",
               "kind",        # plane|cylinder|cone|sphere|torus|freeform_unfit
               "params",      # kind-specific analytic params (JSON-safe)
               "rms_mm", "grade",
               "candidates",  # {kind: rms_mm|None} — full honesty trail
               "best_candidate"  # only for freeform_unfit
             }],
  "n_freeform_unfit": int,
  "freeform_area_fraction": float,   # unfit area / total area
  "rms_tol_mm": float,
  "method_note": str,
}

Body unchanged (post ``body_present``).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.inspect.fit_cone import _fit_cone_from_points
from phone_designer.skills.inspect.fit_sphere import _fit_sphere_from_points
from phone_designer.skills.inspect.fit_torus import _fit_torus_from_points
from phone_designer.skills.reverse_engineer.mesh_segment_regions import (
    region_summary_json,
    segment_body,
)

_METHOD_NOTE = (
    "fits computed from region triangle VERTICES with module-local least "
    "squares (SVD plane; normal-nullspace axis + Kåsa circle cylinder) — the "
    "existing fit_plane/fit_cylinder skills are face-selector based and "
    "cannot consume mesh regions; sphere/cone/torus candidates reuse the "
    "point-based helpers imported from inspect.fit_sphere/fit_cone/fit_torus "
    "with RMS recomputed here on the same metric."
)


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except Exception:
        return False


def _rms(residuals) -> float | None:
    import numpy as np

    r = np.asarray(residuals, dtype=float)
    if r.size == 0 or not np.all(np.isfinite(r)):
        return None
    return float(np.sqrt(np.mean(r * r)))


# --------------------------------------------------------------------------- #
# candidate fitters — every one returns (params dict, rms_mm) or (None, None)
# --------------------------------------------------------------------------- #

def _fit_plane(pts, mean_normal=None):
    import numpy as np

    if len(pts) < 3:
        return None, None
    centroid = np.mean(pts, axis=0)
    centered = pts - centroid
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    n = vh[-1, :]
    mag = float(np.linalg.norm(n))
    if mag < 1e-12:
        return None, None
    n = n / mag
    # sign: align with the region's mean triangle normal (outward)
    if mean_normal is not None and float(np.dot(n, mean_normal)) < 0:
        n = -n
    rms = _rms(centered @ n)
    if rms is None:
        return None, None
    params = {
        "origin": [round(float(c), 6) for c in centroid],
        "normal": [round(float(c), 6) for c in n],
    }
    return params, rms


def _fit_cylinder(pts, tri_normals, tri_areas):
    """Axis = null direction of the area-weighted normal matrix; radius/center
    via Kåsa algebraic circle fit in the plane perpendicular to the axis."""
    import numpy as np

    if len(pts) < 6 or len(tri_normals) < 2:
        return None, None
    w = np.sqrt(np.maximum(tri_areas, 0.0))[:, None]
    _, _, vh = np.linalg.svd(tri_normals * w, full_matrices=False)
    a = vh[-1, :]
    mag = float(np.linalg.norm(a))
    if mag < 1e-12:
        return None, None
    a = a / mag

    # frame perpendicular to axis
    e = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = e - np.dot(e, a) * a
    x = x / np.linalg.norm(x)
    y = np.cross(a, x)

    o0 = np.mean(pts, axis=0)
    d = pts - o0
    u = d @ x
    v = d @ y
    # Kåsa: [2u 2v 1] · [uc vc k]ᵀ = u²+v²
    A = np.column_stack([2.0 * u, 2.0 * v, np.ones(len(u))])
    b = u * u + v * v
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except Exception:
        return None, None
    uc, vc, k = float(sol[0]), float(sol[1]), float(sol[2])
    r_sq = k + uc * uc + vc * vc
    if not _finite(r_sq) or r_sq <= 0:
        return None, None
    r = math.sqrt(r_sq)
    dist = np.sqrt((u - uc) ** 2 + (v - vc) ** 2)
    rms = _rms(dist - r)
    if rms is None or r < 1e-9:
        return None, None
    origin = o0 + uc * x + vc * y
    params = {
        "origin": [round(float(c), 6) for c in origin],
        "axis": [round(float(c), 6) for c in a],
        "radius_mm": round(r, 6),
    }
    return params, rms


def _fit_sphere(pts):
    import numpy as np

    if len(pts) < 4:
        return None, None
    center, r, _max_dev = _fit_sphere_from_points(
        [tuple(p) for p in pts.tolist()])
    if not all(_finite(c) for c in center) or not _finite(r) or r < 1e-9:
        return None, None
    c = np.asarray(center, dtype=float)
    dist = np.linalg.norm(pts - c, axis=1)
    rms = _rms(dist - r)
    if rms is None:
        return None, None
    params = {
        "center": [round(float(x), 6) for x in center],
        "radius_mm": round(float(r), 6),
    }
    return params, rms


def _fit_cone(pts):
    import numpy as np

    if len(pts) < 6:
        return None, None
    apex, axis, half_angle, _max_dev = _fit_cone_from_points(
        [tuple(p) for p in pts.tolist()])
    if (not all(_finite(c) for c in apex)
            or not all(_finite(c) for c in axis)
            or not _finite(half_angle)):
        return None, None
    if half_angle < math.radians(0.5) or half_angle > math.radians(89.5):
        # degenerate cone (≈cylinder or ≈plane) — let those kinds claim it
        return None, None
    a = np.asarray(axis, dtype=float)
    ap = np.asarray(apex, dtype=float)
    d = pts - ap
    t = d @ a
    rho = np.linalg.norm(d - np.outer(t, a), axis=1)
    # perpendicular distance from point to the cone surface
    res = rho * math.cos(half_angle) - t * math.sin(half_angle)
    rms = _rms(res)
    if rms is None:
        return None, None
    params = {
        "apex": [round(float(c), 6) for c in apex],
        "axis": [round(float(c), 6) for c in axis],
        "half_angle_deg": round(math.degrees(half_angle), 4),
    }
    return params, rms


def _fit_torus(pts):
    import numpy as np

    if len(pts) < 8:
        return None, None
    center, axis, major_r, minor_r, _max_dev = _fit_torus_from_points(
        [tuple(p) for p in pts.tolist()])
    if (not all(_finite(c) for c in center)
            or not all(_finite(c) for c in axis)
            or not _finite(major_r) or not _finite(minor_r)
            or major_r < 1e-9 or minor_r < 1e-9 or minor_r >= major_r):
        return None, None
    c = np.asarray(center, dtype=float)
    a = np.asarray(axis, dtype=float)
    d = pts - c
    t = d @ a
    dp = np.linalg.norm(d - np.outer(t, a), axis=1)
    res = np.sqrt((dp - major_r) ** 2 + t * t) - minor_r
    rms = _rms(res)
    if rms is None:
        return None, None
    params = {
        "center": [round(float(x), 6) for x in center],
        "axis": [round(float(x), 6) for x in axis],
        "major_radius_mm": round(float(major_r), 6),
        "minor_radius_mm": round(float(minor_r), 6),
    }
    return params, rms


def _grade(rms: float, rms_tol_mm: float) -> str:
    if rms <= 1e-4:
        return "exact"
    if rms <= 1e-2:
        return "tight"
    if rms <= rms_tol_mm:
        return "approx"
    return "unfit"


_CANDIDATE_ORDER = ("plane", "cylinder", "cone", "sphere", "torus")


def fit_regions(verts, tris, normals, areas, regions,
                rms_tol_mm: float = 0.05) -> list[dict]:
    """Per region: candidate fits + honest verdict. Shared with scan_to_brep.

    Returns JSON-safe dicts, one per region (same order as ``regions``).
    """
    import numpy as np

    out: list[dict] = []
    for reg in regions:
        idx = reg["_tri_indices"]
        v_idx = np.unique(tris[idx].ravel())
        pts = verts[v_idx]
        tri_n = normals[idx]
        tri_a = areas[idx]
        mean_n = (np.asarray(reg["mean_normal"], dtype=float)
                  if reg["mean_normal"] is not None else None)

        cand: dict[str, tuple[dict | None, float | None]] = {}
        cand["plane"] = _fit_plane(pts, mean_n)
        cand["cylinder"] = _fit_cylinder(pts, tri_n, tri_a)
        cand["cone"] = _fit_cone(pts)
        cand["sphere"] = _fit_sphere(pts)
        cand["torus"] = _fit_torus(pts)

        chosen_kind = None
        chosen_params: dict | None = None
        chosen_rms: float | None = None
        for kind in _CANDIDATE_ORDER:
            params, rms = cand[kind]
            if params is not None and rms is not None and rms <= rms_tol_mm:
                chosen_kind, chosen_params, chosen_rms = kind, params, rms
                break

        candidates_json = {
            k: (round(r, 6) if r is not None else None)
            for k, (_p, r) in cand.items()
        }

        entry: dict[str, Any] = {
            "id": int(reg["id"]),
            "n_triangles": int(reg["n_triangles"]),
            "n_points": int(len(pts)),
            "area_mm2": round(float(reg["area_mm2"]), 6),
            "candidates": candidates_json,
        }
        if chosen_kind is not None:
            entry.update({
                "kind": chosen_kind,
                "params": chosen_params,
                "rms_mm": round(float(chosen_rms), 9),
                "grade": _grade(float(chosen_rms), rms_tol_mm),
            })
        else:
            fitted = [(k, r) for k, (_p, r) in cand.items() if r is not None]
            best = min(fitted, key=lambda kr: kr[1]) if fitted else None
            entry.update({
                "kind": "freeform_unfit",
                "params": {},
                "rms_mm": None,
                "grade": "unfit",
                "best_candidate": (
                    {"kind": best[0], "rms_mm": round(best[1], 6)}
                    if best is not None else None),
            })
        out.append(entry)
    return out


def freeform_area_fraction(fits: list[dict]) -> float:
    total = sum(f["area_mm2"] for f in fits)
    if total <= 0:
        return 1.0
    unfit = sum(f["area_mm2"] for f in fits if f["kind"] == "freeform_unfit")
    return unfit / total


# --------------------------------------------------------------------------- #
# skill
# --------------------------------------------------------------------------- #

@skill(
    name="fit_region_surfaces",
    category="reverse_engineer",
    level="atomic",
    summary="Scan-to-CAD stage 2: per mesh region (mesh_segment_regions "
            "growing rerun internally), try analytic surfaces in fixed order "
            "plane→cylinder→cone→sphere→torus over the region's triangle "
            "VERTICES (module-local least squares — existing fit_* skills "
            "are face-based) and accept the first with RMS ≤ rms_tol_mm. "
            "Regions nothing fits are honestly kind='freeform_unfit'. "
            "Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["region_surface_fits"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.not_a_mesh", "fm.too_many_triangles"],
    cost_hint=0.5,
    post_conditions=[PostCondition(kind="body_present")],
)
class FitRegionSurfaces(SkillBase):
    class Args(BaseModel):
        angle_threshold_deg: float = Field(default=15.0, gt=0.0, le=90.0)
        weld_tolerance_mm: float = Field(default=1e-6, gt=0)
        linear_deflection_mm: float = Field(default=0.1, gt=0)
        angular_deflection_deg: float = Field(default=5.0, gt=0)
        max_triangles: int = Field(default=200000, ge=1)
        rms_tol_mm: float = Field(
            default=0.05, gt=0,
            description="A candidate surface is accepted when its RMS "
                        "residual over the region vertices is at or below "
                        "this. Above it for every candidate → "
                        "kind='freeform_unfit' (honest).")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        verts, tris, normals, areas, _valid, _region_of, regions, source = \
            segment_body(
                body,
                angle_threshold_deg=args.angle_threshold_deg,
                weld_tolerance_mm=args.weld_tolerance_mm,
                linear_deflection_mm=args.linear_deflection_mm,
                angular_deflection_deg=args.angular_deflection_deg,
                max_triangles=args.max_triangles,
            )
        fits = fit_regions(verts, tris, normals, areas, regions,
                           rms_tol_mm=args.rms_tol_mm)
        n_unfit = sum(1 for f in fits if f["kind"] == "freeform_unfit")
        extras = {
            "fit_region_surfaces": {
                "source": source,
                "n_regions": len(fits),
                "regions": fits,
                "n_freeform_unfit": n_unfit,
                "freeform_area_fraction": round(
                    freeform_area_fraction(fits), 6),
                "rms_tol_mm": float(args.rms_tol_mm),
                "segmentation": region_summary_json(regions),
                "method_note": _METHOD_NOTE,
            }
        }
        return SkillResult(body=body, history=EntityHistoryMap(),
                           extras=extras)
