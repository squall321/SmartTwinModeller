"""inspect_wall_thickness — atomic, read-only DFM inspect.

For every face of the body, sample a UV grid, fire an inward ray (along the
negative outward normal) and record the distance to the nearest opposite face.
That distance is the local wall thickness at that sample.

A per-face min/mean/max wall thickness is aggregated, plus global extrema and
explicit lists of ``thin_regions`` / ``thick_regions`` (those that crossed the
thresholds loaded from ``catalogs/dfm_inspect/default_thresholds.yaml``).

This is a refined skill-layer wrapper around the helper in
``phone_designer.manufacturing.dfm.wall_thickness`` — that helper only emits
``DFMViolation`` against a single threshold; this skill returns the full
per-face picture so planners and LLM agents can reason about *where* and *how
much* a part is too thin / too thick.

extras schema:
    extras["wall_thickness"] = {
        "global_min": float,
        "global_max": float,
        "global_mean": float,
        "thin_threshold_mm": float,
        "thick_threshold_mm": float,
        "per_face": [
            {"idx": int, "min": float, "mean": float, "max": float,
             "samples": int},
            ...
        ],
        "thin_regions":  [{"face_idx": i, "point": [x, y, z],
                            "thickness": float}, ...],
        "thick_regions": [{"face_idx": i, "point": [x, y, z],
                            "thickness": float}, ...],
    }
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
    root = pathlib.Path(__file__).resolve().parents[5]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _grid_uvs(u_min: float, u_max: float, v_min: float, v_max: float,
              samples_per_face: int) -> list[tuple[float, float]]:
    """Uniform UV cell-centers. ``samples_per_face`` → grid side = ceil(sqrt(n))."""
    n = max(1, int(samples_per_face))
    side = max(1, int(math.ceil(math.sqrt(n))))
    out: list[tuple[float, float]] = []
    for i in range(side):
        for j in range(side):
            fu = (i + 0.5) / side
            fv = (j + 0.5) / side
            u = u_min + (u_max - u_min) * fu
            v = v_min + (v_max - v_min) * fv
            out.append((u, v))
            if len(out) >= n:
                return out
    return out


def _measure_thickness_at(
    shape, face, u: float, v: float, *,
    max_thickness_check_mm: float,
    false_positive_floor_mm: float,
    start_offset_mm: float,
) -> tuple[tuple[float, float, float], float] | None:
    """Return ((px,py,pz), thickness) for a single (u,v) sample, or None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    from OCP.TopAbs import TopAbs_OUT, TopAbs_REVERSED

    surf = BRepAdaptor_Surface(face)
    try:
        pt = surf.Value(u, v)
        d1u = surf.DN(u, v, 1, 0)
        d1v = surf.DN(u, v, 0, 1)
    except Exception:
        return None

    nx = d1u.Y() * d1v.Z() - d1u.Z() * d1v.Y()
    ny = d1u.Z() * d1v.X() - d1u.X() * d1v.Z()
    nz = d1u.X() * d1v.Y() - d1u.Y() * d1v.X()
    nmag = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nmag < 1e-9:
        return None
    if face.Orientation() == TopAbs_REVERSED:
        nx, ny, nz = -nx, -ny, -nz
    inward = gp_Dir(-nx / nmag, -ny / nmag, -nz / nmag)

    start = gp_Pnt(
        pt.X() + inward.X() * start_offset_mm,
        pt.Y() + inward.Y() * start_offset_mm,
        pt.Z() + inward.Z() * start_offset_mm,
    )
    try:
        clsf = BRepClass3d_SolidClassifier(shape, start, 1e-6)
        if clsf.State() == TopAbs_OUT:
            return None
    except Exception:
        return None

    line = gp_Lin(start, inward)
    try:
        inter = BRepIntCurveSurface_Inter()
        inter.Init(shape, line, 1e-6)
    except Exception:
        return None

    best_t = None
    while inter.More():
        t = inter.W()
        if 0 < t < max_thickness_check_mm:
            if best_t is None or t < best_t:
                best_t = t
        inter.Next()
    if best_t is None:
        return None
    thickness = best_t + start_offset_mm  # add the seed offset back in
    if thickness < false_positive_floor_mm:
        return None
    return ((pt.X(), pt.Y(), pt.Z()), float(thickness))


