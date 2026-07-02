"""mesh_import — OBJ/PLY mesh file → BRep shell/solid.

Headline round-trip pin: Box(20,20,10) → mesh_export (OBJ and PLY) →
mesh_import → volume ~4000 mm³ (rel 1e-3) and is_solid True for BOTH formats.

Also covered:
  - binary_little_endian AND binary_big_endian PLY (scanner case, with extra
    per-vertex normal properties that must be consumed and discarded),
  - hand-written quad-face OBJ (fan triangulation), negative OBJ indices,
    a/b/c face-token forms,
  - inward-wound cube → orientation fix still yields positive volume,
  - deliberately open mesh (face lines deleted) → honest shell:
    is_solid=False, volume_mm3=None, free_edge_count>0,
  - structured refusals: fm.file_not_found / fm.unsupported_format /
    fm.mesh_parse_failed / fm.too_many_triangles / fm.sewing_failed.

Import the skill module at top so its @skill registration happens without
touching the shared export_manifest.
"""
from __future__ import annotations

import struct

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.io.mesh_export import MeshExport
from phone_designer.skills.io.mesh_import import MeshImport


# ------------------------------------------------------------------ helpers --

def _make_box():
    return Box().apply(
        None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}
    ).body


def _export_box(tmp_path, fmt):
    box = _make_box()
    out = tmp_path / f"box.{fmt}"
    MeshExport().apply(box, {"path": str(out), "format": fmt})
    return out


# 2x2x2 cube (corners at 0 and 2), quad faces, OUTWARD winding. Volume = 8.
_CUBE_VERTS = [
    (0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 2.0, 0.0), (0.0, 2.0, 0.0),
    (0.0, 0.0, 2.0), (2.0, 0.0, 2.0), (2.0, 2.0, 2.0), (0.0, 2.0, 2.0),
]
_CUBE_QUADS = [  # 1-based, outward
    (1, 4, 3, 2),  # bottom  (-z)
    (5, 6, 7, 8),  # top     (+z)
    (1, 2, 6, 5),  # front   (-y)
    (2, 3, 7, 6),  # right   (+x)
    (3, 4, 8, 7),  # back    (+y)
    (4, 1, 5, 8),  # left    (-x)
]


def _cube_triangles_0based():
    tris = []
    for (a, b, c, d) in _CUBE_QUADS:
        tris.append((a - 1, b - 1, c - 1))
        tris.append((a - 1, c - 1, d - 1))
    return tris


def _write_binary_ply(path, verts, tris, endian="<", with_normals=False):
    fmt_name = ("binary_little_endian" if endian == "<"
                else "binary_big_endian")
    props = ["property float x", "property float y", "property float z"]
    if with_normals:
        props += ["property float nx", "property float ny",
                  "property float nz"]
    header = "\n".join([
        "ply",
        f"format {fmt_name} 1.0",
        "comment test scanner-style binary",
        f"element vertex {len(verts)}",
        *props,
        f"element face {len(tris)}",
        "property list uchar int vertex_indices",
        "end_header",
    ]) + "\n"
    buf = bytearray(header.encode("ascii"))
    for v in verts:
        vals = list(v) + ([0.0, 0.0, 1.0] if with_normals else [])
        buf += struct.pack(endian + "f" * len(vals), *vals)
    for t in tris:
        buf += struct.pack(endian + "B", 3)
        buf += struct.pack(endian + "3i", *t)
    path.write_bytes(bytes(buf))


# ------------------------------------------- headline round-trip pin ---------

def test_roundtrip_obj_box_solid_volume_4000(tmp_path):
    src = _export_box(tmp_path, "obj")
    r = MeshImport().apply(None, {"path": str(src)})

    assert r.body is not None
    ex = r.extras
    assert ex["format"] == "obj"
    assert ex["is_solid"] is True
    assert ex["volume_mm3"] == pytest.approx(4000.0, rel=1e-3)
    assert ex["n_triangles"] == 12
    assert ex["n_degenerate_skipped"] == 0
    assert ex["free_edge_count"] == 0
    xmin, ymin, zmin, xmax, ymax, zmax = ex["bbox_mm"]
    assert xmax - xmin == pytest.approx(20.0, abs=0.2)
    assert ymax - ymin == pytest.approx(20.0, abs=0.2)
    assert zmax - zmin == pytest.approx(10.0, abs=0.2)


