"""gltf_export skill 단위 테스트.

Self-registers by importing the module at the top (no shared-file edit needed).
Verifies GLB (binary, self-contained) and glTF (JSON + .bin sidecar) exports of
a Box(20,20,10), that files exist with size > 0, and that read-only semantics
hold (body returned unchanged). Also pins the glTF 2.0 unit contract: vertices
are written in METRES (mm × 0.001) with a -90°-about-+X node rotation for +Y
up, and zero-triangle bodies are refused with fm.tessellation_failed.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from build123d import Box

# import 으로 skill 등록 발동
from phone_designer.skills.io.gltf_export import GltfExport


def _box_body():
    return Box(20, 20, 10)


def _gltf_json(path: Path) -> dict:
    """Extract the glTF JSON dict from a .glb (chunk 0) or .gltf (plain JSON)."""
    data = path.read_bytes()
    if data[:4] == b"glTF":
        version, _total = struct.unpack("<II", data[4:12])
        assert version == 2
        length, ctype = struct.unpack("<I4s", data[12:20])
        assert ctype == b"JSON"
        return json.loads(data[20:20 + length].decode("utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def _edge_only_compound():
    """A compound holding a single edge — zero faces, zero triangles."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.TopoDS import TopoDS_Compound
    from OCP.gp import gp_Pnt

    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    builder.Add(comp, BRepBuilderAPI_MakeEdge(
        gp_Pnt(0, 0, 0), gp_Pnt(1, 0, 0)).Edge())
    return comp


def test_glb_export_binary(tmp_path: Path):
    out = tmp_path / "box.glb"
    inst = GltfExport()
    body = _box_body()
    result = inst.apply(body, {"path": str(out)})

    assert out.exists(), "GLB file not written"
    assert out.stat().st_size > 0, "GLB file is empty"

    extras = result.extras
    assert extras["binary"] is True
    assert extras["format"] == "glb"
    assert extras["written_path"] == str(out)
    assert extras["triangle_count"] > 0
    assert extras["file_size_bytes"] == out.stat().st_size
    # GLB is self-contained — no .bin sidecar.
    assert extras["bin_sidecar_path"] is None

    # GLB magic header — first 4 bytes are ASCII "glTF".
    with out.open("rb") as fh:
        assert fh.read(4) == b"glTF", "GLB magic header missing"

    # Read-only: body returned unchanged (same object).
    assert result.body is body


def test_gltf_export_text_with_bin_sidecar(tmp_path: Path):
    out = tmp_path / "box.gltf"
    inst = GltfExport()
    result = inst.apply(_box_body(), {"path": str(out)})

    assert out.exists() and out.stat().st_size > 0
    extras = result.extras
    assert extras["binary"] is False
    assert extras["format"] == "gltf"

    # glTF text mode emits a .bin buffer sidecar.
    sidecar = out.with_suffix(".bin")
    assert sidecar.exists() and sidecar.stat().st_size > 0
    assert extras["bin_sidecar_path"] == str(sidecar)

    # glTF JSON starts with '{'.
    assert out.read_text(encoding="utf-8").lstrip().startswith("{")


def test_binary_flag_overrides_extension(tmp_path: Path):
    # Force binary GLB content into a .gltf-named path.
    out = tmp_path / "forced.gltf"
    inst = GltfExport()
    result = inst.apply(_box_body(), {"path": str(out), "binary": True})

    assert out.exists() and out.stat().st_size > 0
    assert result.extras["binary"] is True
    with out.open("rb") as fh:
        assert fh.read(4) == b"glTF", "forced-binary file lacks GLB magic"


def test_glb_vertices_in_metres_with_y_up_rotation(tmp_path: Path):
    """glTF 2.0 §3.4: 1 unit = 1 metre, +Y up. A 20×20×10 mm box must land at
    a 0.02×0.02×0.01 m POSITION accessor span (NOT 20/10), with a -90°-about-+X
    node rotation quaternion mapping our Z-up geometry to the spec's +Y up."""
    out = tmp_path / "units.glb"
    result = GltfExport().apply(_box_body(), {"path": str(out)})

    gltf = _gltf_json(out)
    pos_accessors = [
        gltf["accessors"][prim["attributes"]["POSITION"]]
        for mesh in gltf.get("meshes", [])
        for prim in mesh.get("primitives", [])
        if "POSITION" in prim.get("attributes", {})
    ]
    assert pos_accessors, "no POSITION accessor in the written GLB"
    # OCCT emits one primitive per face, so union the accessor bounds.
    mn = [min(a["min"][i] for a in pos_accessors) for i in range(3)]
    mx = [max(a["max"][i] for a in pos_accessors) for i in range(3)]
    span = sorted(mx[i] - mn[i] for i in range(3))
    # metre-scaled: 20 mm → 0.02 m and 10 mm → 0.01 m, not 20/10.
    assert span == pytest.approx([0.01, 0.02, 0.02], rel=1e-6), f"span {span}"

    # +Y-up: some node carries the -90° about +X rotation (glTF xyzw order;
    # q and -q are the same rotation, so normalize the sign via w).
    s = 0.7071067811865476
    quats = [n["rotation"] for n in gltf.get("nodes", []) if "rotation" in n]
    assert quats, "no node carries the Z-up → +Y-up rotation quaternion"
    qx, qy, qz, qw = quats[0]
    if qw < 0:
        qx, qy, qz, qw = -qx, -qy, -qz, -qw
    assert qx == pytest.approx(-s, abs=1e-6)
    assert qy == pytest.approx(0.0, abs=1e-6)
    assert qz == pytest.approx(0.0, abs=1e-6)
    assert qw == pytest.approx(s, abs=1e-6)

    # extras record the applied convention honestly.
    assert result.extras["units"] == "m"
    assert result.extras["up_axis"] == "+Y"
    assert result.extras["unit_scale_applied"] == 0.001


def test_gltf_text_vertices_in_metres_too(tmp_path: Path):
    out = tmp_path / "units.gltf"
    GltfExport().apply(_box_body(), {"path": str(out)})
    gltf = _gltf_json(out)
    pos_accessors = [
        gltf["accessors"][prim["attributes"]["POSITION"]]
        for mesh in gltf.get("meshes", [])
        for prim in mesh.get("primitives", [])
        if "POSITION" in prim.get("attributes", {})
    ]
    assert pos_accessors
    spans = [max(a["max"][i] - a["min"][i] for i in range(3))
             for a in pos_accessors]
    assert max(spans) == pytest.approx(0.02, rel=1e-6)


def test_zero_triangle_body_refused(tmp_path: Path):
    """An edge-only compound tessellates to 0 triangles — refuse instead of
    silently writing an empty glTF scene (fm.tessellation_failed)."""
    out = tmp_path / "empty.glb"
    with pytest.raises(ValueError, match=r"fm\.tessellation_failed"):
        GltfExport().apply(_edge_only_compound(), {"path": str(out)})


def test_rejects_none_body():
    inst = GltfExport()
    with pytest.raises(Exception):
        inst.apply(None, {"path": "unused.glb"})


def test_spec_metadata():
    spec = GltfExport.spec
    assert spec.name == "gltf_export"
    assert spec.category == "io"
    assert spec.level == "atomic"
