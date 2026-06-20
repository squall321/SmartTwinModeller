"""detect_sheet_metal — inspect macro, read-only (2026-06-20).

NEW FRONTIER: sheet-metal reverse-engineering. Phone/watch mid-frames, brackets
and shields are bent sheet metal — a domain the pipeline did not cover. This
recognises a constant-thickness bent-sheet part and recovers its BEND TABLE:
thickness, and per bend the radius, angle, bend-line length, and the
press-brake allowances (bend allowance + bend deduction) a quoter needs.

HONEST scope (the anti-fake-precision rule applies):
  * thickness, bend RADIUS, bend ANGLE and bend-line LENGTH are MEASURED from
    the B-rep — the bend angle is read straight off the bend cylinder's arc
    extent (no normal-sign ambiguity), the line length from its developed area.
  * bend allowance / deduction depend on the K-FACTOR (neutral-axis position),
    which is a material/process assumption, NOT geometry — so it is a documented
    default (0.44), overridable, and the result is graded 'estimate'.
  * the full 2D FLAT-PATTERN nest (a true unfold to a blank outline) is NOT
    generated in v1 — the bend deductions + outer dimensions give the developed
    length, but the nest itself is an explicit honest limit (see `flat_pattern`).

extras["sheet_metal"] = {
    "is_sheet_metal", "confidence", "thickness_mm",
    "n_bends", "bends": [{radius_mm, angle_deg, bend_line_length_mm,
                          bend_allowance_mm, bend_deduction_mm, axis_dir,
                          surfaces}],
    "k_factor", "total_bend_allowance_mm",
    "flat_pattern": {note, bends_share_single_axis, k_factor,
                     total_bend_allowance_mm},
    "assumptions": [...], "grade": "estimate",
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

_PARALLEL_TOL = 0.99   # |dot(n1,n2)| above this → parallel planes
# A genuine press-brake bend turns the sheet through [~5°, ~170°]. Below that is
# numerical noise; a ~180° arc is a ROUNDED-OVER EDGE or a folded hem (flanges
# end up parallel), NOT a bend — and a ~360° arc is a hole. Excluding ≥170°
# kills the extrusion edge-round false positives (anti-fake-accuracy).
_BEND_ARC_MIN = math.radians(5.0)
_BEND_ARC_MAX = math.radians(170.0)
_SHEET_COVERAGE_MIN = 0.7  # sheet metal = most planar area is the two thin faces


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _unit(v):
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / m, v[1] / m, v[2] / m) if m > 1e-12 else (0.0, 0.0, 0.0)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _face_geometry(face):
    """(kind, area, centroid, payload) — kind in {'plane','cylinder','other'}.

    plane payload = normal(unit); cylinder payload = (radius, axis_dir_unit,
    axis_loc, arc_rad).
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
    from OCP.GProp import GProp_GProps

    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    area = float(g.Mass())
    c = g.CentreOfMass()
    centroid = (c.X(), c.Y(), c.Z())
    surf = BRepAdaptor_Surface(face)
    t = surf.GetType()
    if t == GeomAbs_Plane:
        n = surf.Plane().Axis().Direction()
        return "plane", area, centroid, _unit((n.X(), n.Y(), n.Z()))
    if t == GeomAbs_Cylinder:
        cyl = surf.Cylinder()
        d = cyl.Axis().Direction()
        loc = cyl.Location()
        arc = abs(surf.LastUParameter() - surf.FirstUParameter())
        return "cylinder", area, centroid, (
            float(cyl.Radius()), _unit((d.X(), d.Y(), d.Z())),
            (loc.X(), loc.Y(), loc.Z()), float(arc))
    return "other", area, centroid, None


def _bbox(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, bb)
    if bb.IsVoid():
        return None
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return (xmax - xmin, ymax - ymin, zmax - zmin)


def _thickness(planes, bbox_ref):
    """Dominant perpendicular gap between parallel, laterally-overlapping planar
    pairs, weighted by participating area. Returns (thickness, coverage_area).

    ``bbox_ref`` is the MEDIAN bbox extent (the smaller of the two sheet "face"
    dimensions) — thickness is small relative to THAT, not relative to the
    minimum extent (for a flat/barely-bent blank the thickness IS the minimum).
    """
    upper = 0.4 * bbox_ref if bbox_ref else None
    bins: dict[float, float] = {}
    for i in range(len(planes)):
        ni, ci, ai = planes[i]["normal"], planes[i]["centroid"], planes[i]["area"]
        for j in range(i + 1, len(planes)):
            nj, cj, aj = planes[j]["normal"], planes[j]["centroid"], planes[j]["area"]
            if abs(_dot(ni, nj)) < _PARALLEL_TOL:
                continue
            diff = (cj[0] - ci[0], cj[1] - ci[1], cj[2] - ci[2])
            gap = abs(_dot(diff, ni))
            if gap < 0.05 or (upper and gap > upper):
                continue
            # lateral overlap: the in-plane offset should be within the faces'
            # span, else these are two separate (offset) walls, not a thickness.
            lat = math.sqrt(max(_dot(diff, diff) - gap * gap, 0.0))
            span = math.sqrt(max(min(ai, aj), 1e-9))
            if lat > 2.0 * span:
                continue
            key = round(gap, 1)
            bins[key] = bins.get(key, 0.0) + ai + aj
    if not bins:
        return None, 0.0
    best = max(bins.items(), key=lambda kv: kv[1])
    return best[0], best[1]