def test_roundtrip_ply_box_solid_volume_4000(tmp_path):
    src = _export_box(tmp_path, "ply")
    r = MeshImport().apply(None, {"path": str(src)})

    ex = r.extras
    assert ex["format"] == "ply"
    assert ex["is_solid"] is True
    assert ex["volume_mm3"] == pytest.approx(4000.0, rel=1e-3)
    assert ex["n_triangles"] == 12
    assert ex["free_edge_count"] == 0


# --------------------------------------------------------------- binary PLY --

def test_binary_little_endian_ply_with_normals(tmp_path):
    """Scanner-style binary PLY: extra nx/ny/nz vertex props are consumed."""
    out = tmp_path / "cube_le.ply"
    _write_binary_ply(out, _CUBE_VERTS, _cube_triangles_0based(),
                      endian="<", with_normals=True)
    r = MeshImport().apply(None, {"path": str(out)})
    ex = r.extras
    assert ex["format"] == "ply"
    assert ex["n_vertices"] == 8
    assert ex["n_triangles"] == 12
    assert ex["is_solid"] is True
    assert ex["volume_mm3"] == pytest.approx(8.0, rel=1e-3)


def test_binary_big_endian_ply(tmp_path):
    out = tmp_path / "cube_be.ply"
    _write_binary_ply(out, _CUBE_VERTS, _cube_triangles_0based(), endian=">")
    r = MeshImport().apply(None, {"path": str(out)})
    assert r.extras["is_solid"] is True
    assert r.extras["volume_mm3"] == pytest.approx(8.0, rel=1e-3)


# ---------------------------------------------------- OBJ parsing features ---

def _cube_obj_text(faces_1based, token=lambda i: str(i)):
    lines = [f"v {x} {y} {z}" for (x, y, z) in _CUBE_VERTS]
    for f in faces_1based:
        lines.append("f " + " ".join(token(i) for i in f))
    return "\n".join(lines) + "\n"


def test_obj_quad_faces_fan_triangulated(tmp_path):
    """Quad faces + a/b/c-style tokens → 12 triangles, watertight cube."""
    out = tmp_path / "cube_quads.obj"
    out.write_text(_cube_obj_text(_CUBE_QUADS,
                                  token=lambda i: f"{i}/{i}/{i}"),
                   encoding="ascii")
    r = MeshImport().apply(None, {"path": str(out)})
    ex = r.extras
    assert ex["n_triangles"] == 12  # 6 quads fan-triangulated
    assert ex["is_solid"] is True
    assert ex["volume_mm3"] == pytest.approx(8.0, rel=1e-3)


def test_obj_negative_indices(tmp_path):
    """Negative (relative) indices: with 8 verts defined first, -8..-1 map to
    vertices 1..8."""
    neg_quads = [tuple(i - 9 for i in q) for q in _CUBE_QUADS]
    out = tmp_path / "cube_neg.obj"
    out.write_text(_cube_obj_text(neg_quads), encoding="ascii")
    r = MeshImport().apply(None, {"path": str(out)})
    assert r.extras["is_solid"] is True
    assert r.extras["volume_mm3"] == pytest.approx(8.0, rel=1e-3)


def test_obj_inward_wound_cube_orientation_fixed(tmp_path):
    """All faces wound INWARD (proven trap) → orientation fix still yields a
    positive-volume solid."""
    inward = [tuple(reversed(q)) for q in _CUBE_QUADS]
    out = tmp_path / "cube_inward.obj"
    out.write_text(_cube_obj_text(inward), encoding="ascii")
    r = MeshImport().apply(None, {"path": str(out)})
    assert r.extras["is_solid"] is True
    assert r.extras["volume_mm3"] == pytest.approx(8.0, rel=1e-3)
    assert r.extras["volume_mm3"] > 0


