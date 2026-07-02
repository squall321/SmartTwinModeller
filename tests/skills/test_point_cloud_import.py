"""point_cloud_import — raw .xyz / vertex-only .ply point-cloud ingest.

Verification per the scan-to-CAD spec:
  - synthetic 10k-point cloud on the plane z=5 (x,y in [0,20], tiny gaussian
    noise, FIXED seed default_rng(42)) → n_points=10000, centroid ~(10,10,5),
    SVD best-fit plane normal ~(0,0,±1) with rms < 2*sigma, aabb size
    ~[20,20,~0],
  - .xyz with extra columns (intensity / rgb) parses — first 3 floats taken,
  - a .ply WITH faces is refused (fm.ply_has_faces) and the message
    cross-references the mesh_import path — verified against the REAL
    mesh_export PLY writer output (round-trip of this session's writer),
  - missing file refused (fm.file_not_found),
  - too many points refused (fm.too_many_points),
  - unknown extension refused (fm.unsupported_format),
  - garbage line refused (fm.point_cloud_parse_failed),
  - >cap clouds subsample the BODY vertices only — stats stay full-set.

Import the skill module at top so its @skill registration happens without
touching the shared export_manifest.
"""
from __future__ import annotations

import numpy as np
import pytest

from phone_designer.skills.io.point_cloud_import import PointCloudImport


SIGMA = 0.02


def _plane_cloud(n=10_000, sigma=SIGMA, seed=42):
    """n points on z=5 within x,y in [0,20] plus tiny gaussian z-noise."""
    rng = np.random.default_rng(seed)
    x = rng.uniform(0.0, 20.0, n)
    y = rng.uniform(0.0, 20.0, n)
    z = 5.0 + rng.normal(0.0, sigma, n)
    return np.column_stack([x, y, z])


def _write_xyz(path, pts, extra_cols=False):
    lines = ["# synthetic plane cloud", ""]
    for row in pts:
        base = f"{row[0]:.6f} {row[1]:.6f} {row[2]:.6f}"
        if extra_cols:
            base += " 128 64 32 0.87"   # rgb + intensity — must be ignored
        lines.append(base)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _count_vertices(body):
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer

    shape = body.wrapped if hasattr(body, "wrapped") else body
    n = 0
    it = TopExp_Explorer(shape, TopAbs_VERTEX)
    while it.More():
        n += 1
        it.Next()
    return n


# ------------------------------------------------------------ plane cloud ----

def test_plane_cloud_stats_and_body(tmp_path):
    pts = _plane_cloud()
    f = tmp_path / "plane.xyz"
    _write_xyz(f, pts)

    r = PointCloudImport().apply(None, {"path": str(f)})
    ex = r.extras

    assert ex["format"] == "xyz"
    assert ex["n_points"] == 10_000

    cx, cy, cz = ex["centroid"]
    assert abs(cx - 10.0) < 0.3 and abs(cy - 10.0) < 0.3
    assert abs(cz - 5.0) < 0.01

    # AABB ~ [20, 20, ~0]
    sx, sy, sz = ex["aabb"]["size"]
    assert abs(sx - 20.0) < 0.2 and abs(sy - 20.0) < 0.2
    assert sz < 10 * SIGMA          # z spread is pure noise (~±4σ extremes)
    assert abs(ex["aabb"]["min"][2] - 5.0) < 10 * SIGMA

    # SVD best-fit plane: normal ~(0,0,±1), rms below 2σ
    plane = ex["best_fit_plane"]
    assert plane is not None
    nx, ny, nz = plane["normal"]
    assert abs(abs(nz) - 1.0) < 1e-4
    assert abs(nx) < 0.01 and abs(ny) < 0.01
    assert plane["rms_distance"] < 2 * SIGMA
    assert abs(plane["point"][2] - 5.0) < 0.01

    # rms_from_centroid ≈ in-plane spread of a 20×20 uniform square (~8.16)
    assert 7.0 < ex["rms_from_centroid"] < 9.5

    # Body: real OCCT compound with one vertex per point (no subsampling)
    assert r.body is not None
    assert ex["subsampled"] is False
    assert ex["subsample_note"] is None
    assert ex["body_vertex_count"] == 10_000
    assert _count_vertices(r.body) == 10_000

    # Honesty flags
    assert ex["is_solid"] is False
    assert ex["reconstruction"] == "unsupported (ingest + fit only)"

    # extras must be strict-JSON-safe
    import json
    json.dumps({k: v for k, v in ex.items() if k != "_step_metrics"},
               allow_nan=False)


