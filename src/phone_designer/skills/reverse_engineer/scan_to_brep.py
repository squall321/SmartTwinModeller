"""scan_to_brep — macro. Scan-to-CAD v1 spike (Phase 3-1, stage 3).

mesh → segmentation (mesh_segment_regions helpers) → analytic fits
(fit_region_surfaces helpers) → analytic B-rep faces → sew → solid lift.

v1 SCOPE — machined / prismatic only
------------------------------------
* Faces are BUILT for fitted **planar** regions (region boundary loops walked
  from the mesh, projected onto the fitted plane; loops whose vertices all lie
  on a built cylinder are STITCHED to the analytic circle so cap↔wall edges
  sew exactly) and **cylindrical** regions (iso-parameter patch: full 360°
  when the angular coverage wraps, else a rectangular u×v patch — an honest
  approximation noted per face).
* Regions fitted as sphere / cone / torus are analytic but NOT buildable in
  v1 — recorded per region, shell left open (honest partial).
* ``fm.organic_unsupported``: when more than ``max_freeform_area_fraction``
  of the mesh area is ``freeform_unfit``, the skill REFUSES with the measured
  fraction — organic/freeform reconstruction is out of scope (project ruling:
  analytic surfaces only).
* ``fm.not_a_mesh`` / ``fm.too_many_triangles`` propagate from segmentation;
  smooth B-rep input is auto-tessellated (same documented decision as
  mesh_segment_regions).
* ``fm.no_buildable_regions``: every fitted region is a v1-unbuildable kind
  (e.g. a pure sphere) — structured refusal instead of an empty shape.

HONESTY GATES
-------------
``is_solid`` is True only when sewing yields exactly one closed shell that
lifts to a ``TopAbs_SOLID`` **and** the measured volume exceeds 1e-6 mm³
(open-shell pseudo-mass trap); otherwise the PARTIAL shell is returned with
``is_solid=False``, ``volume_mm3=None``, per-face fit grades, and fm-style
notes — never a fabricated solid. The artifact carries its own grade label:
``"reconstructed_solid"`` or ``"partial_shell"``.

extras["scan_to_brep"] = {
  "grade": "reconstructed_solid" | "partial_shell",
  "is_solid": bool, "volume_mm3": float|None, "free_edge_count": int,
  "n_regions", "built_face_count",
  "freeform_area_fraction": float,
  "regions": [{"id","kind","rms_mm","fit_grade","built","note",
               "face_area_mm2","region_area_mm2"}],
  "notes": [str, ...],
}
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.reverse_engineer.fit_region_surfaces import (
    fit_regions,
    freeform_area_fraction,
)
from phone_designer.skills.reverse_engineer.mesh_segment_regions import (
    segment_body,
)


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #

def _perp_frame(n):
    """Right-handed in-plane frame (x̂, ŷ) for unit normal n (ŷ = n × x̂)."""
    import numpy as np

    e = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = e - np.dot(e, n) * n
    x = x / np.linalg.norm(x)
    y = np.cross(n, x)
    return x, y


def _region_boundary_loops(tris, tri_idx) -> list[list[int]]:
    """Directed boundary loops of a triangle region (mesh-winding order).

    Boundary = undirected edge used by exactly ONE region triangle. Directed
    edges inherit the triangle winding, so with outward-consistent normals the
    outer loop comes out CCW around the region normal and holes CW.
    Raises ValueError on non-manifold / broken boundaries (caught per region).
    """
    count: dict[tuple[int, int], int] = {}
    for t in tri_idx:
        a, b, c = int(tris[t, 0]), int(tris[t, 1]), int(tris[t, 2])
        for i, j in ((a, b), (b, c), (c, a)):
            key = (i, j) if i < j else (j, i)
            count[key] = count.get(key, 0) + 1

    out_map: dict[int, int] = {}
    for t in tri_idx:
        a, b, c = int(tris[t, 0]), int(tris[t, 1]), int(tris[t, 2])
        for i, j in ((a, b), (b, c), (c, a)):
            key = (i, j) if i < j else (j, i)
            if count[key] == 1:
                if i in out_map:
                    raise ValueError(
                        f"non-manifold boundary at vertex {i} "
                        "(two outgoing boundary edges)")
                out_map[i] = j

    loops: list[list[int]] = []
    visited: set[int] = set()
    for start in sorted(out_map):
        if start in visited:
            continue
        loop = [start]
        visited.add(start)
        cur = out_map[start]
        steps = 0
        while cur != start:
            if cur in visited or cur not in out_map:
                raise ValueError(f"broken boundary chain at vertex {cur}")
            loop.append(cur)
            visited.add(cur)
            cur = out_map[cur]
            steps += 1
            if steps > len(out_map) + 1:
                raise ValueError("boundary walk did not close")
        if len(loop) >= 3:
            loops.append(loop)
    if not loops:
        raise ValueError("region has no closed boundary loop")
    return loops


def _loop_signed_area(pts_3d, origin, x, y) -> float:
    """Shoelace area of the loop projected on the (x̂, ŷ) plane frame."""
    import numpy as np

    d = pts_3d - origin
    u = d @ x
    v = d @ y
    return 0.5 * float(np.sum(u * np.roll(v, -1) - np.roll(u, -1) * v))


def _face_area(face) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    return float(g.Mass())


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return float(g.Mass())


def _count_solids(shape) -> int:
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    n = 0
    it = TopExp_Explorer(shape, TopAbs_SOLID)
    while it.More():
        n += 1
        it.Next()
    return n


def _circle_wire(center, normal, radius):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    circ = gp_Circ(
        gp_Ax2(gp_Pnt(*[float(c) for c in center]),
               gp_Dir(*[float(c) for c in normal])),
        float(radius))
    edge = BRepBuilderAPI_MakeEdge(circ).Edge()
    return BRepBuilderAPI_MakeWire(edge).Wire()


def _polygon_wire(pts_3d):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    poly = BRepBuilderAPI_MakePolygon()
    for p in pts_3d:
        poly.Add(gp_Pnt(float(p[0]), float(p[1]), float(p[2])))
    poly.Close()
    if not poly.IsDone():
        raise ValueError("polygon wire construction failed")
    return poly.Wire()


def _build_planar_face(verts, tris, reg, fit, built_cylinders,
                       stitch_tol_mm: float):
    """Fitted planar region → TopoDS_Face (outer loop + holes).

    Loops whose vertices all lie on a BUILT cylinder (|dist_to_axis − r| ≤
    stitch_tol) and whose axis is within 5° of the plane normal are replaced
    by the analytic circle (plane ∩ cylinder) so the cap and the cylindrical
    wall share edge geometry and sew cleanly.
    Returns (face, note_or_None).
    """
    import numpy as np
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from OCP.ShapeFix import ShapeFix_Face

    o = np.asarray(fit["params"]["origin"], dtype=float)
    n = np.asarray(fit["params"]["normal"], dtype=float)
    n = n / np.linalg.norm(n)
    x, y = _perp_frame(n)

    loops = _region_boundary_loops(tris, reg["_tri_indices"])
    notes: list[str] = []

    entries = []  # (signed_area, wire)
    for loop in loops:
        pts = verts[np.asarray(loop, dtype=np.int64)]
        s_area = _loop_signed_area(pts, o, x, y)

        wire = None
        for cyl in built_cylinders:
            co = np.asarray(cyl["params"]["origin"], dtype=float)
            ca = np.asarray(cyl["params"]["axis"], dtype=float)
            ca = ca / np.linalg.norm(ca)
            r = float(cyl["params"]["radius_mm"])
            if abs(float(np.dot(ca, n))) < math.cos(math.radians(5.0)):
                continue  # cylinder not ⟂ to this cap — keep the polygon
            d = pts - co
            t = d @ ca
            dist = np.linalg.norm(d - np.outer(t, ca), axis=1)
            if np.max(np.abs(dist - r)) <= stitch_tol_mm:
                center = co + float(np.mean(t)) * ca
                # keep the circle exactly in the fitted plane
                center = center - float(np.dot(center - o, n)) * n
                axis_dir = n if s_area > 0 else -n
                wire = _circle_wire(center, axis_dir, r)
                notes.append(
                    f"loop stitched to cylinder region {cyl['id']} "
                    f"(r={r:.4f})")
                break
        if wire is None:
            # project the polygon onto the fitted plane
            proj = pts - np.outer((pts - o) @ n, n)
            wire = _polygon_wire(proj)
        entries.append((s_area, wire))

    outers = [e for e in entries if e[0] > 0]
    holes = [e for e in entries if e[0] <= 0]
    if len(outers) != 1:
        raise ValueError(
            f"expected exactly 1 outer boundary loop, got {len(outers)} "
            f"(of {len(entries)})")

    pln = gp_Pln(gp_Pnt(*[float(c) for c in o]),
                 gp_Dir(*[float(c) for c in n]))
    maker = BRepBuilderAPI_MakeFace(pln, outers[0][1])
    for _a, hw in holes:
        maker.Add(hw)
    if not maker.IsDone():
        raise ValueError("planar face construction failed")
    face = maker.Face()
    fixer = ShapeFix_Face(face)
    fixer.FixOrientation()
    face = fixer.Face()
    return face, ("; ".join(notes) if notes else None)


def _build_cylindrical_face(verts, tris, normals, reg, fit):
    """Fitted cylindrical region → iso-parameter TopoDS_Face.

    Full 360° when the angular point coverage wraps (largest gap < 30°),
    else a rectangular [θ-span]×[v-span] patch (honest approximation when the
    true boundary is not iso-parametric — noted). Inward-facing regions
    (hole walls) get the face reversed so sewing sees consistent material
    orientation. Returns (face, note_or_None).
    """
    import numpy as np
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Ax3, gp_Cylinder, gp_Dir, gp_Pnt
    from OCP.TopoDS import TopoDS

    o = np.asarray(fit["params"]["origin"], dtype=float)
    a = np.asarray(fit["params"]["axis"], dtype=float)
    a = a / np.linalg.norm(a)
    r = float(fit["params"]["radius_mm"])
    x, y = _perp_frame(a)

    idx = reg["_tri_indices"]
    v_idx = np.unique(tris[idx].ravel())
    pts = verts[v_idx]
    d = pts - o
    t = d @ a
    vmin, vmax = float(np.min(t)), float(np.max(t))
    if vmax - vmin < 1e-9:
        raise ValueError("cylindrical region has zero axial extent")

    q = d - np.outer(t, a)
    theta = np.arctan2(q @ y, q @ x)
    ts = np.sort(theta)
    gaps = np.diff(ts)
    wrap_gap = (2.0 * math.pi) - (ts[-1] - ts[0])
    max_gap = max(float(np.max(gaps)) if len(gaps) else 2.0 * math.pi,
                  float(wrap_gap))
    note = None
    if max_gap < math.radians(30.0):
        u0, u1 = 0.0, 2.0 * math.pi
        x_start = x
    else:
        # partial patch: start u after the largest gap
        if len(gaps) and float(np.max(gaps)) >= wrap_gap:
            k = int(np.argmax(gaps))
            theta_start = float(ts[k + 1])
            span = 2.0 * math.pi - max_gap
        else:
            theta_start = float(ts[0])
            span = float(ts[-1] - ts[0])
        span = max(span, 1e-6)
        c, s = math.cos(theta_start), math.sin(theta_start)
        x_start = c * x + s * y
        u0, u1 = 0.0, span
        note = ("partial cylindrical patch approximated by iso-parameter "
                f"rectangle (θ-span {math.degrees(span):.1f}°) — exact only "
                "when the true boundary is iso-parametric")

    base = o + vmin * a
    ax3 = gp_Ax3(gp_Pnt(*[float(c) for c in base]),
                 gp_Dir(*[float(c) for c in a]),
                 gp_Dir(*[float(c) for c in x_start]))
    cyl = gp_Cylinder(ax3, r)
    maker = BRepBuilderAPI_MakeFace(cyl, u0, u1, 0.0, vmax - vmin)
    if not maker.IsDone():
        raise ValueError("cylindrical face construction failed")
    face = maker.Face()

    # hole walls face INWARD: align face orientation with the mesh normals
    tri_pts = verts[tris[idx]]
    centroids = np.mean(tri_pts, axis=1)
    dc = centroids - o
    tc = dc @ a
    radial = dc - np.outer(tc, a)
    rn = np.linalg.norm(radial, axis=1)
    ok = rn > 1e-9
    if np.any(ok):
        mean_dot = float(np.mean(
            np.sum(radial[ok] / rn[ok, None] * normals[idx][ok], axis=1)))
        if mean_dot < 0:
            face = TopoDS.Face_s(face.Reversed())
            note = ((note + "; ") if note else "") + \
                "inward-facing (hole wall) — face reversed"
    return face, note


# --------------------------------------------------------------------------- #
# skill
# --------------------------------------------------------------------------- #

_BUILDABLE = {"plane", "cylinder"}


@skill(
    name="scan_to_brep",
    category="reverse_engineer",
    level="macro",
    summary="Scan-to-CAD v1 macro: segment a triangle mesh into "
            "normal-continuous regions, fit analytic surfaces per region, "
            "rebuild PLANAR faces (boundary loops projected onto the fitted "
            "plane; cap loops on a built cylinder stitched to the analytic "
            "circle) and CYLINDRICAL faces (full/partial iso patches), sew, "
            "and lift to a solid when exactly one closed shell results. "
            "Machined/prismatic only — organic meshes refuse with the "
            "measured freeform fraction; open results return an honest "
            "partial shell (is_solid=False), never a fabricated solid.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["reconstructed_brep"],
    preserves=[],
    manufacturing={},
    failure_modes=[
        "fm.not_a_mesh",
        "fm.too_many_triangles",
        "fm.organic_unsupported",
        "fm.no_buildable_regions",
    ],
    cost_hint=0.8,
    expansion=["mesh_segment_regions", "fit_region_surfaces"],
    post_conditions=[PostCondition(kind="body_present")],
)
class ScanToBrep(SkillBase):
    class Args(BaseModel):
        angle_threshold_deg: float = Field(default=15.0, gt=0.0, le=90.0)
        weld_tolerance_mm: float = Field(default=1e-6, gt=0)
        linear_deflection_mm: float = Field(default=0.1, gt=0)
        angular_deflection_deg: float = Field(default=5.0, gt=0)
        max_triangles: int = Field(default=200000, ge=1)
        rms_tol_mm: float = Field(
            default=0.05, gt=0,
            description="Per-region analytic fit acceptance (RMS over region "
                        "vertices).")
        max_freeform_area_fraction: float = Field(
            default=0.3, ge=0.0, le=1.0,
            description="Refuse (fm.organic_unsupported) when the "
                        "freeform_unfit share of total mesh area exceeds "
                        "this.")
        sewing_tolerance_mm: float = Field(
            default=1e-3, gt=0,
            description="BRepBuilderAPI_Sewing tolerance for stitching the "
                        "rebuilt analytic faces.")
        stitch_tol_mm: float = Field(
            default=0.1, gt=0,
            description="A planar boundary loop whose vertices are all "
                        "within this distance of a built cylinder is "
                        "replaced by the analytic circle.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeSolid,
            BRepBuilderAPI_Sewing,
        )
        from OCP.BRepCheck import BRepCheck_Shell, BRepCheck_Status
        from OCP.TopAbs import TopAbs_SHELL
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        # ---- stage 1+2: segment + fit (shared helpers, one tessellation) ----
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

        # ---- organic gate (measured, refusal carries the number) ------------
        frac = freeform_area_fraction(fits)
        if frac > args.max_freeform_area_fraction:
            unfit_mm2 = sum(f["area_mm2"] for f in fits
                            if f["kind"] == "freeform_unfit")
            raise ValueError(
                f"fm.organic_unsupported: {frac:.1%} of the mesh area "
                f"({unfit_mm2:.1f} mm² across "
                f"{sum(1 for f in fits if f['kind'] == 'freeform_unfit')} "
                f"region(s)) has no analytic surface fit within rms_tol_mm="
                f"{args.rms_tol_mm} (limit "
                f"{args.max_freeform_area_fraction:.0%}) — scan_to_brep v1 "
                "reconstructs machined/prismatic parts only; keep the "
                "faceted shell (mesh_import / mesh_to_brep) for organic "
                "geometry.")

        kinds = {f["kind"] for f in fits}
        if not (kinds & _BUILDABLE):
            raise ValueError(
                "fm.no_buildable_regions: no fitted region is a plane or "
                f"cylinder (fitted kinds: {sorted(kinds)}) — scan_to_brep v1 "
                "builds planar+cylindrical faces only; the analytic fit "
                "params are available via fit_region_surfaces.")

        # ---- stage 3: build faces -------------------------------------------
        notes: list[str] = []
        region_records: list[dict] = []
        built_faces: list = []

        # cylinders first (planar caps stitch against them)
        built_cylinders: list[dict] = []
        cyl_faces: dict[int, tuple] = {}
        for reg, fit in zip(regions, fits):
            if fit["kind"] != "cylinder":
                continue
            try:
                face, note = _build_cylindrical_face(
                    verts, tris, normals, reg, fit)
                cyl_faces[fit["id"]] = (face, note)
                built_cylinders.append(fit)
            except Exception as exc:
                cyl_faces[fit["id"]] = (
                    None,
                    f"build failed: {type(exc).__name__}: {exc}")

        for reg, fit in zip(regions, fits):
            rec = {
                "id": fit["id"],
                "kind": fit["kind"],
                "rms_mm": fit["rms_mm"],
                "fit_grade": fit["grade"],
                "region_area_mm2": fit["area_mm2"],
                "built": False,
                "face_area_mm2": None,
                "note": None,
            }
            face = None
            if fit["kind"] == "plane":
                try:
                    face, note = _build_planar_face(
                        verts, tris, reg, fit, built_cylinders,
                        args.stitch_tol_mm)
                    rec["note"] = note
                except Exception as exc:
                    rec["note"] = (
                        f"build failed: {type(exc).__name__}: {exc}")
            elif fit["kind"] == "cylinder":
                face, rec["note"] = cyl_faces[fit["id"]]
            elif fit["kind"] == "freeform_unfit":
                rec["note"] = ("freeform_unfit — no analytic surface within "
                               "tolerance; face not built")
            else:
                rec["note"] = (f"fitted as '{fit['kind']}' — not buildable "
                               "in scan_to_brep v1 (plane+cylinder only)")

            if face is not None:
                rec["built"] = True
                fa = _face_area(face)
                rec["face_area_mm2"] = round(fa, 6)
                ra = fit["area_mm2"]
                if ra > 1e-9 and abs(fa - ra) / ra > 0.2:
                    rec["note"] = ((rec["note"] + "; ") if rec["note"] else
                                   "") + (
                        f"area_mismatch: rebuilt face {fa:.2f} mm² vs mesh "
                        f"region {ra:.2f} mm²")
                built_faces.append(face)
            region_records.append(rec)

        unbuilt = [r for r in region_records if not r["built"]]
        if not built_faces:
            raise ValueError(
                "fm.no_buildable_regions: every buildable-kind region "
                "failed face construction — "
                + "; ".join(f"region {r['id']}: {r['note']}" for r in unbuilt))
        if unbuilt:
            notes.append(
                f"{len(unbuilt)} of {len(region_records)} region(s) not "
                "rebuilt — shell cannot be closed; returning partial result")

        # ---- sew + honest solid lift (mesh_import recipe) --------------------
        sewer = BRepBuilderAPI_Sewing(float(args.sewing_tolerance_mm))
        for f in built_faces:
            sewer.Add(f)
        sewer.Perform()
        sewn = sewer.SewedShape()
        if sewn is None or sewn.IsNull():
            raise ValueError(
                "fm.no_buildable_regions: sewing the rebuilt faces produced "
                "a null shape")
        try:
            free_edges = int(sewer.NbFreeEdges())
        except Exception:
            free_edges = -1

        result_shape = sewn
        volume: float | None = None
        shells: list = []
        exp = TopExp_Explorer(sewn, TopAbs_SHELL)
        while exp.More():
            shells.append(TopoDS.Shell_s(exp.Current()))
            exp.Next()
        closed = []
        for shl in shells:
            try:
                if (BRepCheck_Shell(shl).Closed()
                        == BRepCheck_Status.BRepCheck_NoError):
                    closed.append(shl)
            except Exception:
                continue
        if len(shells) == 1 and len(closed) == 1:
            solid_maker = BRepBuilderAPI_MakeSolid(closed[0])
            if solid_maker.IsDone():
                candidate = solid_maker.Solid()
                if candidate is not None and not candidate.IsNull():
                    vol = _volume(candidate)
                    if vol < 0:
                        # inward-wound (proven trap): ShapeFix, else reverse
                        try:
                            from OCP.ShapeFix import ShapeFix_Solid
                            sf = ShapeFix_Solid(candidate)
                            sf.Perform()
                            fixed = sf.Solid()
                            if fixed is not None and not fixed.IsNull():
                                v2 = _volume(fixed)
                                if v2 > 0:
                                    candidate, vol = fixed, v2
                        except Exception:
                            pass
                        if vol < 0:
                            rev = candidate.Reversed()
                            v3 = _volume(rev)
                            if v3 > 0:
                                candidate, vol = rev, v3
                    result_shape = candidate
                    volume = vol

        # is_solid = TopAbs_SOLID count AND volume > 1e-6 (house rule)
        n_solids = _count_solids(result_shape)
        is_solid = bool(n_solids > 0 and volume is not None and volume > 1e-6)
        if not is_solid:
            volume = None
            notes.append(
                f"sew left the shell open (free_edges={free_edges}, "
                f"shells={len(shells)}, closed={len(closed)}) — returning "
                "PARTIAL shell, is_solid=False")

        grade = "reconstructed_solid" if is_solid else "partial_shell"
        extras = {
            "scan_to_brep": {
                "grade": grade,
                "source": source,
                "is_solid": is_solid,
                "volume_mm3": (round(volume, 6) if volume is not None
                               else None),
                "free_edge_count": free_edges,
                "n_regions": len(region_records),
                "built_face_count": len(built_faces),
                "freeform_area_fraction": round(float(frac), 6),
                "regions": region_records,
                "notes": notes,
            }
        }
        return SkillResult(
            body=Part(result_shape),
            history=EntityHistoryMap(
                rules={"output_solid": HistoryRule.GENERATED_NEW}),
            extras=extras,
        )