# ------------------------------------------------------------- open mesh -----

def test_open_mesh_is_honest_shell(tmp_path):
    """Delete face lines from an exported box OBJ → is_solid=False,
    volume_mm3=None (no pseudo-mass leak), free_edge_count>0."""
    src = _export_box(tmp_path, "obj")
    lines = src.read_text(encoding="ascii").splitlines()
    face_lines = [ln for ln in lines if ln.startswith("f ")]
    keep = [ln for ln in lines if not ln.startswith("f ")] + face_lines[:-2]
    open_path = tmp_path / "open.obj"
    open_path.write_text("\n".join(keep) + "\n", encoding="ascii")

    r = MeshImport().apply(None, {"path": str(open_path)})
    ex = r.extras
    assert r.body is not None  # body_present still holds — honest shell
    assert ex["n_triangles"] == 10
    assert ex["is_solid"] is False
    assert ex["volume_mm3"] is None
    assert ex["free_edge_count"] > 0


# ---------------------------------------------------------------- refusals ---

def test_missing_file_fm_file_not_found(tmp_path):
    with pytest.raises(ValueError, match=r"fm\.file_not_found"):
        MeshImport().apply(None, {"path": str(tmp_path / "nope.obj")})


def test_garbage_obj_fm_mesh_parse_failed(tmp_path):
    bad = tmp_path / "garbage.obj"
    bad.write_text("this is not a mesh at all\njust words\n",
                   encoding="ascii")
    with pytest.raises(ValueError, match=r"fm\.mesh_parse_failed"):
        MeshImport().apply(None, {"path": str(bad)})


def test_garbage_ply_fm_mesh_parse_failed(tmp_path):
    bad = tmp_path / "garbage.ply"
    bad.write_bytes(b"\x00\x01\x02 definitely not a ply header")
    with pytest.raises(ValueError, match=r"fm\.mesh_parse_failed"):
        MeshImport().apply(None, {"path": str(bad)})


def test_truncated_binary_ply_fm_mesh_parse_failed(tmp_path):
    """Binary body shorter than the header promises → wrapped struct.error."""
    out = tmp_path / "trunc.ply"
    _write_binary_ply(out, _CUBE_VERTS, _cube_triangles_0based())
    data = out.read_bytes()
    out.write_bytes(data[:len(data) - 30])
    with pytest.raises(ValueError, match=r"fm\.mesh_parse_failed"):
        MeshImport().apply(None, {"path": str(out)})


def test_unsupported_extension_fm_unsupported_format(tmp_path):
    stl = tmp_path / "mesh.stl"
    stl.write_text("solid x\nendsolid x\n", encoding="ascii")
    with pytest.raises(ValueError, match=r"fm\.unsupported_format"):
        MeshImport().apply(None, {"path": str(stl)})


def test_too_many_triangles_fm(tmp_path):
    src = _export_box(tmp_path, "obj")  # 12 triangles
    with pytest.raises(ValueError, match=r"fm\.too_many_triangles"):
        MeshImport().apply(None, {"path": str(src), "max_triangles": 4})


def test_all_degenerate_fm_sewing_failed(tmp_path):
    """Collinear-only mesh parses fine but every triangle is degenerate."""
    bad = tmp_path / "collinear.obj"
    bad.write_text("v 0 0 0\nv 1 0 0\nv 2 0 0\nf 1 2 3\n", encoding="ascii")
    with pytest.raises(ValueError, match=r"fm\.sewing_failed"):
        MeshImport().apply(None, {"path": str(bad)})


# ------------------------------------------------------------- spec sanity ---

def test_mesh_import_spec_registered():
    assert MeshImport.spec.name == "mesh_import"
    assert MeshImport.spec.category == "create"
    assert any(pc.kind == "body_present"
               for pc in MeshImport.spec.post_conditions)
    for fm in ("fm.file_not_found", "fm.unsupported_format",
               "fm.mesh_parse_failed", "fm.too_many_triangles",
               "fm.sewing_failed"):
        assert fm in MeshImport.spec.failure_modes
