"""gltf_export skill 단위 테스트.

Self-registers by importing the module at the top (no shared-file edit needed).
Verifies GLB (binary, self-contained) and glTF (JSON + .bin sidecar) exports of
a Box(20,20,10), that files exist with size > 0, and that read-only semantics
hold (body returned unchanged).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from build123d import Box

# import 으로 skill 등록 발동
from phone_designer.skills.io.gltf_export import GltfExport


def _box_body():
    return Box(20, 20, 10)


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


def test_rejects_none_body():
    inst = GltfExport()
    with pytest.raises(Exception):
        inst.apply(None, {"path": "unused.glb"})


def test_spec_metadata():
    spec = GltfExport.spec
    assert spec.name == "gltf_export"
    assert spec.category == "io"
    assert spec.level == "atomic"
