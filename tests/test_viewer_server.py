"""viewer_server V1 — the web CAD viewer bridge (rotate + view in the browser).

Pins the dependency-free bridge: it lists workspace bodies, builds a GLB from a
body_id's STEP (reusing the verified GltfExport), caches it (body is immutable),
serves the static three.js viewer, and 404s an unknown body. The GLB carries ONE
primitive per OCCT face — the latent per-face-pick path for V2 — so a face count
pin guards that too.

No live socket needed: exercises the handler's core functions directly.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")


def _write_step(body, path):
    """Local STEP writer (avoids importing mcp_server, which needs FastMCP)."""
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    shape = body.wrapped if hasattr(body, "wrapped") else body
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    w.Write(path)
    return os.path.exists(path) and os.path.getsize(path) > 0


def _make_workspace_body(tmp_path):
    """Generate a bracket and write its STEP into a workspace dir; return (ws, id)."""
    from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
    spec = [
        {"op": "box", "args": {"length_mm": 40, "width_mm": 30, "height_mm": 8}},
        {"op": "hole", "args": {"position": [0, 0, 8], "diameter_mm": 6,
                                "depth_mm": 8, "direction": "-Z"}},
    ]
    r = GenerateFromSpec().apply(None, {"spec": spec})
    ws = tmp_path / "ws"
    ws.mkdir()
    assert _write_step(r.body, str(ws / "part1.step"))
    return ws


def test_list_bodies(tmp_path):
    from phone_designer.viewer_server import _list_bodies
    ws = _make_workspace_body(tmp_path)
    bodies = _list_bodies(ws)
    assert [b["body_id"] for b in bodies] == ["part1"]
    assert bodies[0]["step"] == "part1.step" and bodies[0]["size_kb"] > 0


def test_glb_build_and_per_face_primitives(tmp_path):
    import pygltflib
    from phone_designer.viewer_server import _glb_for
    ws = _make_workspace_body(tmp_path)
    glb = _glb_for(ws, "part1")
    assert glb is not None and glb.exists() and glb.stat().st_size > 1000
    g = pygltflib.GLTF2().load(str(glb))
    prims = sum(len(m.primitives) for m in g.meshes)
    # box (6) + hole cylinder wall + the two annular faces the bore leaves → the
    # GLB emits ONE primitive per OCCT face (the pickable-face contract for V2).
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from phone_designer.skills.create.import_step import ImportStep
    body = ImportStep().apply(None, {"path": str(ws / "part1.step")}).body
    n = 0
    ex = TopExp_Explorer(body.wrapped, TopAbs_FACE)
    while ex.More():
        n += 1
        ex.Next()
    assert prims == n, f"GLB primitives {prims} must equal OCCT faces {n}"


def test_glb_cached_when_step_unchanged(tmp_path):
    from phone_designer.viewer_server import _glb_for
    ws = _make_workspace_body(tmp_path)
    a = _glb_for(ws, "part1")
    mtime_a = a.stat().st_mtime
    b = _glb_for(ws, "part1")
    assert b == a and b.stat().st_mtime == mtime_a   # not rebuilt


def test_unknown_body_returns_none(tmp_path):
    from phone_designer.viewer_server import _glb_for
    ws = _make_workspace_body(tmp_path)
    assert _glb_for(ws, "does_not_exist") is None


def test_static_index_present():
    from phone_designer.viewer_server import _STATIC
    idx = _STATIC / "index.html"
    assert idx.is_file()
    html = idx.read_text(encoding="utf-8")
    assert "OrbitControls" in html and "/model/" in html   # rotate + glb load wired


def test_cad_export_glb_format():
    # cad_export gained a 'glb' format (the viewer feed). Needs the mcp package
    # (FastMCP), absent on some CI lanes — skip cleanly there.
    M = pytest.importorskip(
        "phone_designer.mcp_server",
        reason="mcp (FastMCP) not installed in this environment")
    g = M.cad_generate([{"op": "box", "args": {
        "length_mm": 20, "width_mm": 20, "height_mm": 20}}], name="glbtest")
    assert g["ok"]
    e = M.cad_export(body_id=g["body_id"], formats=["glb"])
    assert e["ok"] and "glb" in e["files"] and os.path.exists(e["files"]["glb"])


# guard: the whole module imports even where the mcp package is absent — but
# viewer_server itself has no mcp dep, so it must import standalone.
def test_viewer_server_imports_without_mcp_package():
    import importlib
    import phone_designer.viewer_server as vs
    importlib.reload(vs)
    assert hasattr(vs, "serve") and hasattr(vs, "_glb_for")
