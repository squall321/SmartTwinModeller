"""measure_assembly_fit — inspect macro, read-only (2026-06-20).

The deeper half of fit recognition: where ``recognize_fits`` takes the mating Ø
as an ARGUMENT, this MEASURES a real assembled fit from geometry. Given one body
that is an assembly (a compound of ≥2 solids — e.g. a pin inserted in a housing
bore), it splits the solids, finds each solid's cylindrical bores and shafts,
matches a bore in one solid to a COAXIAL shaft in another, and measures the
actual clearance — then names the nearest standard ISO 286 fit.

HONEST grade: the clearance here is **measured** from the B-rep (both diameters
+ the real coaxial gap), and the fit_type (clearance / transition / interference)
follows from the sign of the real gap — so result_grade='measured', NOT a
recommendation. The "nearest standard fit" is an ISO 286 reference label layered
on top. Bore-vs-shaft is decided robustly by a radial point-inside-solid probe
(material outside the cylinder → bore; inside → shaft), not the orientation flag
(which STEP imports flip unreliably).

extras["assembly_fit"] = {
    "n_solids", "n_fits",
    "fits": [{hole_solid, shaft_solid, axis_dir, axis_origin,
              hole_mm, shaft_mm, actual_clearance_mm, fit_type,
              nearest_standard_fit:{designation, fit_type, clearance_mm,
                                    midpoint_mm, delta_mm}}],
    "assumptions": [...], "grade": "measured",
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

_AXIS_PARALLEL = 0.999    # |dot| above this → parallel axes
_AXIS_COLLINEAR_MM = 0.15  # perpendicular distance between axis lines for coaxial


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _unit(v):
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / m, v[1] / m, v[2] / m) if m > 1e-12 else (0.0, 0.0, 0.0)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _perp(axis):
    """A unit vector perpendicular to ``axis`` (robust for any axis)."""
    ref = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 1.0, 0.0)
    c = (axis[1] * ref[2] - axis[2] * ref[1],
         axis[2] * ref[0] - axis[0] * ref[2],
         axis[0] * ref[1] - axis[1] * ref[0])
    return _unit(c)


def _point_inside(solid, p, tol=1e-6):
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_State

    try:
        clf = BRepClass3d_SolidClassifier(solid)
        clf.Perform(gp_Pnt(p[0], p[1], p[2]), tol)
        return clf.State() == TopAbs_State.TopAbs_IN
    except Exception:
        return False


def _axial_extent(face, axis, origin):
    """[amin, amax] = projection of the face's bbox corners onto the axis."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(face, bb)
    if bb.IsVoid():
        return None
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    projs = []
    for x in (xmin, xmax):
        for y in (ymin, ymax):
            for z in (zmin, zmax):
                projs.append((x - origin[0]) * axis[0] + (y - origin[1]) * axis[1]
                             + (z - origin[2]) * axis[2])
    return [min(projs), max(projs)]


