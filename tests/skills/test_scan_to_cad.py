"""Scan-to-CAD v1 spike (Phase 3-1): mesh → segmentation → analytic fit → B-rep.

Self-contained round-trip pins ONLY (no self-scored corpus ratios — the
corpus go/no-go gate is the maintainer's, run later):

(a) Box(20,20,10) → mesh_export OBJ → mesh_import → exactly 6 planar regions,
    6 plane fits rms < 1e-6, scan_to_brep → solid, volume 4000 ± 1%.
(b) Cylinder d=20 h=30 tessellated fine → lateral region found, cylinder fit
    radius ≈ 10 (≤1%), and the macro reconstructs the ANALYTIC solid
    (V = 3000π) via cap-loop → circle stitching.
(c) smooth-noisy blob → honest freeform_unfit + fm.organic_unsupported with
    the measured fraction; clean sphere → analytic fit but
    fm.no_buildable_regions (v1 builds plane+cylinder only).
(d) non-mesh solid input → WORKS via deterministic auto-tessellation
    (decision documented in mesh_segment_regions docstring; pinned here).
"""
from __future__ import annotations

import json
import math

import pytest
from build123d import Box, Cylinder, Sphere

from phone_designer.skills.io.mesh_export import MeshExport
from phone_designer.skills.io.mesh_import import MeshImport
from phone_designer.skills.reverse_engineer.fit_region_surfaces import (
    FitRegionSurfaces,
)
from phone_designer.skills.reverse_engineer.mesh_segment_regions import (
    MeshSegmentRegions,
)
from phone_designer.skills.reverse_engineer.scan_to_brep import ScanToBrep


FINE = {"linear_deflection_mm": 0.02}


def _segment(body, **kw):
    return MeshSegmentRegions().apply(body, kw).extras["mesh_segment_regions"]


def _fit(body, **kw):
    return FitRegionSurfaces().apply(body, kw).extras["fit_region_surfaces"]


def _box_mesh(tmp_path):
    """Box(20,20,10) → OBJ → mesh_import (the faceted-shell front door)."""
    path = str(tmp_path / "box.obj")
    MeshExport().apply(Box(20, 20, 10), {"path": path})
    return MeshImport().apply(None, {"path": path}).body


def _blob_obj(tmp_path) -> str:
    """Smooth low-frequency bumpy UV-sphere — organic by construction.

    r(θ, φ) = 10 + 1.5·sin(3θ)·sin(2φ): adjacent facet normals stay within
    the 15° region-growing threshold (mesh is smooth), so triangles merge
    into large curved regions that NO analytic surface fits — the honest
    freeform_unfit path, deterministic (no RNG)."""
    nu, nv = 36, 18
    verts: list[tuple[float, float, float]] = []
    for j in range(1, nv):
        phi = math.pi * j / nv
        for i in range(nu):
            th = 2 * math.pi * i / nu
            r = 10.0 + 1.5 * math.sin(3 * th) * math.sin(2 * phi)
            verts.append((r * math.sin(phi) * math.cos(th),
                          r * math.sin(phi) * math.sin(th),
                          r * math.cos(phi)))
    verts.append((0.0, 0.0, 10.0))
    verts.append((0.0, 0.0, -10.0))
    i_top, i_bot = len(verts) - 2, len(verts) - 1

    def vid(i: int, j: int) -> int:
        return (j - 1) * nu + (i % nu)

    faces: list[tuple[int, int, int]] = []
    for j in range(1, nv - 1):
        for i in range(nu):
            a, b = vid(i, j), vid(i + 1, j)
            c, d = vid(i + 1, j + 1), vid(i, j + 1)
            faces.append((a, b, c))
            faces.append((a, c, d))
    for i in range(nu):
        faces.append((i_top, vid(i + 1, 1), vid(i, 1)))
        faces.append((i_bot, vid(i, nv - 1), vid(i + 1, nv - 1)))

    path = str(tmp_path / "blob.obj")
    with open(path, "w", encoding="ascii") as fh:
        for v in verts:
            fh.write("v %.6f %.6f %.6f\n" % v)
        for f in faces:
            fh.write("f %d %d %d\n" % (f[0] + 1, f[1] + 1, f[2] + 1))
    return path


# --------------------------------------------------------------------------- #
# (a) box OBJ round trip
# --------------------------------------------------------------------------- #

def test_box_obj_roundtrip_segments_six_planar_regions(tmp_path):
    mesh = _box_mesh(tmp_path)
    seg = _segment(mesh)
    assert seg["source"] == "faceted"
    assert seg["n_regions"] == 6
    assert seg["n_degenerate"] == 0
    # area-desc deterministic ordering: 2× (20×20) then 4× (20×10)
    areas = [r["area_mm2"] for r in seg["regions"]]
    assert areas == pytest.approx([400, 400, 200, 200, 200, 200], rel=1e-6)
    assert all(r["normal_spread_deg"] < 1e-6 for r in seg["regions"])
    # one region id per triangle (12 triangles, 2 per box face)
    assert len(seg["region_of_triangle"]) == 12
    assert sorted(set(seg["region_of_triangle"])) == [0, 1, 2, 3, 4, 5]


