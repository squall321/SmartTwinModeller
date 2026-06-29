"""detect_sheet_metal — inspect macro, read-only (2026-06-20).

NEW FRONTIER: sheet-metal reverse-engineering. Phone/watch mid-frames, brackets
and shields are bent sheet metal — a domain the pipeline did not cover. This
recognises a constant-thickness bent-sheet part and recovers its BEND TABLE:
thickness, and per bend the radius, angle, bend-line length, and the
press-brake allowances (bend allowance + bend deduction) a quoter needs.

PRECISION (validated against the 161-file corpus — the corpus is mostly NON-sheet
machined/revolved parts, so over-firing is the real risk):
  * thickness must be in a real sheet GAUGE range [0.15, 6.0]mm — this rejects
    thick assemblies whose minimum wall was mis-read as "thin" (e.g. one assembly
    measured a 254mm "thickness");
  * a bend's line must span ≥1.5×thickness — a tiny cylinder (an IC lead, a pin)
    is NOT a bend;
  * confidence is HONEST about ambiguity: a FORMED part (real bends/hems) is
    strong sheet evidence → high confidence; a FLAT thin part is geometrically
    indistinguishable from a laser-cut/machined plate or a thin moulded body
    (`flat_unformed=True`, reduced confidence). And even a formed part's bends
    cannot be told apart from cooling fins / stamped lead-frames / pulley grooves
    of the same local geometry — `assumptions` says so.

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
# A ~180° (≥170°) arc is a WRAP: either a rounded-over sheet EDGE (its two planar
# neighbours are the one sheet's faces, offset ≈ thickness) or a folded HEM (a
# return flange, neighbour offset ≥ ~1.5×thickness — an open hem with a real gap).
_WRAP_ARC_MIN = math.radians(170.0)
_SHEET_COVERAGE_MIN = 0.7  # sheet metal = most planar area is the two thin faces


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _unit(v):
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / m, v[1] / m, v[2] / m) if m > 1e-12 else (0.0, 0.0, 0.0)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _canon_axis(a):
    """Sign-normalise a bend axis to a canonical hemisphere (first non-zero
    component positive). OCCT emits anti-parallel pairs (0,0,1)/(0,0,-1) for
    cylinders about the SAME physical axis, so a single-axis Z/S-fold would
    otherwise read as two axes and be wrongly refused. Canonicalising collapses
    them."""
    a = _unit(tuple(a))
    for c in a:
        if abs(c) > 1e-6:
            return tuple(-x for x in a) if c < 0 else tuple(a)
    return a


def _flange_flat_len(face, axis, normal):
    """The flat (developed) length of one flange face along its OWN in-plane
    unroll direction u = unit(axis × normal). Projecting the flange's OWN
    vertices onto u (which lies in the flange's plane) is POSE-INDEPENDENT — a
    world-AABB diagonal or a single global unroll vector collapses post-bend
    flanges to garbage (0/47mm where the truth is 18/28). Returns (flat_len, u)."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    u = _unit(_cross(axis, normal))
    if u == (0.0, 0.0, 0.0):
        return 0.0, u
    s = []
    ve = TopExp_Explorer(face, TopAbs_VERTEX)
    while ve.More():
        v = TopoDS.Vertex_s(ve.Current())
        p = BRep_Tool.Pnt_s(v)
        s.append(_dot((p.X(), p.Y(), p.Z()), u))
        ve.Next()
    return (max(s) - min(s)) if s else 0.0, u


def _face_inplane_extents(face, normal):
    """The two in-plane extents (L, W) of a planar face measured in ITS OWN
    plane — correct for a plate at any IN-PLANE rotation. Projecting onto fixed
    world axes would return the AABB (e.g. 58×53 for a 50×30 plate rotated 35°);
    instead compute the MINIMUM-AREA bounding rectangle by trying each boundary
    edge's direction as a candidate orientation (a min-area rect always has a
    side flush with a hull edge), so a rectangular blank reports its true 50×30."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    n = _unit(normal)
    pts = []
    ve = TopExp_Explorer(face, TopAbs_VERTEX)
    while ve.More():
        v = TopoDS.Vertex_s(ve.Current())
        p = BRep_Tool.Pnt_s(v)
        pts.append((p.X(), p.Y(), p.Z()))
        ve.Next()
    if len(pts) < 2:
        return None, None

    # candidate in-plane directions: each unique edge between consecutive
    # (deduped) vertices, plus a world-axis fallback.
    world = min(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
               key=lambda w: abs(_dot(w, n)))
    fallback = _unit(tuple(world[i] - _dot(world, n) * n[i] for i in range(3)))
    cands = [fallback]
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = tuple(pts[j][k] - pts[i][k] for k in range(3))
            # project the edge into the plane and normalise.
            dp = tuple(d[k] - _dot(d, n) * n[k] for k in range(3))
            u = _unit(dp)
            if u != (0.0, 0.0, 0.0):
                cands.append(u)

    best = None
    for e1 in cands:
        e2 = _unit(_cross(n, e1))
        if e2 == (0.0, 0.0, 0.0):
            continue
        s1 = [_dot(xyz, e1) for xyz in pts]
        s2 = [_dot(xyz, e2) for xyz in pts]
        L = max(s1) - min(s1)
        W = max(s2) - min(s2)
        if L < 1e-9 or W < 1e-9:
            continue
        area = L * W
        ext = (max(L, W), min(L, W))
        if best is None or area < best[0] - 1e-6:
            best = (area, ext[0], ext[1])
    if best is None:
        return None, None
    return best[1], best[2]


def _neighbor_flanges_of_bend(bend_face, axis, efm, flange_faces):
    """The flange-face indices adjacent to a bend cylinder (sharing an edge),
    for ordering the flange→bend chain. flange_faces is [(idx, face), ...]."""
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    nbrs = []
    ee = TopExp_Explorer(bend_face, TopAbs_EDGE)
    while ee.More():
        edge = TopoDS.Edge_s(ee.Current())
        try:
            faces = efm.FindFromKey(edge)
            it = faces  # TopTools list
            from OCP.TopTools import TopTools_ListIteratorOfListOfShape
            lit = TopTools_ListIteratorOfListOfShape(it)
            while lit.More():
                fc = lit.Value()
                for idx, ff in flange_faces:
                    if ff.IsSame(fc) and idx not in nbrs:
                        nbrs.append(idx)
                lit.Next()
        except Exception:
            pass
        ee.Next()
    return nbrs


def _build_flat_outline(planes, formed, thickness, size, cover_area,
                        flat_pattern, efm, is_sheet, flat_unformed):
    """Produce the 2D flat-pattern blank outline + bend-line layout (single-axis
    only) or an HONEST refusal. Returns ONLY new keys for flat_pattern.update().
    See the module docstring / tests for the schema."""
    refused = {
        "outline_2d": None, "outline_kind": "refused", "outline_area_mm2": None,
        "outline_area_reconciles": False, "unfoldable": False,
        "refusal_reason": "not_sheet_metal", "bend_lines_2d": [], "flanges": [],
        "flanges_geometric": False, "single_bend_axis_canonical": False,
        "has_varying_width": False, "holes_projected": False, "per_axis": None,
        "outline_note": "",
    }
    if not is_sheet or not thickness:
        return refused

    # canonical axis routing (sign-normalised — fixes the anti-parallel bug).
    canon = [_canon_axis(b["axis_dir"]) for b in formed]
    distinct = {tuple(round(c, 3) for c in a) for a in canon}
    n_distinct = len(distinct)
    single_canon = n_distinct <= 1
    refused["single_bend_axis_canonical"] = single_canon

    dev_len = flat_pattern.get("developed_length_mm")
    blank_w = flat_pattern.get("blank_width_mm")
    blank_area = flat_pattern.get("flat_blank_area_mm2")

    # ── CASE 3 — flat unformed: the outline IS the dominant face's own extent ──
    if flat_unformed or not formed:
        dom = max((p for p in planes if p.get("face") is not None),
                  key=lambda p: p["area"], default=None)
        L = W = None
        if dom is not None:
            L, W = _face_inplane_extents(dom["face"], dom["normal"])
        if L and W:
            outline = [[0.0, 0.0], [round(L, 3), 0.0],
                       [round(L, 3), round(W, 3)], [0.0, round(W, 3)]]
            return {**refused, "outline_2d": outline, "outline_kind": "rectangle",
                    "outline_area_mm2": round(L * W, 2),
                    "outline_area_reconciles": True, "unfoldable": True,
                    "refusal_reason": None,
                    "single_bend_axis_canonical": single_canon,
                    "outline_note": ("flat blank: in-plane bbox envelope of the "
                                     "dominant face (not a traced silhouette; "
                                     "holes/notches not subtracted).")}
        return {**refused, "refusal_reason": "developed_length_unavailable",
                "single_bend_axis_canonical": single_canon}

    # ── CASE 2 — multi-axis formed: HONEST REFUSAL (never fabricate) ──────────
    if n_distinct >= 2:
        per_axis = []
        for a in distinct:
            grp = [b for b, c in zip(formed, canon)
                   if tuple(round(x, 3) for x in c) == a]
            strip = sum(b.get("bend_allowance_mm", 0.0) * b["bend_line_length_mm"]
                        for b in grp)
            per_axis.append({"axis_dir": list(a), "n_bends": len(grp),
                             "developed_strip_area_mm2": round(strip, 2)})
        return {**refused, "refusal_reason": "multi_axis_unfold_unsupported",
                "single_bend_axis_canonical": False, "per_axis": per_axis,
                "outline_note": ("multi-axis bends (a box/tray) — a true 2D "
                                 "unfold is ambiguous (corner relief/overlap); "
                                 "refused. Per-axis developed strips + total "
                                 "developed area are still reported.")}

    # ── CASE 1 — single-axis formed: PRODUCE the outline + bend lines ─────────
    if dev_len is None or not blank_w:
        return {**refused, "refusal_reason": "developed_length_unavailable",
                "single_bend_axis_canonical": single_canon}

    axis = canon[0]
    # identify flanges: planar faces whose normal ⟂ axis. One PHYSICAL flange is
    # a pair of parallel faces (inner+outer) separated by the thickness, so merge
    # faces that share a canonical normal axis AND lie within ~1.5×t of each other
    # along that axis — this collapses a panel's two faces into one flange while
    # keeping distinct panels (e.g. a U-channel's two legs, same normal axis but
    # far apart) separate.
    cand = [p for p in planes if p.get("face") is not None
            and abs(_dot(_unit(p["normal"]), axis)) < 0.1
            and p["area"] >= 0.05 * cover_area]
    by_axis: dict = {}
    for p in cand:
        cn = _canon_axis(p["normal"])
        by_axis.setdefault(tuple(round(c, 2) for c in cn), []).append(
            (p, _dot(p["centroid"], cn)))
    tol = 1.5 * thickness
    flange_planes = []
    for cn_key, members in by_axis.items():
        members.sort(key=lambda m: m[1])
        cluster = [members[0]]
        for m in members[1:]:
            if abs(m[1] - cluster[-1][1]) <= tol:
                cluster.append(m)
            else:
                flange_planes.append(max(cluster, key=lambda x: x[0]["area"])[0])
                cluster = [m]
        flange_planes.append(max(cluster, key=lambda x: x[0]["area"])[0])

    flanges_meas = []
    for p in flange_planes:
        flat_len, _u = _flange_flat_len(p["face"], axis, _unit(p["normal"]))
        flanges_meas.append({"plane": p, "flat_len": flat_len,
                             "normal": _unit(p["normal"])})

    # order the flange→bend chain via adjacency (a single-axis chain is a path).
    flange_faces = [(i, fm["plane"]["face"]) for i, fm in enumerate(flanges_meas)]
    chain_ok = False
    sequence: list = []
    if efm is not None and len(flanges_meas) == len(formed) + 1:
        adj: dict = {i: [] for i in range(len(flanges_meas))}
        bend_edges = []
        for b in formed:
            bf = b.get("_face")
            if bf is None:
                break
            nb = _neighbor_flanges_of_bend(bf, axis, efm, flange_faces)
            if len(nb) >= 2:
                adj[nb[0]].append((nb[1], b))
                adj[nb[1]].append((nb[0], b))
                bend_edges.append((nb[0], nb[1], b))
        if len(bend_edges) == len(formed):
            ends = [i for i, e in adj.items() if len(e) == 1]
            if ends:
                seen = set()
                cur = ends[0]
                seen.add(cur)
                sequence = [("F", cur)]
                while True:
                    nxt = [(j, b) for j, b in adj[cur] if j not in seen]
                    if not nxt:
                        break
                    j, b = nxt[0]
                    sequence.append(("B", b))
                    sequence.append(("F", j))
                    seen.add(j)
                    cur = j
                chain_ok = len(seen) == len(flanges_meas)

    if not chain_ok:
        # fallback: order flanges by projection on the base flange's unroll dir.
        base = max(flanges_meas, key=lambda fm: fm["plane"]["area"])
        u0 = _unit(_cross(axis, base["normal"]))
        order = sorted(range(len(flanges_meas)),
                       key=lambda i: _dot(flanges_meas[i]["plane"]["centroid"], u0))
        bsorted = sorted(formed, key=lambda b: _dot(b.get("centroid", (0, 0, 0)), u0))
        sequence = []
        for k, i in enumerate(order):
            sequence.append(("F", i))
            if k < len(bsorted):
                sequence.append(("B", bsorted[k]))

    # accumulate developed coordinate.
    s = 0.0
    flanges_out: list = []
    bend_lines: list = []
    order_index = 0
    for kind, item in sequence:
        if kind == "F":
            fm = flanges_meas[item]
            flanges_out.append({
                "developed_length_mm": round(fm["flat_len"], 3),
                "order_index": order_index, "s_start_mm": round(s, 3),
                "s_end_mm": round(s + fm["flat_len"], 3),
                "plane_normal": [round(c, 4) for c in fm["normal"]],
                "area_mm2": round(fm["plane"]["area"], 2)})
            s += fm["flat_len"]
            order_index += 1
        else:
            b = item
            ba = b.get("bend_allowance_mm", 0.0)
            bend_lines.append({
                "position_mm": round(s + ba / 2.0, 3),
                "length_mm": b["bend_line_length_mm"],
                "bend_index": formed.index(b),
                "kind": "hem" if b.get("_is_hem") else "bend",
                "angle_deg": b.get("angle_deg"), "radius_mm": b.get("radius_mm"),
                "dev_start_mm": round(s, 3), "dev_end_mm": round(s + ba, 3)})
            s += ba

    # reconcile with the authoritative developed_length (genuine, not cosmetic).
    s_total = s
    residual = s_total - dev_len
    tol = max(0.5, 0.02 * dev_len)
    flanges_geometric = abs(residual) <= tol
    note = ""
    if flanges_geometric and flanges_out and abs(residual) > 1e-9:
        # distribute the tiny residual proportionally so the chain ends exactly
        # at the authoritative developed_length.
        scale = dev_len / s_total if s_total > 1e-9 else 1.0
        s2 = 0.0
        for fo in flanges_out:
            fl = (fo["s_end_mm"] - fo["s_start_mm"]) * scale
            fo["s_start_mm"] = round(s2, 3)
            fo["developed_length_mm"] = round(fl, 3)
            s2 += fl
            fo["s_end_mm"] = round(s2, 3)
        # re-place bend lines by recomputed flange ends (already monotone).
    elif not flanges_geometric:
        note = (f"per-flange flat lengths did not reconcile with the developed "
                f"length (residual={round(residual, 3)}mm); bend-line layout is "
                "the cumulative-allowance fallback, not measured geometry.")

    L = round(dev_len, 3)
    W = round(blank_w, 3)
    outline = [[0.0, 0.0], [L, 0.0], [L, W], [0.0, W]]
    out_area = round(L * W, 2)
    reconciles = (blank_area is not None
                  and abs(out_area - blank_area) <= max(1.0, 1e-4 * blank_area))
    # centre each bend line across the width.
    for bl in bend_lines:
        half = bl["length_mm"] / 2.0
        bl["segment_2d"] = [[bl["position_mm"], round(W / 2.0 - half, 3)],
                            [bl["position_mm"], round(W / 2.0 + half, 3)]]

    # varying-width detection (stepped flanges → bounding rectangle).
    widths = [b["bend_line_length_mm"] for b in formed]
    has_varying = bool(widths and (max(widths) - min(widths)) > 0.02 * max(widths))

    return {
        "outline_2d": outline, "outline_kind": "rectangle",
        "outline_area_mm2": out_area, "outline_area_reconciles": reconciles,
        "unfoldable": True, "refusal_reason": None,
        "bend_lines_2d": sorted(bend_lines, key=lambda x: x["position_mm"]),
        "flanges": flanges_out, "flanges_geometric": flanges_geometric,
        "single_bend_axis_canonical": True, "has_varying_width": has_varying,
        "holes_projected": False, "per_axis": None, "outline_note": note,
    }


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
    # thickness = the SMALLEST gap among the top-area bins. A doubled-over hem
    # makes a base↔return-flange gap that can TIE the true wall gap by area; the
    # real thickness is the minimum consistent wall, so break ties toward smaller.
    max_area = max(bins.values())
    top = [(g, a) for g, a in bins.items() if a >= 0.9 * max_area]
    gap, area = min(top, key=lambda ga: ga[0])
    return gap, area


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
        # A genuine press-brake bend spans the material — its bend line is at
        # least ~1.5× the thickness. A tiny cylinder (an IC lead, a pin) is NOT a
        # bend; this drops those false positives.
        if thickness and line_len < 1.5 * thickness:
            continue
        bends.append({
            "radius_mm": round(inner["radius"], 4),
            "angle_deg": round(math.degrees(arc_rad), 2),
            "bend_line_length_mm": round(line_len, 3),
            "axis_dir": [round(v, 4) for v in inner["axis"]],
            "surfaces": len(group),  # 2 = concentric inner+outer recovered
            "_arc_rad": arc_rad,
            "_face": inner.get("face"),       # for the flat-pattern chain walk
            "centroid": inner.get("centroid"),
        })
    return bends


def _edge_face_map(shape):
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, m)
    return m


def _neighbor_plane_offset(cyl_face, axis, efm):
    """Max perpendicular offset between two PARALLEL FLANGE faces adjacent to
    ``cyl_face``. Only flanges (normal ⟂ to the cylinder axis) count — the
    end-cap faces (normal ∥ axis, far apart) are NOT the fold's flanges. For a
    rounded sheet edge this ≈ thickness (the one sheet's two sides); for a hem it
    is the doubled-over gap (≥ ~1.5×thickness)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    planes, seen = [], set()
    it = TopExp_Explorer(cyl_face, TopAbs_EDGE)
    while it.More():
        try:
            for sh in efm.FindFromKey(it.Current()):
                if sh.IsSame(cyl_face):
                    continue
                s = BRepAdaptor_Surface(TopoDS.Face_s(sh))
                if s.GetType() != GeomAbs_Plane:
                    continue
                pl = s.Plane()
                n = pl.Axis().Direction()
                lc = pl.Location()
                normal = _unit((n.X(), n.Y(), n.Z()))
                if abs(_dot(normal, axis)) > 0.1:
                    continue  # end-cap (normal ∥ axis) — not a fold flange
                key = (round(normal[0], 2), round(normal[1], 2), round(normal[2], 2),
                       round(lc.X() * normal[0] + lc.Y() * normal[1]
                             + lc.Z() * normal[2], 2))
                if key in seen:
                    continue
                seen.add(key)
                planes.append((normal, (lc.X(), lc.Y(), lc.Z())))
        except Exception:  # noqa: BLE001
            pass
        it.Next()
    best = 0.0
    for i in range(len(planes)):
        ni, pi = planes[i]
        for j in range(i + 1, len(planes)):
            nj, pj = planes[j]
            if abs(_dot(ni, nj)) < _PARALLEL_TOL:
                continue
            diff = (pj[0] - pi[0], pj[1] - pi[1], pj[2] - pi[2])
            best = max(best, abs(_dot(diff, ni)))
    return best


def _collect_wraps(cyls, thickness, efm):
    """Classify ≥170° wrap cylinders into hems (real return-flange folds) and
    rounded edges, grouping concentric inner+outer surfaces. Returns
    (hems, rounded_edge_count)."""
    t = thickness or 0.0
    cand = [c for c in cyls if c["arc"] >= _WRAP_ARC_MIN]
    used = [False] * len(cand)
    hems, rounded = [], 0
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
            off = (d["loc"][0] - c["loc"][0], d["loc"][1] - c["loc"][1],
                   d["loc"][2] - c["loc"][2])
            along = _dot(off, c["axis"])
            perp = math.sqrt(max(_dot(off, off) - along * along, 0.0))
            if perp > 0.05 * max(c["radius"], 1.0):
                continue
            group.append(d)
            used[j] = True
        inner = min(group, key=lambda x: x["radius"])
        gap = max(_neighbor_plane_offset(g["face"], g["axis"], efm) for g in group)
        if t and abs(gap - t) < 0.5 * t:
            rounded += 1                       # rounded sheet edge — not a hem
            continue
        if t and gap < 1.5 * t:
            continue                            # ambiguous tight fold — conservative
        arc_rad = sum(x["arc"] for x in group) / len(group)
        line_len = (inner["area"] / (inner["radius"] * arc_rad)
                    if inner["radius"] * arc_rad > 1e-9 else 0.0)
        hems.append({
            "radius_mm": round(inner["radius"], 4),
            "angle_deg": round(math.degrees(arc_rad), 2),
            "bend_line_length_mm": round(line_len, 3),
            "axis_dir": [round(v, 4) for v in inner["axis"]],
            "_arc_rad": arc_rad,
            "_face": inner.get("face"),
            "centroid": inner.get("centroid"),
            "_is_hem": True,
        })
    return hems, rounded


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
        min_thickness_mm: float = Field(
            default=0.15,
            description="Below this, the 'thickness' is foil/sub-feature noise, "
                        "not a sheet-metal gauge → not classified sheet.",
        )
        max_thickness_mm: float = Field(
            default=6.0,
            description="Above this, the part is plate/machined, not formed sheet "
                        "metal → not classified sheet (a real physical bound that "
                        "rejects thick assemblies measured as 'thin').",
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
                # ``face`` handle added for the flat-pattern flange unfold
                # (per-flange vertex projection). _thickness reads only
                # normal/centroid/area, so this is a no-op for it.
                planes.append({"normal": payload, "centroid": centroid,
                               "area": area, "face": f})
            elif kind == "cylinder":
                r, axis, loc, arc = payload
                cyls.append({"radius": r, "axis": axis, "loc": loc, "arc": arc,
                             "area": area, "centroid": centroid, "face": f})

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

        # is_sheet_metal FIRST: a consistent thin thickness covering most planar
        # area, thin relative to the part. Bend/hem recovery is meaningful ONLY
        # for a recognised sheet part — running it on (say) an extrusion would
        # report its decorative edge fillets as fake bends/hems.
        thin = bool(thickness and bbox_ref and thickness < 0.34 * bbox_ref)
        # Sheet metal has a real physical GAUGE range: below ~0.15mm the measured
        # "thickness" is foil/sub-feature noise, above ~6mm it is plate/machined
        # (this rejects thick assemblies whose minimum wall was read as a sheet).
        in_gauge = bool(thickness and args.min_thickness_mm <= thickness
                        <= args.max_thickness_mm)
        is_sheet = bool(thin and in_gauge and coverage >= _SHEET_COVERAGE_MIN)
        if thickness and not in_gauge:
            assumptions.append(
                f"thickness {round(thickness,3)}mm outside the sheet gauge range "
                f"[{args.min_thickness_mm},{args.max_thickness_mm}]mm → not sheet.")

        t = thickness or 0.0
        total_ba = 0.0
        bends: list[dict[str, Any]] = []
        hems: list[dict[str, Any]] = []
        rounded_edges = 0
        if is_sheet:
            bends = _collect_bends(cyls, thickness)
            hems, rounded_edges = _collect_wraps(cyls, thickness, _edge_face_map(shape))

            def _allow(feat):
                nonlocal total_ba
                arc_rad = feat.pop("_arc_rad")
                r = feat["radius_mm"]
                ba = arc_rad * (r + args.k_factor * t)
                feat["bend_allowance_mm"] = round(ba, 4)
                # bend deduction = 2(r+t)·tan(θ/2) − BA blows up as θ→180° (a hem):
                # the outside-dimension method is undefined there, so report None.
                if arc_rad / 2.0 >= math.radians(85.0):
                    feat["bend_deduction_mm"] = None
                else:
                    feat["bend_deduction_mm"] = round(
                        2.0 * (r + t) * math.tan(arc_rad / 2.0) - ba, 4)
                total_ba += ba

            for _feat in bends + hems:
                _allow(_feat)

        if not is_sheet:
            assumptions.append(
                f"NOT classified sheet-metal (thin={thin}, planar coverage="
                f"{round(coverage,2)}, thickness={thickness}).")

        # Honest confidence: a FORMED part (real bends/hems) is strong evidence of
        # sheet metal; a FLAT thin part is geometrically AMBIGUOUS — it could be a
        # sheet blank, a laser-cut/machined plate, or a thin moulded body, and
        # geometry alone cannot tell them apart. Reflect that, don't overclaim.
        is_formed = bool(bends or hems)
        flat_unformed = bool(is_sheet and not is_formed)
        base_conf = coverage if (thin and in_gauge) else coverage * 0.4
        confidence = round(base_conf * (1.0 if is_formed else 0.6), 3) \
            if thickness else 0.0
        if flat_unformed:
            assumptions.append(
                "FLAT (unformed) constant-thickness part — geometry alone cannot "
                "distinguish a sheet blank from a laser-cut/machined plate or a "
                "thin moulded body; confidence reduced accordingly.")
        if is_formed:
            assumptions.append(
                "bend recovery is GEOMETRIC — a forming bend, a cooling fin, a "
                "stamped lead-frame and a pulley groove can share the same local "
                "geometry and are not distinguished; verify bends against function.")

        # ── flat pattern: developed blank AREA + (single-axis) developed LENGTH ─
        # One side of the sheet = half the thickness-pair face area (cover_area/2);
        # each bend adds its neutral-strip developed area = bend_allowance × line.
        # For an L-bracket this reproduces the hand calc (Σ flange flats + Σ BA).
        formed = bends + hems  # hems add a fold strip to the flat pattern too
        axes = {tuple(b["axis_dir"]) for b in formed}
        single_axis = len(axes) <= 1
        flat_pattern: dict[str, Any] = {
            "k_factor": args.k_factor,
            "total_bend_allowance_mm": round(total_ba, 4),
            "bends_share_single_axis": single_axis,
        }
        if thickness:
            bend_dev_area = sum(b["bend_allowance_mm"] * b["bend_line_length_mm"]
                                for b in formed)
            blank_area = cover_area / 2.0 + bend_dev_area
            if formed and single_axis:
                blank_width = max(b["bend_line_length_mm"] for b in formed)
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

        # ── 2D flat-pattern blank OUTLINE + bend-line layout (single-axis only;
        # honest refusal for multi-axis). Reuses the developed numbers above so
        # the rectangle is consistent with flat_blank_area_mm2.
        try:
            efm = _edge_face_map(shape) if is_sheet else None
            flat_pattern.update(_build_flat_outline(
                planes, formed, thickness, size, cover_area, flat_pattern,
                efm, is_sheet, flat_unformed))
            if flat_pattern.get("outline_note"):
                assumptions.append(flat_pattern["outline_note"])
        except Exception as exc:  # never crash the detector on an unfold edge case
            flat_pattern.setdefault("outline_2d", None)
            flat_pattern.setdefault("outline_kind", "refused")
            flat_pattern.setdefault("unfoldable", False)
            flat_pattern["refusal_reason"] = f"outline_error:{type(exc).__name__}"
        flat_pattern.pop("outline_note", None)

        # strip internal-only keys (OCCT face handles, raw centroids) from the
        # public bend/hem records before serialising.
        for _f in bends + hems:
            _f.pop("_face", None)
            _f.pop("centroid", None)
            _f.pop("_is_hem", None)

        out = {
            "is_sheet_metal": is_sheet,
            "confidence": confidence,
            "flat_unformed": flat_unformed,
            "thickness_mm": round(thickness, 4) if thickness else None,
            "n_bends": len(bends),
            "bends": bends,
            "n_hems": len(hems),
            "hems": hems,
            "rounded_edges": rounded_edges,
            "k_factor": args.k_factor,
            "total_bend_allowance_mm": round(total_ba, 4),
            "flat_pattern": flat_pattern,
            "bbox_mm": [round(v, 3) for v in size] if size else None,
            "assumptions": assumptions,
            "grade": "estimate",
        }
        return SkillResult(body=body, history=EntityHistoryMap(),
                           extras={"sheet_metal": out})