def _cyl_features(solid):
    """Cylindrical faces of ``solid`` as bore/shaft records."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.GProp import GProp_GProps

    from phone_designer.skills._resolvers import _all_faces

    out = []
    for f in _all_faces(solid):
        s = BRepAdaptor_Surface(f)
        if s.GetType() != GeomAbs_Cylinder:
            continue
        cyl = s.Cylinder()
        d = cyl.Axis().Direction()
        loc = cyl.Location()
        axis = _unit((d.X(), d.Y(), d.Z()))
        origin = (loc.X(), loc.Y(), loc.Z())
        radius = float(cyl.Radius())
        g = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, g)
        c = g.CentreOfMass()
        centroid = (c.X(), c.Y(), c.Z())
        # radial direction at the face: from axis to centroid, else any perp
        t = _dot((centroid[0] - origin[0], centroid[1] - origin[1],
                  centroid[2] - origin[2]), axis)
        a_pt = (origin[0] + t * axis[0], origin[1] + t * axis[1],
                origin[2] + t * axis[2])
        rad = (centroid[0] - a_pt[0], centroid[1] - a_pt[1], centroid[2] - a_pt[2])
        rm = math.sqrt(_dot(rad, rad))
        rdir = (rad[0] / rm, rad[1] / rm, rad[2] / rm) if rm > 1e-6 else _perp(axis)
        delta = max(0.05, 0.02 * radius)
        p_out = tuple(a_pt[i] + (radius + delta) * rdir[i] for i in range(3))
        p_in = tuple(a_pt[i] + (radius - delta) * rdir[i] for i in range(3))
        io, ii = _point_inside(solid, p_out), _point_inside(solid, p_in)
        if io and not ii:
            kind = "bore"
        elif ii and not io:
            kind = "shaft"
        else:
            continue  # ambiguous — skip
        extent = _axial_extent(f, axis, origin)
        out.append({"kind": kind, "radius": radius, "axis": axis,
                    "origin": origin, "extent": extent})
    return out


def _planar_features(solid, max_faces=300):
    """Parallel planar-face pairs of ``solid`` as slot/key WIDTH features.

    Two parallel planar faces a width W apart are a prismatic feature. Classified
    by their OUTWARD (orientation-corrected) normals: a SLOT's two walls face
    TOWARD each other (into the gap), a KEY / tongue (or body bulk) faces AWAY.
    This is robust where a midplane point-inside probe is not — e.g. a block with
    a bore through its centre would read the centre as 'outside' and fake a slot.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_REVERSED

    from phone_designer.skills._resolvers import _all_faces

    planes = []
    for f in _all_faces(solid):
        s = BRepAdaptor_Surface(f)
        if s.GetType() != GeomAbs_Plane:
            continue
        n = s.Plane().Axis().Direction()
        nv = _unit((n.X(), n.Y(), n.Z()))
        if f.Orientation() == TopAbs_REVERSED:
            nv = (-nv[0], -nv[1], -nv[2])   # outward normal
        g = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, g)
        c = g.CentreOfMass()
        planes.append((nv, (c.X(), c.Y(), c.Z()), float(g.Mass())))
        if len(planes) > max_faces:
            break
    feats = []
    for i in range(len(planes)):
        na, ca, aa = planes[i]
        for j in range(i + 1, len(planes)):
            nb, cb, ab = planes[j]
            if abs(_dot(na, nb)) < 0.99:   # parallel
                continue
            ab_vec = (cb[0] - ca[0], cb[1] - ca[1], cb[2] - ca[2])
            width = abs(_dot(ab_vec, na))
            lateral = math.sqrt(max(_dot(ab_vec, ab_vec) - width * width, 0.0))
            if width < 0.5 or lateral > 2.0 * math.sqrt(max(min(aa, ab), 1e-9)):
                continue
            da, db = _dot(na, ab_vec), _dot(nb, ab_vec)
            if da > 0 and db < 0:
                kind = "slot"        # outward normals face each other → void between
            elif da < 0 and db > 0:
                kind = "key"         # face away → material between (tongue/bulk)
            else:
                continue
            center = tuple((ca[k] + cb[k]) / 2.0 for k in range(3))
            feats.append({"kind": kind, "width": width, "normal": na,
                          "center": center})
    return feats


def _coaxial(b, s):
    """Parallel + collinear axes, with overlapping axial extents."""
    if abs(_dot(b["axis"], s["axis"])) < _AXIS_PARALLEL:
        return False
    off = (s["origin"][0] - b["origin"][0], s["origin"][1] - b["origin"][1],
           s["origin"][2] - b["origin"][2])
    along = _dot(off, b["axis"])
    perp = math.sqrt(max(_dot(off, off) - along * along, 0.0))
    if perp > _AXIS_COLLINEAR_MM:
        return False
    eb, es = b.get("extent"), s.get("extent")
    if eb and es:
        # project both onto b's axis (origins differ by `along` on the axis)
        lo = max(eb[0], es[0] + along)
        hi = min(eb[1], es[1] + along)
        if hi - lo < 0.5:  # need real axial overlap
            return False
    return True