def test_box_fit_six_planes_rms_below_1e_6(tmp_path):
    fit = _fit(_box_mesh(tmp_path))
    assert len(fit["regions"]) == 6
    for f in fit["regions"]:
        assert f["kind"] == "plane"
        assert f["rms_mm"] < 1e-6
        assert f["grade"] == "exact"
        # unit normal in params
        n = f["params"]["normal"]
        assert sum(c * c for c in n) == pytest.approx(1.0, abs=1e-9)
    assert fit["freeform_area_fraction"] == 0.0
    # honesty pin: the artifact SAYS the fits are vertex-based module-local LS
    assert "triangle VERTICES" in fit["method_note"]
    assert "face-selector" in fit["method_note"]


def test_box_scan_to_brep_solid_volume_4000(tmp_path):
    res = ScanToBrep().apply(_box_mesh(tmp_path), {})
    e = res.extras["scan_to_brep"]
    assert e["grade"] == "reconstructed_solid"
    assert e["is_solid"] is True
    assert e["volume_mm3"] == pytest.approx(4000.0, rel=0.01)
    assert e["built_face_count"] == 6
    assert e["free_edge_count"] == 0
    # verify the returned BODY with the house is_solid rule, independently of
    # the skill's own claim: TopAbs_SOLID count AND volume > 1e-6
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    shape = res.body.wrapped
    n_solids = 0
    it = TopExp_Explorer(shape, TopAbs_SOLID)
    while it.More():
        n_solids += 1
        it.Next()
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    assert n_solids == 1
    assert float(g.Mass()) == pytest.approx(4000.0, rel=0.01)


# --------------------------------------------------------------------------- #
# (b) cylinder d=20 h=30
# --------------------------------------------------------------------------- #

def test_cylinder_segmentation_finds_lateral_region():
    seg = _segment(Cylinder(10, 30), **FINE)
    assert seg["source"] == "tessellated_brep"
    assert seg["n_regions"] == 3  # lateral + 2 caps
    lateral = seg["regions"][0]  # largest by area
    assert lateral["area_mm2"] == pytest.approx(math.pi * 20 * 30, rel=0.01)
    # wrap-around region: mean normal collapses — honest None + saturated spread
    assert lateral["mean_normal"] is None
    assert lateral["normal_spread_deg"] == pytest.approx(180.0, abs=1.0)
    # caps are flat
    for cap in seg["regions"][1:]:
        assert cap["normal_spread_deg"] < 1e-6


def test_cylinder_fit_radius_ten():
    fit = _fit(Cylinder(10, 30), **FINE)
    lateral = fit["regions"][0]
    assert lateral["kind"] == "cylinder"
    # tessellation vertices lie ON the true surface → ~1% is generous
    assert lateral["params"]["radius_mm"] == pytest.approx(10.0, rel=0.01)
    assert abs(lateral["params"]["axis"][2]) > 0.999  # axis ≈ ±Z
    assert lateral["rms_mm"] <= 1e-4
    assert lateral["grade"] == "exact"
    kinds = sorted(f["kind"] for f in fit["regions"])
    assert kinds == ["cylinder", "plane", "plane"]


def test_cylinder_scan_to_brep_reconstructs_analytic_solid():
    res = ScanToBrep().apply(Cylinder(10, 30), dict(FINE))
    e = res.extras["scan_to_brep"]
    # cap polygon loops are stitched to the analytic rim circle, so the sewn
    # result is the ANALYTIC cylinder — volume πr²h, not the chordal mesh's
    assert e["grade"] == "reconstructed_solid"
    assert e["is_solid"] is True
    assert e["volume_mm3"] == pytest.approx(math.pi * 100.0 * 30.0, rel=1e-3)
    assert e["built_face_count"] == 3
    assert e["free_edge_count"] == 0
    stitched = [r for r in e["regions"]
                if r["note"] and "stitched to cylinder" in r["note"]]
    assert len(stitched) == 2  # both caps


def test_plate_with_through_hole_round_trip():
    # the canonical prismatic machined part: outer prism + inward hole wall
    plate = Box(30, 30, 10) - Cylinder(5, 10)
    res = ScanToBrep().apply(plate, dict(FINE))
    e = res.extras["scan_to_brep"]
    assert e["is_solid"] is True
    assert e["volume_mm3"] == pytest.approx(
        9000.0 - math.pi * 25.0 * 10.0, rel=1e-3)
    assert e["built_face_count"] == 7  # 6 planes + hole wall
    hole = [r for r in e["regions"] if r["kind"] == "cylinder"]
    assert len(hole) == 1
    assert "inward" in hole[0]["note"]  # hole wall honestly marked + reversed


