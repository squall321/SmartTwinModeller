"""mesh_export — OBJ + PLY neutral-mesh writer.

Exports a Box(20, 20, 10) to OBJ and to PLY, then parses each file back and
asserts:
  - the file exists and is non-empty,
  - the parsed vertex cloud spans the 8 corners of the box (its bbox matches
    the box dimensions to within mesh deflection), i.e. the "8 unique-ish
    vertices region",
  - there are >0 triangles (a meshed box is exactly 12 triangles),
  - the body is passed through unchanged and extras report both formats.

Import the skill module at top so its @skill registration happens without
touching the shared export_manifest.
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.io.mesh_export import MeshExport


def _make_box():
    return Box().apply(
        None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}
    ).body


def _parse_obj(path):
    verts, faces = [], []
    for line in path.read_text(encoding="ascii").splitlines():
        line = line.strip()
        if line.startswith("v "):
            _, x, y, z = line.split()
            verts.append((float(x), float(y), float(z)))
        elif line.startswith("f "):
            parts = line.split()[1:]
            # OBJ face indices are 1-based; take the leading integer of each
            idx = [int(p.split("/")[0]) for p in parts]
            faces.append(tuple(idx))
    return verts, faces


def _parse_ply(path):
    text = path.read_text(encoding="ascii").splitlines()
    # locate header
    assert text[0].strip() == "ply", "PLY must start with magic 'ply'"
    n_vert = n_face = None
    end = None
    for i, line in enumerate(text):
        s = line.strip()
        if s.startswith("element vertex"):
            n_vert = int(s.split()[-1])
        elif s.startswith("element face"):
            n_face = int(s.split()[-1])
        elif s == "end_header":
            end = i
            break
    assert n_vert is not None and n_face is not None and end is not None
    body = text[end + 1:]
    verts = []
    for line in body[:n_vert]:
        x, y, z = line.split()
        verts.append((float(x), float(y), float(z)))
    faces = []
    for line in body[n_vert:n_vert + n_face]:
        nums = [int(v) for v in line.split()]
        assert nums[0] == 3, "each PLY face must be a triangle (count 3)"
        faces.append(tuple(nums[1:]))
    return verts, faces


def _bbox(verts):
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _assert_box_span(verts, tol=0.2):
    (mn, mx) = _bbox(verts)
    span = tuple(mx[i] - mn[i] for i in range(3))
    # Box(20,20,10) — build123d centers it, so span should be (20, 20, 10).
    assert abs(span[0] - 20.0) < tol, f"x span {span[0]}"
    assert abs(span[1] - 20.0) < tol, f"y span {span[1]}"
    assert abs(span[2] - 10.0) < tol, f"z span {span[2]}"


# ------------------------------------------------------------------ OBJ ------

def test_mesh_export_obj_box(tmp_path):
    box = _make_box()
    out = tmp_path / "box.obj"
    r = MeshExport().apply(box, {"path": str(out), "format": "obj"})

    assert out.exists() and out.stat().st_size > 0
    assert r.body is box  # read-only passthrough
    assert r.extras["format"] == "obj"
    assert r.extras["written_path"] == str(out)
    assert r.extras["file_size_bytes"] > 0
    assert r.extras["triangle_count"] == 12  # a meshed box = 12 triangles

    verts, faces = _parse_obj(out)
    assert len(faces) > 0
    assert len(faces) == r.extras["triangle_count"]
    assert len(verts) == r.extras["vertex_count"]
    # the 8-corner box region: parsed cloud spans full box dimensions
    _assert_box_span(verts)
    # OBJ indices are 1-based and in range
    for f in faces:
        assert len(f) == 3
        for idx in f:
            assert 1 <= idx <= len(verts)


# ------------------------------------------------------------------ PLY ------

def test_mesh_export_ply_box(tmp_path):
    box = _make_box()
    out = tmp_path / "box.ply"
    r = MeshExport().apply(box, {"path": str(out), "format": "ply"})

    assert out.exists() and out.stat().st_size > 0
    assert r.body is box
    assert r.extras["format"] == "ply"
    assert r.extras["triangle_count"] == 12

    verts, faces = _parse_ply(out)
    assert len(faces) == 12
    assert len(verts) == r.extras["vertex_count"]
    _assert_box_span(verts)
    # PLY indices are 0-based and in range
    for f in faces:
        for idx in f:
            assert 0 <= idx < len(verts)


# --------------------------------------------------------- format inference --

def test_mesh_export_infers_format_from_extension(tmp_path):
    box = _make_box()
    obj = tmp_path / "auto.obj"
    ply = tmp_path / "auto.ply"
    r1 = MeshExport().apply(box, {"path": str(obj)})
    r2 = MeshExport().apply(box, {"path": str(ply)})
    assert r1.extras["format"] == "obj"
    assert r2.extras["format"] == "ply"
    assert obj.exists() and ply.exists()


def test_mesh_export_bad_extension_raises(tmp_path):
    box = _make_box()
    with pytest.raises(ValueError):
        MeshExport().apply(box, {"path": str(tmp_path / "x.xyz")})


def test_mesh_export_none_body_raises(tmp_path):
    with pytest.raises(ValueError):
        MeshExport().apply(None, {"path": str(tmp_path / "x.obj")})


# ------------------------------------------------------------- spec sanity ---

def test_mesh_export_spec_registered():
    assert MeshExport.spec.name == "mesh_export"
    assert MeshExport.spec.category == "io"
    assert any(pc.kind == "body_present"
               for pc in MeshExport.spec.post_conditions)