# ------------------------------------------------------ xyz extra columns ----

def test_xyz_extra_columns_parse(tmp_path):
    pts = _plane_cloud(n=500)
    f = tmp_path / "rgb.xyz"
    _write_xyz(f, pts, extra_cols=True)

    r = PointCloudImport().apply(None, {"path": str(f)})
    assert r.extras["n_points"] == 500
    assert abs(r.extras["centroid"][2] - 5.0) < 0.05
    assert _count_vertices(r.body) == 500


# --------------------------------------------------------- vertex-only ply ---

def test_ply_vertex_only_parses(tmp_path):
    f = tmp_path / "cloud.ply"
    header = [
        "ply",
        "format ascii 1.0",
        "comment scanner dump",
        "element vertex 4",
        "property float x",
        "property float y",
        "property float z",
        "property uchar red",       # extra property AFTER z — must be ignored
        "end_header",
    ]
    rows = ["0 0 0 255", "10 0 0 255", "10 10 0 255", "0 10 0 255"]
    f.write_text("\n".join(header + rows) + "\n", encoding="ascii")

    r = PointCloudImport().apply(None, {"path": str(f)})
    ex = r.extras
    assert ex["format"] == "ply"
    assert ex["n_points"] == 4
    assert ex["centroid"] == [5.0, 5.0, 0.0]
    assert _count_vertices(r.body) == 4
    # 4 coplanar points → exact plane, rms ~0, normal ±Z
    assert abs(abs(ex["best_fit_plane"]["normal"][2]) - 1.0) < 1e-9
    assert ex["best_fit_plane"]["rms_distance"] < 1e-9


def test_ply_element_face_zero_is_accepted(tmp_path):
    """'element face 0' (some exporters always emit it) is still a cloud."""
    f = tmp_path / "face0.ply"
    f.write_text("\n".join([
        "ply", "format ascii 1.0",
        "element vertex 2",
        "property float x", "property float y", "property float z",
        "element face 0",
        "property list uchar int vertex_indices",
        "end_header",
        "0 0 0", "1 1 1",
    ]) + "\n", encoding="ascii")
    r = PointCloudImport().apply(None, {"path": str(f)})
    assert r.extras["n_points"] == 2
    assert r.extras["best_fit_plane"] is None  # <3 points → honest None


# ----------------------------------------------- ply WITH faces → refusal ----

def test_ply_with_faces_refused_points_to_mesh_import(tmp_path):
    f = tmp_path / "mesh.ply"
    f.write_text("\n".join([
        "ply", "format ascii 1.0",
        "element vertex 3",
        "property float x", "property float y", "property float z",
        "element face 1",
        "property list uchar int vertex_indices",
        "end_header",
        "0 0 0", "1 0 0", "0 1 0",
        "3 0 1 2",
    ]) + "\n", encoding="ascii")

    with pytest.raises(ValueError, match=r"fm\.ply_has_faces") as ei:
        PointCloudImport().apply(None, {"path": str(f)})
    # cross-reference to the mesh path must be in the message
    assert "mesh_import" in str(ei.value)
    assert "mesh_to_brep" in str(ei.value)


def test_real_mesh_export_ply_output_is_refused(tmp_path):
    """Round-trip against THIS session's PLY writer: mesh_export always emits
    faces, so point_cloud_import must refuse its output with the same
    cross-reference — proving our header parser reads the real writer's
    header, not a toy."""
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.io.mesh_export import MeshExport

    box = Box().apply(None, {"length_mm": 20, "width_mm": 20,
                             "height_mm": 10}).body
    out = tmp_path / "box.ply"
    MeshExport().apply(box, {"path": str(out), "format": "ply"})
    assert out.exists()

    with pytest.raises(ValueError, match=r"fm\.ply_has_faces"):
        PointCloudImport().apply(None, {"path": str(out)})


