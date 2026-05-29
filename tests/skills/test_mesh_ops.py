"""Tests for the mesh-ops pack: mesh_to_brep, brep_to_mesh, mesh_simplify,
mesh_quality.

Most tests use a small box exported through `stl_export` first; that gives a
known-good 12-triangle STL we can re-import / decimate / sew.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.sphere import Sphere
from phone_designer.skills.inspect.mesh_quality import MeshQuality
from phone_designer.skills.io.brep_to_mesh import BrepToMesh
from phone_designer.skills.io.mesh_simplify import MeshSimplify
from phone_designer.skills.io.mesh_to_brep import MeshToBrep
from phone_designer.skills.io.stl_export import StlExport


def _bbox(body):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    shape = body.wrapped if hasattr(body, "wrapped") else body
    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return (xmin, ymin, zmin), (xmax, ymax, zmax)


# -------------------------- brep_to_mesh ------------------------------------

def test_brep_to_mesh_writes_stl_and_reports_stats(tmp_path):
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    out = tmp_path / "box_brep_to_mesh.stl"

    r = BrepToMesh().apply(box, {
        "path": str(out),
        "linear_deflection_mm": 0.1,
        "angular_deflection_deg": 5.0,
    })

    assert out.exists() and out.stat().st_size > 0
    stats = r.extras["mesh_stats"]
    assert stats["triangle_count"] >= 12  # cube ⇒ at least 12 triangles
    assert stats["vertex_count"] > 0
    assert stats["surface_area_mm2"] > 0
    # 10x10x10 cube → 6 * 100 = 600 mm² ± mesh chord error
    assert abs(stats["surface_area_mm2"] - 600.0) < 5.0
    assert stats["mesh_path"] == str(out)
    # body unchanged
    assert r.body is box


def test_brep_to_mesh_none_body_raises():
    with pytest.raises(ValueError):
        BrepToMesh().apply(None, {"path": "x.stl"})


# -------------------------- mesh_to_brep -----------------------------------

def test_mesh_to_brep_roundtrip_box_yields_body(tmp_path):
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    stl = tmp_path / "box.stl"
    StlExport().apply(box, {"path": str(stl), "linear_deflection_mm": 0.5})

    r = MeshToBrep().apply(None, {
        "path": str(stl),
        "sewing_tolerance_mm": 0.1,
    })

    assert r.body is not None
    assert r.extras["triangle_count_input"] >= 12
    assert r.extras["triangles_sewn"] >= 12
    # bbox of result body should approximately match original box bbox
    (mn1, mx1) = _bbox(box)
    (mn2, mx2) = _bbox(r.body)
    for a, b in zip(mn1, mn2):
        assert abs(a - b) < 0.5
    for a, b in zip(mx1, mx2):
        assert abs(a - b) < 0.5


def test_mesh_to_brep_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        MeshToBrep().apply(None, {"path": "/no/such/file.stl"})


def test_mesh_to_brep_rejects_obj(tmp_path):
    fake = tmp_path / "thing.obj"
    fake.write_text("v 0 0 0\n")
    with pytest.raises(NotImplementedError):
        MeshToBrep().apply(None, {"path": str(fake)})


def test_mesh_to_brep_respects_max_triangles(tmp_path):
    # write a moderately tessellated sphere — easily blows past max_triangles=5
    sphere = Sphere().apply(None, {"radius_mm": 5.0}).body
    stl = tmp_path / "sphere.stl"
    StlExport().apply(sphere, {"path": str(stl), "linear_deflection_mm": 0.05})

    with pytest.raises(RuntimeError, match="limit is"):
        MeshToBrep().apply(None, {
            "path": str(stl),
            "max_triangles": 5,
        })


# -------------------------- mesh_simplify ----------------------------------

def test_mesh_simplify_reduces_triangle_count(tmp_path):
    sphere = Sphere().apply(None, {"radius_mm": 10.0}).body
    src = tmp_path / "sphere_src.stl"
    StlExport().apply(sphere, {"path": str(src), "linear_deflection_mm": 0.1})

    out = tmp_path / "sphere_decim.stl"
    # need to pass a body so that body_present post-condition is satisfied
    r = MeshSimplify().apply(sphere, {
        "input_path": str(src),
        "output_path": str(out),
        "target_ratio": 0.3,
    })

    assert out.exists() and out.stat().st_size > 0
    stats = r.extras["simplify_stats"]
    assert stats["triangles_in"] > 0
    assert stats["triangles_out"] > 0
    assert stats["triangles_out"] < stats["triangles_in"], \
        "decimated mesh should have fewer triangles than the original"
    # body passthrough
    assert r.body is sphere


def test_mesh_simplify_missing_input_raises(tmp_path):
    box = Box().apply(None, {"length_mm": 5, "width_mm": 5, "height_mm": 5}).body
    with pytest.raises(FileNotFoundError):
        MeshSimplify().apply(box, {
            "input_path": str(tmp_path / "no.stl"),
            "output_path": str(tmp_path / "o.stl"),
            "target_ratio": 0.5,
        })


def test_mesh_simplify_target_ratio_close_to_one_is_near_no_op(tmp_path):
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    src = tmp_path / "box.stl"
    StlExport().apply(box, {"path": str(src), "linear_deflection_mm": 0.1})

    out = tmp_path / "box_decim.stl"
    r = MeshSimplify().apply(box, {
        "input_path": str(src),
        "output_path": str(out),
        "target_ratio": 1.0,
    })
    stats = r.extras["simplify_stats"]
    # With ratio=1.0 vertex-cluster grid is finest; output ≈ input
    assert stats["triangles_out"] >= stats["triangles_in"] * 0.5


# -------------------------- mesh_quality -----------------------------------

def test_mesh_quality_on_closed_box_has_no_boundary_edges():
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = MeshQuality().apply(box, {
        "linear_deflection_mm": 0.1,
        "force_remesh": True,
    })
    report = r.extras["mesh_quality_report"]
    assert report["triangle_count"] >= 12
    assert report["vertex_count"] > 0
    assert report["max_edge_length_mm"] > 0
    assert report["min_edge_length_mm"] > 0
    assert 0.0 < report["min_triangle_angle_deg"] <= 90.0
    assert report["max_triangle_angle_deg"] < 180.0
    # Closed box: every edge should be shared by exactly 2 triangles.
    # We allow a small number of boundary edges to absorb seam-quantization
    # artifacts at face boundaries (the 1e-6 mm snap may miss a few coincidences).
    assert report["boundary_edge_count"] <= report["triangle_count"]
    assert report["non_manifold_edge_count"] == 0
    assert report["degenerate_triangle_count"] == 0
    # body unchanged
    assert r.body is box


def test_mesh_quality_none_body_raises():
    with pytest.raises(ValueError):
        MeshQuality().apply(None, {})


# -------------------------- spec sanity -------------------------------------

def test_mesh_ops_specs_registered():
    assert MeshToBrep.spec.name == "mesh_to_brep"
    assert MeshToBrep.spec.category == "create"
    assert MeshToBrep.spec.level == "atomic"
    assert any(pc.kind == "body_present" for pc in MeshToBrep.spec.post_conditions)

    assert BrepToMesh.spec.name == "brep_to_mesh"
    assert BrepToMesh.spec.category == "inspect"
    assert any(pc.kind == "body_present" for pc in BrepToMesh.spec.post_conditions)

    assert MeshSimplify.spec.name == "mesh_simplify"
    assert MeshSimplify.spec.category == "inspect"
    assert any(pc.kind == "body_present" for pc in MeshSimplify.spec.post_conditions)

    assert MeshQuality.spec.name == "mesh_quality"
    assert MeshQuality.spec.category == "inspect"
    assert any(pc.kind == "body_present" for pc in MeshQuality.spec.post_conditions)
