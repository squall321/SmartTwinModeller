"""inspect_sink_mark_risk — atomic, read-only DFM inspect.

Detect injection-mold **sink-mark risks** caused by rib/boss features that are
too thick relative to the nominal wall.

Why this matters
    Sink marks happen where local material thickness is large enough that the
    last-to-solidify region pulls inward as the resin cools. The classic rule
    is: rib thickness should be ≤ 60% of the nominal wall thickness. Anything
    above that — and physically intersecting the wall it grows from — is a
    sink-mark candidate.

Algorithm
    1. Cast inward thickness rays from per-face UV samples (same machinery as
       `inspect_wall_thickness`).
    2. The ray hitting another body surface is the "intersecting wall" — if
       the measured local thickness exceeds
       ``rib_to_wall_ratio * nominal_wall_mm`` *and* is at least
       ``min_rib_thickness_mm``, record a risk entry.
       ``material_shrinkage_pct`` modulates the severity (higher shrinkage ⇒
       more pronounced sink mark).

extras schema
    extras["sink_mark_risks"] = [
        {
            "location": [x, y, z],          # mm, face-sample point
            "face_idx": int,
            "local_thickness_mm": float,
            "ratio_to_wall": float,         # local / nominal
            "severity": "low"|"medium"|"high",
            "recommendation": str,
        },
        ...
    ]
    extras["sink_mark_summary"] = {
        "nominal_wall_mm": float,
        "shrinkage_pct": float,
        "rib_to_wall_ratio_threshold": float,
        "risk_count": int,
        "severity_counts": {"low": n, "medium": n, "high": n},
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
    here = pathlib.Path(__file__).resolve()
    for root in here.parents:
        cand = root / "catalogs" / family / f"{name}.yaml"
        if cand.exists():
            return yaml.safe_load(cand.read_text())
    raise FileNotFoundError(f"catalog not found: {family}/{name}.yaml")


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _severity(ratio: float, shrinkage_pct: float) -> str:
    """ratio = local_thickness / nominal_wall. shrinkage_pct boosts severity.

    Mapping (with shrinkage 0.6%):
        ratio ∈ [0.6, 0.8) → low
        ratio ∈ [0.8, 1.0) → medium
        ratio ≥ 1.0        → high
    Shrinkage ≥ 1.0% bumps every band up one level (low→medium, medium→high).
    """
    if ratio >= 1.0:
        base = "high"
    elif ratio >= 0.8:
        base = "medium"
    else:
        base = "low"
    if shrinkage_pct >= 1.0:
        bump = {"low": "medium", "medium": "high", "high": "high"}
        return bump[base]
    return base


def _recommendation(severity: str) -> str:
    if severity == "high":
        return ("Reduce rib/boss thickness below 60% of the nominal wall, or "
                "core out the base to thin the local section.")
    if severity == "medium":
        return ("Trim the rib to ~60% of the wall, or add a small core-out at "
                "the junction.")
    return ("Acceptable — monitor for cosmetic sink on A-surface; consider a "
            "draft / fillet to break up the heavy section.")


def _measure_local_thickness(
    shape, face, u: float, v: float, *,
    max_thickness_check_mm: float,
    false_positive_floor_mm: float,
    start_offset_mm: float,
) -> tuple[tuple[float, float, float], float, tuple[float, float, float]] | None:
    """Return ((px,py,pz), thickness_mm, inward_unit_dir) for one (u,v) sample."""
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
    inward = (-nx / nmag, -ny / nmag, -nz / nmag)

    start = gp_Pnt(
        pt.X() + inward[0] * start_offset_mm,
        pt.Y() + inward[1] * start_offset_mm,
        pt.Z() + inward[2] * start_offset_mm,
    )
    try:
        clsf = BRepClass3d_SolidClassifier(shape, start, 1e-6)
        if clsf.State() == TopAbs_OUT:
            return None
    except Exception:
        return None

    try:
        line = gp_Lin(start, gp_Dir(inward[0], inward[1], inward[2]))
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
    thickness = best_t + start_offset_mm
    if thickness < false_positive_floor_mm:
        return None
    return ((pt.X(), pt.Y(), pt.Z()), float(thickness), inward)


@skill(
    name="inspect_sink_mark_risk",
    category="inspect",
    level="atomic",
    summary="Find rib/boss regions whose local thickness exceeds "
            "0.6 * nominal_wall_mm and physically intersect another wall — "
            "these are injection-mold sink-mark candidates. Severity is "
            "scaled by material shrinkage. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["sink_mark_report"],
    preserves=["body_topology"],
    manufacturing={
        "injection_mold_pa": {"extras": {"sink_mark_aware": True}},
    },
    failure_modes=["fm.no_faces", "fm.raycast_failed"],
    cost_hint=0.5,
    post_conditions=[PostCondition(kind="body_present")],
)
class InspectSinkMarkRisk(SkillBase):
    class Args(BaseModel):
        nominal_wall_mm: float = Field(gt=0.0, le=500.0)
        material_shrinkage_pct: float = Field(default=0.6, ge=0.0, le=10.0)
        samples_per_face: int = Field(default=9, ge=1, le=400)
        max_thickness_check_mm: float = Field(default=20.0, gt=0.0, le=1000.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        cat = _load("dfm_inspect", "default_thresholds")
        sm_cat = cat.get("sink_mark", {})
        wt_cat = cat["wall_thickness"]
        ratio_thr = float(sm_cat.get("rib_to_wall_ratio", 0.6))
        min_rib_t = float(sm_cat.get("min_rib_thickness_mm", 0.3))
        fp_floor = float(wt_cat["false_positive_floor_mm"])
        start_off = float(wt_cat["start_offset_mm"])

        shape = _occt_shape(body)
        faces = _all_faces(shape)

        risks: list[dict[str, Any]] = []
        severity_counts = {"low": 0, "medium": 0, "high": 0}

        # absolute threshold = max(ratio*wall, min_rib_thickness)
        abs_threshold = max(ratio_thr * args.nominal_wall_mm, min_rib_t)

        from OCP.BRepAdaptor import BRepAdaptor_Surface

        # cell-center UV sampling per face
        side = max(1, int(math.ceil(math.sqrt(args.samples_per_face))))

        for f_idx, face in enumerate(faces):
            try:
                surf = BRepAdaptor_Surface(face)
                u0, u1 = surf.FirstUParameter(), surf.LastUParameter()
                v0, v1 = surf.FirstVParameter(), surf.LastVParameter()
            except Exception:
                continue
            if u1 <= u0 or v1 <= v0:
                continue

            # collect sample points
            seen_locations: list[tuple[float, float, float]] = []
            for i in range(side):
                for j in range(side):
                    if len(seen_locations) >= args.samples_per_face:
                        break
                    fu = (i + 0.5) / side
                    fv = (j + 0.5) / side
                    u = u0 + (u1 - u0) * fu
                    v = v0 + (v1 - v0) * fv
                    res = _measure_local_thickness(
                        shape, face, u, v,
                        max_thickness_check_mm=float(args.max_thickness_check_mm),
                        false_positive_floor_mm=fp_floor,
                        start_offset_mm=start_off,
                    )
                    if res is None:
                        continue
                    (px, py, pz), thickness, _inward = res
                    if thickness <= abs_threshold:
                        continue
                    # By construction, _measure_local_thickness already proved
                    # the inward ray strikes another body surface within the
                    # solid (that hit *is* the intersecting wall). The thick
                    # region therefore intersects another wall — flag it.

                    # de-dup: skip if a previous location on this face is
                    # within 0.5 mm
                    too_close = False
                    for loc in seen_locations:
                        d = math.sqrt(
                            (loc[0] - px) ** 2
                            + (loc[1] - py) ** 2
                            + (loc[2] - pz) ** 2
                        )
                        if d < 0.5:
                            too_close = True
                            break
                    if too_close:
                        continue
                    seen_locations.append((px, py, pz))

                    ratio = thickness / max(args.nominal_wall_mm, 1e-9)
                    sev = _severity(ratio, args.material_shrinkage_pct)
                    severity_counts[sev] += 1
                    risks.append({
                        "location": [round(px, 4), round(py, 4), round(pz, 4)],
                        "face_idx": f_idx,
                        "local_thickness_mm": round(thickness, 4),
                        "ratio_to_wall": round(ratio, 4),
                        "severity": sev,
                        "recommendation": _recommendation(sev),
                    })

        summary = {
            "nominal_wall_mm": float(args.nominal_wall_mm),
            "shrinkage_pct": float(args.material_shrinkage_pct),
            "rib_to_wall_ratio_threshold": ratio_thr,
            "risk_count": len(risks),
            "severity_counts": severity_counts,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "sink_mark_risks": risks,
                "sink_mark_summary": summary,
            },
        )