# --------------------------------------------------------------------------- #
# (c) honest refusals — freeform / organic / unbuildable
# --------------------------------------------------------------------------- #

def test_sphere_fits_sphere_but_scan_refuses_unbuildable():
    sph = Sphere(10)
    fit = _fit(sph, **FINE)
    assert fit["regions"][0]["kind"] == "sphere"
    assert fit["regions"][0]["params"]["radius_mm"] == pytest.approx(
        10.0, rel=0.01)
    # analytic fit succeeded, but v1 builds plane+cylinder only → structured
    # refusal, NOT a fabricated solid
    with pytest.raises(ValueError, match="fm.no_buildable_regions"):
        ScanToBrep().apply(sph, dict(FINE))


def test_noisy_blob_regions_are_honestly_freeform_unfit(tmp_path):
    blob = MeshImport().apply(None, {"path": _blob_obj(tmp_path)}).body
    fit = _fit(blob)
    biggest = fit["regions"][0]
    assert biggest["kind"] == "freeform_unfit"
    assert biggest["grade"] == "unfit"
    assert biggest["rms_mm"] is None
    # the refusal is *evidenced*: best candidate + its rms are reported
    assert biggest["best_candidate"] is not None
    assert biggest["best_candidate"]["rms_mm"] > fit["rms_tol_mm"]
    assert fit["freeform_area_fraction"] > 0.5


def test_noisy_blob_scan_to_brep_organic_refusal(tmp_path):
    blob = MeshImport().apply(None, {"path": _blob_obj(tmp_path)}).body
    with pytest.raises(ValueError, match="fm.organic_unsupported") as exc:
        ScanToBrep().apply(blob, {})
    # refusal carries the MEASURED freeform fraction
    assert "%" in str(exc.value)
    assert "rms_tol_mm" in str(exc.value)


# --------------------------------------------------------------------------- #
# (d) non-mesh input decision pin + structured refusals + determinism
# --------------------------------------------------------------------------- #

def test_plain_solid_auto_tessellation_decision_pin():
    # DECISION (documented in mesh_segment_regions): smooth B-rep input is
    # accepted via deterministic in-place auto-tessellation, reported as
    # source='tessellated_brep' — NOT refused.
    seg = _segment(Box(20, 20, 10))
    assert seg["source"] == "tessellated_brep"
    assert seg["n_regions"] == 6
    areas = [r["area_mm2"] for r in seg["regions"]]
    assert areas == pytest.approx([400, 400, 200, 200, 200, 200], rel=1e-6)


def test_not_a_mesh_refusals():
    # body=None
    with pytest.raises(ValueError, match="fm.not_a_mesh"):
        MeshSegmentRegions().apply(None, {})
    # face-less shape (an edge): raw TopoDS accepted by the skill surface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Pnt

    edge = BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(1, 1, 1)).Edge()
    with pytest.raises(ValueError, match="fm.not_a_mesh"):
        MeshSegmentRegions().apply(edge, {})
    # the macro propagates the same refusal
    with pytest.raises(ValueError, match="fm.not_a_mesh"):
        ScanToBrep().apply(None, {})


def test_too_many_triangles_guard():
    with pytest.raises(ValueError, match="fm.too_many_triangles"):
        MeshSegmentRegions().apply(Box(20, 20, 10), {"max_triangles": 4})
    with pytest.raises(ValueError, match="fm.too_many_triangles"):
        FitRegionSurfaces().apply(Box(20, 20, 10), {"max_triangles": 4})
    with pytest.raises(ValueError, match="fm.too_many_triangles"):
        ScanToBrep().apply(Box(20, 20, 10), {"max_triangles": 4})


def test_segmentation_and_fit_are_deterministic():
    # same input twice → byte-identical extras (ordering pin: area desc,
    # smallest-member-triangle tie-break; parallel=False tessellation)
    a = json.dumps(_segment(Cylinder(10, 30), **FINE), sort_keys=True)
    b = json.dumps(_segment(Cylinder(10, 30), **FINE), sort_keys=True)
    assert a == b
    fa = json.dumps(_fit(Cylinder(10, 30), **FINE), sort_keys=True)
    fb = json.dumps(_fit(Cylinder(10, 30), **FINE), sort_keys=True)
    assert fa == fb


def test_extras_are_strict_json_safe(tmp_path):
    # inf/nan must never leak (strict-JSON house rule)
    mesh = _box_mesh(tmp_path)
    seg = _segment(mesh)
    fit = _fit(mesh)
    scan = ScanToBrep().apply(mesh, {}).extras["scan_to_brep"]
    for artifact in (seg, fit, scan):
        json.dumps(artifact, allow_nan=False)