@skill(
    name="inspect_wall_thickness",
    category="inspect",
    level="atomic",
    summary="Ray-march from each face along the inward normal and record the "
            "distance to the nearest opposite face — that is the local wall "
            "thickness. Aggregates per-face min/mean/max and lists thin / "
            "thick regions against catalog thresholds. Read-only — body "
            "unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["wall_thickness_report"],
    preserves=["body_topology"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8},
    },
    failure_modes=["fm.no_faces", "fm.raycast_failed"],
    cost_hint=0.5,
    post_conditions=[PostCondition(kind="body_present")],
)
class InspectWallThickness(SkillBase):
    class Args(BaseModel):
        samples_per_face: int = Field(default=50, ge=1, le=2000)
        max_thickness_check_mm: float = Field(default=20.0, gt=0, le=1000.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        cat = _load("dfm_inspect", "default_thresholds")
        wt_cat = cat["wall_thickness"]
        thin_thr = float(wt_cat["thin_threshold_mm"])
        thick_thr = float(wt_cat["thick_threshold_mm"])
        fp_floor = float(wt_cat["false_positive_floor_mm"])
        start_off = float(wt_cat["start_offset_mm"])

        shape = _occt_shape(body)
        faces = _all_faces(shape)

        per_face: list[dict[str, Any]] = []
        thin_regions: list[dict[str, Any]] = []
        thick_regions: list[dict[str, Any]] = []
        global_min = math.inf
        global_max = 0.0
        global_sum = 0.0
        global_n = 0

        from OCP.BRepAdaptor import BRepAdaptor_Surface

        for idx, face in enumerate(faces):
            try:
                surf = BRepAdaptor_Surface(face)
                u0, u1 = surf.FirstUParameter(), surf.LastUParameter()
                v0, v1 = surf.FirstVParameter(), surf.LastVParameter()
            except Exception:
                continue
            if u1 <= u0 or v1 <= v0:
                continue

            uvs = _grid_uvs(u0, u1, v0, v1, args.samples_per_face)
            f_min = math.inf
            f_max = 0.0
            f_sum = 0.0
            f_n = 0

            for u, v in uvs:
                res = _measure_thickness_at(
                    shape, face, u, v,
                    max_thickness_check_mm=float(args.max_thickness_check_mm),
                    false_positive_floor_mm=fp_floor,
                    start_offset_mm=start_off,
                )
                if res is None:
                    continue
                pt, t = res
                f_n += 1
                f_sum += t
                if t < f_min:
                    f_min = t
                if t > f_max:
                    f_max = t
                if t < thin_thr:
                    thin_regions.append({
                        "face_idx": idx,
                        "point": [round(c, 4) for c in pt],
                        "thickness": round(t, 4),
                    })
                if t > thick_thr:
                    thick_regions.append({
                        "face_idx": idx,
                        "point": [round(c, 4) for c in pt],
                        "thickness": round(t, 4),
                    })

            if f_n == 0:
                continue
            per_face.append({
                "idx": idx,
                "min": round(f_min, 4),
                "mean": round(f_sum / f_n, 4),
                "max": round(f_max, 4),
                "samples": f_n,
            })
            global_sum += f_sum
            global_n += f_n
            if f_min < global_min:
                global_min = f_min
            if f_max > global_max:
                global_max = f_max

        if global_n == 0:
            global_min = 0.0
            global_mean = 0.0
        else:
            global_mean = global_sum / global_n
        if not math.isfinite(global_min):
            global_min = 0.0

        report = {
            "global_min": round(global_min, 4),
            "global_max": round(global_max, 4),
            "global_mean": round(global_mean, 4),
            "thin_threshold_mm": thin_thr,
            "thick_threshold_mm": thick_thr,
            "per_face": per_face,
            "thin_regions": thin_regions,
            "thick_regions": thick_regions,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"wall_thickness": report},
        )