def _collect_bends(cyls, thickness):
    """Group concentric bend cylinders (inner r + outer r+t share an axis line)
    into one bend each; exclude full-circle holes. Returns bend dicts."""
    # keep only genuine press-brake bend arcs (exclude rounded edges / hems at
    # ~180°, holes at ~360°, and sub-5° numerical noise)
    cand = [c for c in cyls if _BEND_ARC_MIN < c["arc"] < _BEND_ARC_MAX]
    used = [False] * len(cand)
    bends = []
    for i, c in enumerate(cand):
        if used[i]:
            continue
        group = [c]
        used[i] = True
        for j in range(i + 1, len(cand)):
            if used[j]:
                continue
            d = cand[j]
            if abs(_dot(c["axis"], d["axis"])) < _PARALLEL_TOL:
                continue
            # collinear axes (concentric): perpendicular distance between axis
            # lines ~ 0
            off = (d["loc"][0] - c["loc"][0], d["loc"][1] - c["loc"][1],
                   d["loc"][2] - c["loc"][2])
            along = _dot(off, c["axis"])
            perp = math.sqrt(max(_dot(off, off) - along * along, 0.0))
            if perp > 0.05 * max(c["radius"], 1.0):
                continue
            if abs(c["arc"] - d["arc"]) > math.radians(15.0):
                continue
            group.append(d)
            used[j] = True
        inner = min(group, key=lambda x: x["radius"])
        arc_rad = sum(x["arc"] for x in group) / len(group)
        # bend-line length = developed area / (radius * arc) of the inner surface
        line_len = (inner["area"] / (inner["radius"] * arc_rad)
                    if inner["radius"] * arc_rad > 1e-9 else 0.0)
        bends.append({
            "radius_mm": round(inner["radius"], 4),
            "angle_deg": round(math.degrees(arc_rad), 2),
            "bend_line_length_mm": round(line_len, 3),
            "axis_dir": [round(v, 4) for v in inner["axis"]],
            "surfaces": len(group),  # 2 = concentric inner+outer recovered
            "_arc_rad": arc_rad,
        })
    return bends