# ------------------------------------------------------- structured refusals -

def test_missing_file_fm_file_not_found(tmp_path):
    with pytest.raises(ValueError, match=r"fm\.file_not_found"):
        PointCloudImport().apply(None, {"path": str(tmp_path / "nope.xyz")})


def test_too_many_points_xyz(tmp_path):
    pts = _plane_cloud(n=50)
    f = tmp_path / "big.xyz"
    _write_xyz(f, pts)
    with pytest.raises(ValueError, match=r"fm\.too_many_points"):
        PointCloudImport().apply(None, {"path": str(f), "max_points": 10})


def test_too_many_points_ply_header_gate(tmp_path):
    f = tmp_path / "big.ply"
    f.write_text("\n".join([
        "ply", "format ascii 1.0",
        "element vertex 100",
        "property float x", "property float y", "property float z",
        "end_header",
    ] + ["0 0 0"] * 100) + "\n", encoding="ascii")
    with pytest.raises(ValueError, match=r"fm\.too_many_points"):
        PointCloudImport().apply(None, {"path": str(f), "max_points": 10})


def test_unsupported_extension(tmp_path):
    f = tmp_path / "cloud.stl"
    f.write_text("solid x\nendsolid x\n", encoding="ascii")
    with pytest.raises(ValueError, match=r"fm\.unsupported_format"):
        PointCloudImport().apply(None, {"path": str(f)})


def test_garbage_line_fm_parse_failed(tmp_path):
    f = tmp_path / "bad.xyz"
    f.write_text("1 2 3\nhello world nope\n", encoding="ascii")
    with pytest.raises(ValueError, match=r"fm\.point_cloud_parse_failed"):
        PointCloudImport().apply(None, {"path": str(f)})


def test_empty_xyz_fm_parse_failed(tmp_path):
    f = tmp_path / "empty.xyz"
    f.write_text("# only comments\n\n", encoding="ascii")
    with pytest.raises(ValueError, match=r"fm\.point_cloud_parse_failed"):
        PointCloudImport().apply(None, {"path": str(f)})


def test_binary_ply_fm_parse_failed(tmp_path):
    f = tmp_path / "bin.ply"
    f.write_text("ply\nformat binary_little_endian 1.0\n"
                 "element vertex 1\nend_header\n", encoding="ascii")
    with pytest.raises(ValueError, match=r"fm\.point_cloud_parse_failed"):
        PointCloudImport().apply(None, {"path": str(f)})


# ----------------------------------------------------------- subsampling -----

def test_body_subsampled_stats_full(tmp_path):
    pts = _plane_cloud(n=5_000)
    f = tmp_path / "sub.xyz"
    _write_xyz(f, pts)

    r = PointCloudImport().apply(
        None, {"path": str(f), "subsample_body_vertices": 1_000})
    ex = r.extras
    assert ex["n_points"] == 5_000                 # stats = FULL set
    assert ex["subsampled"] is True
    assert ex["subsample_note"] is not None and "5000" in ex["subsample_note"]
    assert ex["body_vertex_count"] <= 1_000
    assert _count_vertices(r.body) == ex["body_vertex_count"]
    # full-set plane fit still holds after body subsampling
    assert abs(abs(ex["best_fit_plane"]["normal"][2]) - 1.0) < 1e-4


# ------------------------------------------------------------- spec sanity ---

def test_point_cloud_import_spec_registered():
    assert PointCloudImport.spec.name == "point_cloud_import"
    assert PointCloudImport.spec.category == "create"
    assert set(PointCloudImport.spec.failure_modes) == {
        "fm.file_not_found", "fm.unsupported_format",
        "fm.point_cloud_parse_failed", "fm.ply_has_faces",
        "fm.too_many_points",
    }
    assert any(pc.kind == "body_present"
               for pc in PointCloudImport.spec.post_conditions)