@skill(
    name="measure_assembly_fit",
    category="inspect",
    level="macro",
    summary="Measure a real assembled fit: split an assembly's solids and match a "
            "bore↔shaft (cylindrical) OR a slot↔key (prismatic) across solids, "
            "measuring the actual clearance — then name the nearest standard ISO "
            "286 fit. Clearance + fit_type are MEASURED from geometry "
            "(result_grade='measured'); bore/shaft via a radial point-inside "
            "probe, slot/key via outward-normal facing.",
    selector_kinds=[],
    history_rules={},
    produces_features=["assembly_fit"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.5,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class MeasureAssemblyFit(SkillBase):
    class Args(BaseModel):
        radius_tol_mm: float = Field(
            default=0.6, ge=0.0,
            description="Max |bore_r − shaft_r| to consider a mating fit "
                        "(coaxial cylinders further apart in radius are unrelated).",
        )
        max_solids: int = Field(
            default=400, ge=2,
            description="Skip (honest sentinel) above this solid count.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.assembly._compound import iter_solid_components
        from phone_designer.skills.inspect.recognize_fits import (
            _classify_measured_fit,
        )

        shape = _occt_shape(body)
        solids = list(iter_solid_components(shape))
        assumptions: list[str] = [
            "clearance is MEASURED between coaxial bore↔shaft (cylindrical) or "
            "coplanar slot↔key (prismatic) across two solids; fit_type follows "
            "from the real gap sign.",
            "bore vs shaft via a radial point-inside-solid probe; slot vs key via "
            "outward-normal facing (walls face in = slot, flanks face out = key).",
            "nearest standard fit is an ISO 286 reference label (size ≤ 180mm).",
        ]
        if len(solids) < 2:
            assumptions.append(f"only {len(solids)} solid(s) — need ≥2 for a fit.")
            return SkillResult(body=body, history=EntityHistoryMap(), extras={
                "assembly_fit": {"n_solids": len(solids), "n_fits": 0,
                                 "fits": [], "assumptions": assumptions,
                                 "grade": "measured"}})
        if len(solids) > args.max_solids:
            return SkillResult(body=body, history=EntityHistoryMap(), extras={
                "assembly_fit": {"skipped": True, "reason": "too_big",
                                 "n_solids": len(solids), "limit": args.max_solids}})

        # collect cylindrical bores/shafts tagged with their solid index
        bores, shafts = [], []
        for si, sol in enumerate(solids):
            for c in _cyl_features(sol):
                c["solid"] = si
                (bores if c["kind"] == "bore" else shafts).append(c)

        fits = []
        used_shaft = [False] * len(shafts)
        for b in bores:
            best = None
            for j, s in enumerate(shafts):
                if used_shaft[j] or s["solid"] == b["solid"]:
                    continue
                if abs(b["radius"] - s["radius"]) > args.radius_tol_mm:
                    continue
                if not _coaxial(b, s):
                    continue
                score = abs(b["radius"] - s["radius"])
                if best is None or score < best[0]:
                    best = (score, j, s)
            if best is None:
                continue
            _, j, s = best
            used_shaft[j] = True
            hole_mm = round(2.0 * b["radius"], 4)
            shaft_mm = round(2.0 * s["radius"], 4)
            mf = _classify_measured_fit(hole_mm, shaft_mm)
            entry = {
                "geometry": "cylindrical",
                "hole_solid": b["solid"],
                "shaft_solid": s["solid"],
                "axis_dir": [round(v, 4) for v in b["axis"]],
                "axis_origin": [round(v, 4) for v in b["origin"]],
                "hole_mm": hole_mm,
                "shaft_mm": shaft_mm,
                "actual_clearance_mm": round(hole_mm - shaft_mm, 4),
            }
            if mf is not None:
                entry["fit_type"] = mf["fit_type"]
                entry["nearest_standard_fit"] = mf["nearest_standard_fit"]
            else:
                entry["fit_type"] = ("clearance" if hole_mm > shaft_mm
                                     else "interference" if hole_mm < shaft_mm
                                     else "transition")
                assumptions.append(
                    f"Ø{hole_mm} outside ISO table — fit_type only, no standard fit.")
            fits.append(entry)

        # ── prismatic (key / keyway) fits: a SLOT in one solid + a coplanar KEY
        #    in another, matched by width. Same ISO machinery (IT grades apply to
        #    a width as much as a diameter). ──────────────────────────────────
        prism = []
        for si, sol in enumerate(solids):
            for pf in _planar_features(sol):
                pf["solid"] = si
                prism.append(pf)
        slots = [f for f in prism if f["kind"] == "slot"]
        keys = [f for f in prism if f["kind"] == "key"]
        used_key = [False] * len(keys)
        for sl in slots:
            best = None
            for k, ky in enumerate(keys):
                if used_key[k] or ky["solid"] == sl["solid"]:
                    continue
                if abs(_dot(sl["normal"], ky["normal"])) < 0.99:
                    continue
                if abs(sl["width"] - ky["width"]) > args.radius_tol_mm:
                    continue
                dist = math.sqrt(sum((sl["center"][i] - ky["center"][i]) ** 2
                                     for i in range(3)))
                if dist > max(sl["width"], ky["width"]) + 5.0:  # co-located
                    continue
                score = abs(sl["width"] - ky["width"])
                if best is None or score < best[0]:
                    best = (score, k, ky)
            if best is None:
                continue
            _, k, ky = best
            used_key[k] = True
            wf = round(sl["width"], 4)
            wm = round(ky["width"], 4)
            mf = _classify_measured_fit(wf, wm)
            entry = {
                "geometry": "prismatic",
                "slot_solid": sl["solid"],
                "key_solid": ky["solid"],
                "axis_dir": [round(v, 4) for v in sl["normal"]],
                "width_mm": wf,
                "key_width_mm": wm,
                "actual_clearance_mm": round(wf - wm, 4),
            }
            if mf is not None:
                entry["fit_type"] = mf["fit_type"]
                entry["nearest_standard_fit"] = mf["nearest_standard_fit"]
            else:
                entry["fit_type"] = ("clearance" if wf > wm else "interference"
                                     if wf < wm else "transition")
            fits.append(entry)

        out = {
            "n_solids": len(solids),
            "n_fits": len(fits),
            "fits": fits,
            "assumptions": assumptions,
            "grade": "measured",
        }
        return SkillResult(body=body, history=EntityHistoryMap(),
                           extras={"assembly_fit": out})