@skill(
    name="detect_sheet_metal",
    category="inspect",
    level="macro",
    summary="Recognise a constant-thickness bent sheet-metal part and recover its "
            "BEND TABLE: thickness + per-bend radius, angle (from the bend "
            "cylinder's arc), bend-line length and press-brake allowances (bend "
            "allowance / deduction). Thickness/radius/angle are measured; the "
            "allowances depend on an assumed K-factor → result_grade='estimate'. "
            "Full 2D flat-pattern nest is a documented v1 limit.",
    selector_kinds=[],
    history_rules={},
    produces_features=["sheet_metal"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.4,
    result_grade="estimate",
    post_conditions=[PostCondition(kind="body_present")],
)
class DetectSheetMetal(SkillBase):
    class Args(BaseModel):
        k_factor: float = Field(
            default=0.44, ge=0.0, le=0.5,
            description="Neutral-axis K-factor for bend-allowance (material/process "
                        "assumption; 0.33 soft air-bend … 0.5 hard). Default 0.44.",
        )
        max_faces: int = Field(
            default=2000, ge=1,
            description="Skip (honest sentinel) above this face count.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        shape = _occt_shape(body)
        faces = _all_faces(shape)
        if len(faces) > args.max_faces:
            return SkillResult(body=body, history=EntityHistoryMap(), extras={
                "sheet_metal": {"is_sheet_metal": False, "skipped": True,
                                "reason": "too_big", "face_count": len(faces),
                                "limit": args.max_faces}})

        planes, cyls = [], []
        for f in faces:
            kind, area, centroid, payload = _face_geometry(f)
            if kind == "plane":
                planes.append({"normal": payload, "centroid": centroid, "area": area})
            elif kind == "cylinder":
                r, axis, loc, arc = payload
                cyls.append({"radius": r, "axis": axis, "loc": loc, "arc": arc,
                             "area": area, "centroid": centroid})

        size = _bbox(shape)
        # median extent = the smaller of the two sheet "face" dimensions; the
        # reference both thickness-gap and thin-ness are judged against.
        bbox_ref = sorted(size)[1] if size else None
        assumptions: list[str] = [
            "thickness, bend radius/angle/line-length are MEASURED from the B-rep "
            "(angle = bend-cylinder arc extent).",
            f"bend allowance/deduction use an ASSUMED K-factor {args.k_factor} "
            "(material/process, not geometry) → graded 'estimate'.",
            "developed length/area = sheet mid-surface (flange area + neutral bend "
            "strips); a true 2D blank OUTLINE with cutouts is the remaining limit.",
        ]

        thickness, cover_area = _thickness(planes, bbox_ref)
        total_planar_area = sum(p["area"] for p in planes) or 1.0
        coverage = min(cover_area / total_planar_area, 1.0)

        bends = _collect_bends(cyls, thickness)

        # bend allowance / deduction per bend (need thickness)
        t = thickness or 0.0
        total_ba = 0.0
        for b in bends:
            arc_rad = b.pop("_arc_rad")
            r = b["radius_mm"]
            ba = arc_rad * (r + args.k_factor * t)
            bd = 2.0 * (r + t) * math.tan(arc_rad / 2.0) - ba
            b["bend_allowance_mm"] = round(ba, 4)
            b["bend_deduction_mm"] = round(bd, 4)
            total_ba += ba

        # is_sheet_metal: a consistent thin thickness covering most planar area,
        # thin relative to the part, and at least the look of a sheet (bends OR
        # high coverage of a thin constant wall).
        thin = bool(thickness and bbox_ref and thickness < 0.34 * bbox_ref)
        is_sheet = bool(thin and coverage >= _SHEET_COVERAGE_MIN)
        confidence = round(coverage * (1.0 if thin else 0.4), 3) if thickness else 0.0
        if not is_sheet:
            assumptions.append(
                f"NOT classified sheet-metal (thin={thin}, planar coverage="
                f"{round(coverage,2)}, thickness={thickness}).")

        # ── flat pattern: developed blank AREA + (single-axis) developed LENGTH ─
        # One side of the sheet = half the thickness-pair face area (cover_area/2);
        # each bend adds its neutral-strip developed area = bend_allowance × line.
        # For an L-bracket this reproduces the hand calc (Σ flange flats + Σ BA).
        axes = {tuple(b["axis_dir"]) for b in bends}
        single_axis = len(axes) <= 1
        flat_pattern: dict[str, Any] = {
            "k_factor": args.k_factor,
            "total_bend_allowance_mm": round(total_ba, 4),
            "bends_share_single_axis": single_axis,
        }
        if thickness:
            bend_dev_area = sum(b["bend_allowance_mm"] * b["bend_line_length_mm"]
                                for b in bends)
            blank_area = cover_area / 2.0 + bend_dev_area
            if bends and single_axis:
                blank_width = max(b["bend_line_length_mm"] for b in bends)
            elif size:
                blank_width = sorted(size)[1]   # median = smaller sheet-face dim
            else:
                blank_width = None
            developed_length = (round(blank_area / blank_width, 3)
                                if (single_axis and blank_width and blank_width > 1e-6)
                                else None)
            flat_pattern.update({
                "flat_blank_area_mm2": round(blank_area, 2),
                "developed_length_mm": developed_length,
                "blank_width_mm": round(blank_width, 3) if blank_width else None,
                "note": ("developed length/area from the sheet mid-surface (flange "
                         "area + neutral bend strips at K-factor); developed_length "
                         "is single-bend-axis only. A true 2D outline with cutouts "
                         "is the remaining limit."),
            })
        else:
            flat_pattern.update({
                "flat_blank_area_mm2": None, "developed_length_mm": None,
                "blank_width_mm": None,
                "note": "no constant thickness → not a sheet blank.",
            })

        out = {
            "is_sheet_metal": is_sheet,
            "confidence": confidence,
            "thickness_mm": round(thickness, 4) if thickness else None,
            "n_bends": len(bends),
            "bends": bends,
            "k_factor": args.k_factor,
            "total_bend_allowance_mm": round(total_ba, 4),
            "flat_pattern": flat_pattern,
            "bbox_mm": [round(v, 3) for v in size] if size else None,
            "assumptions": assumptions,
            "grade": "estimate",
        }
        return SkillResult(body=body, history=EntityHistoryMap(),
                           extras={"sheet_metal": out})
