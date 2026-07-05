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


# ── V2: face pick → selection → Claude-fillets-it ────────────────────────────

def test_pick_face_resolves_and_stashes(tmp_path):
    from phone_designer.viewer_server import pick_face, _SELECTION_FILE
    ws = _make_workspace_body(tmp_path)
    # part1 is a 40x30x8 box+hole (box op is MIN-Z: top face at z=8)
    sel = pick_face(ws, "part1", [5.0, 3.0, 8.0])
    assert sel["ok"] and sel["surface_type"] == "plane"
    assert abs(sel["centroid"][2] - 8.0) < 1e-3           # picked the top face
    assert sel["selector"]["kind"] == "faces_near_point"
    # stashed for cad_get_selection
    assert (ws / _SELECTION_FILE).exists()


def test_pick_unknown_body(tmp_path):
    from phone_designer.viewer_server import pick_face
    ws = _make_workspace_body(tmp_path)
    assert pick_face(ws, "nope", [0, 0, 0])["ok"] is False


def test_faces_near_point_selector_targets_the_clicked_face():
    # the keystone: a coordinate pick resolves to exactly one face and drives a
    # fillet — durable (coordinate-based), unlike a TopExp index.
    from build123d import Align, Box
    from phone_designer.skills._resolvers import _all_faces, _face_center, resolve_faces
    from phone_designer.skills._selectors import FacesNearPointSelector
    b = Box(40, 30, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    hit = resolve_faces(b.wrapped, FacesNearPointSelector(point=(5, 3, 5), tol_mm=20, n=1))
    assert len(hit) == 1 and abs(_face_center(hit[0])[2] - 5.0) < 1e-6
    # far pick + tight tol → honest empty, not a wrong face
    assert resolve_faces(b.wrapped,
                         FacesNearPointSelector(point=(999, 999, 999), tol_mm=1, n=1)) == []


# ── F1: face-primitive-INDEX pick (preferred, exact) ─────────────────────────

def _top_face_idx(ws, body_id):
    """The _all_faces index of the +Z (top) plane — GLB primitive order == this
    order (verified keystone), so this is the traverse index the browser sends."""
    from phone_designer.skills._resolvers import _all_faces, _face_center
    from phone_designer.skills.create.import_step import ImportStep
    faces = _all_faces(ImportStep().apply(
        None, {"path": str(ws / f"{body_id}.step")}).body.wrapped)
    return max(range(len(faces)), key=lambda i: _face_center(faces[i])[2])


def test_pick_by_index_returns_the_exact_top_face(tmp_path):
    # F1 keystone: the browser sends the hit primitive's traverse index; the
    # server uses faces[idx] DIRECTLY (no coordinate conversion, no centroid
    # search). part1 is a 40x30x8 box+hole (box is MIN-Z → top plane at z=8).
    from phone_designer.viewer_server import pick_face_by_index, _SELECTION_FILE
    ws = _make_workspace_body(tmp_path)
    idx = _top_face_idx(ws, "part1")
    sel = pick_face_by_index(ws, "part1", idx)
    assert sel["ok"] and sel["face_idx"] == idx
    assert sel["surface_type"] == "plane"
    assert abs(sel["centroid"][2] - 8.0) < 1e-3            # exactly the top face
    assert sel["normal"][2] > 0.99                          # +Z outward
    assert sel["selector"]["kind"] == "faces_near_point"    # durable, body-agnostic
    assert (ws / _SELECTION_FILE).exists()


def test_pick_by_index_out_of_range_is_honest_not_a_crash(tmp_path):
    # F1/F7: an out-of-range face_idx returns {ok:False} with a range message,
    # never a silent face 0 (which would poison cad_get_selection).
    from phone_designer.viewer_server import pick_face_by_index
    ws = _make_workspace_body(tmp_path)
    sel = pick_face_by_index(ws, "part1", 9999)
    assert sel["ok"] is False and "out of range" in sel["error"]
    assert pick_face_by_index(ws, "part1", -1)["ok"] is False


def test_pick_index_and_point_agree_on_the_top_face(tmp_path):
    # both pick paths must land on the SAME face: index-pick (exact) and the
    # point fallback fed the top face's OCCT-mm centroid resolve identically.
    from phone_designer.viewer_server import pick_face, pick_face_by_index
    from phone_designer.skills._resolvers import _all_faces, _face_center
    from phone_designer.skills.create.import_step import ImportStep
    ws = _make_workspace_body(tmp_path)
    idx = _top_face_idx(ws, "part1")
    by_idx = pick_face_by_index(ws, "part1", idx)
    faces = _all_faces(ImportStep().apply(
        None, {"path": str(ws / "part1.step")}).body.wrapped)
    c = _face_center(faces[idx])
    by_pt = pick_face(ws, "part1", [c[0], c[1], c[2]])   # OCCT-mm fallback path
    assert by_idx["face_idx"] == by_pt["face_idx"] == idx


# ── F4: path traversal + containment ─────────────────────────────────────────

def test_glb_for_refuses_traversal(tmp_path):
    from phone_designer.viewer_server import _glb_for, _safe_body_id, _step_for
    ws = _make_workspace_body(tmp_path)
    # plant a STEP OUTSIDE the workspace a traversal id would reach.
    outside = tmp_path / "PWNED_probe.step"
    outside.write_bytes((ws / "part1.step").read_bytes())
    for evil in ("../PWNED_probe", "..\\PWNED_probe", "../../etc/passwd",
                 "a/b", "a\\b"):
        assert _safe_body_id(evil) is False
        assert _glb_for(ws, evil) is None          # refused, no GLB written
        assert _step_for(ws, evil) is None
    assert not (ws / "..").joinpath("PWNED_probe.glb").exists()


def test_pick_refuses_traversal_body_id(tmp_path):
    from phone_designer.viewer_server import pick_face, pick_face_by_index
    ws = _make_workspace_body(tmp_path)
    assert pick_face(ws, "../../secret", [0, 0, 0])["ok"] is False
    assert pick_face_by_index(ws, "../../secret", 0)["ok"] is False


# ── F6: GLB cache invalidation on same-mtime overwrite ───────────────────────

def test_glb_rebuilt_when_step_overwritten_same_mtime(tmp_path):
    # F6: the "body is immutable" assumption is false — cad_generate/export can
    # OVERWRITE <id>.step. A different-SIZE overwrite (even at the SAME mtime)
    # must invalidate the cached GLB, not serve the stale one.
    import os
    from phone_designer.viewer_server import _glb_for
    ws = _make_workspace_body(tmp_path)
    glb1 = _glb_for(ws, "part1")
    size1 = glb1.stat().st_size
    step = ws / "part1.step"
    st = step.stat()
    # overwrite part1.step with DIFFERENT geometry (bigger box → bigger STEP)...
    from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
    r = GenerateFromSpec().apply(None, {"spec": [
        {"op": "box", "args": {"length_mm": 80, "width_mm": 60, "height_mm": 20}},
        {"op": "hole", "args": {"position": [0, 0, 20], "diameter_mm": 10,
                                "depth_mm": 20, "direction": "-Z"}},
        {"op": "hole", "args": {"position": [20, 10, 20], "diameter_mm": 6,
                                "depth_mm": 20, "direction": "-Z"}}]})
    assert _write_step(r.body, str(step))
    # ...and force the SAME mtime the old GLB was built from (coarse-granularity
    # clocks make this a real collision).
    os.utime(step, (st.st_atime, st.st_mtime))
    glb2 = _glb_for(ws, "part1")
    assert glb2 is not None
    # the new geometry has more faces → a genuinely different (larger) GLB.
    assert glb2.stat().st_size != size1


def test_glb_not_rebuilt_when_step_truly_unchanged(tmp_path):
    # the flip side of F6: an untouched STEP must NOT trigger a rebuild (the
    # sidecar records size+mtime; identical ⇒ cache hit).
    from phone_designer.viewer_server import _glb_for
    ws = _make_workspace_body(tmp_path)
    a = _glb_for(ws, "part1")
    m0 = a.stat().st_mtime_ns
    b = _glb_for(ws, "part1")
    assert b == a and b.stat().st_mtime_ns == m0


# ── F7: /pick input validation → honest error (not a 500, not a silent NaN) ──

def test_pick_rejects_nan_point(tmp_path):
    # F7: a NaN point must be REFUSED, never silently resolved to face 0 (which
    # would poison the stash cad_get_selection reads).
    from phone_designer.viewer_server import pick_face, _SELECTION_FILE
    ws = _make_workspace_body(tmp_path)
    stash = ws / _SELECTION_FILE
    before = stash.read_text(encoding="utf-8") if stash.exists() else None
    sel = pick_face(ws, "part1", [float("nan"), 0.0, 0.0])
    assert sel["ok"] is False and "non-finite" in sel["error"]
    # stash was NOT overwritten by the bad pick
    after = stash.read_text(encoding="utf-8") if stash.exists() else None
    assert after == before


def test_pick_request_error_shapes():
    # F7 handler-level: malformed /pick bodies map to a 400 error string; a
    # well-shaped request returns None (→ proceeds to a 200 result).
    from phone_designer.viewer_server import _Handler
    e = _Handler._pick_request_error
    assert e({}) is not None                                   # no body_id
    assert e({"body_id": "p"}) is not None                     # neither idx/point
    assert e({"body_id": "p", "point": [1, 2]}) is not None    # short point
    assert e({"body_id": "p", "point": [1, "x", 3]}) is not None
    assert e({"body_id": "p", "point": [float("inf"), 0, 0]}) is not None
    assert e({"body_id": "p", "face_idx": "3"}) is not None    # idx not int
    assert e({"body_id": "p", "face_idx": True}) is not None   # bool != int
    assert e({"body_id": "p", "face_idx": 3}) is None          # OK
    assert e({"body_id": "p", "point": [1.0, 2.0, 3.0]}) is None  # OK


# ── F3: workspace coupling (pointer first; exclude the BodyStore snapshot dir) ─

def test_workspace_excludes_bodystore_snapshot_dir(tmp_path, monkeypatch):
    # F3(c): _workspace() must NOT bind to a pd_mcp_bodies_* dir (the BodyStore
    # snapshot dir), even though it is created mid-session and looks 'newest'.
    import phone_designer.viewer_server as vs
    monkeypatch.delenv("PHONE_DESIGNER_MCP_WORKSPACE", raising=False)
    fake_tmp = tmp_path / "tmp"
    fake_tmp.mkdir()
    real_ws = fake_tmp / "pd_mcp_abcd1234"
    real_ws.mkdir()
    bodies = fake_tmp / "pd_mcp_bodies_zzzz9999"   # created LATER → 'newest'
    bodies.mkdir()
    monkeypatch.setattr(vs.tempfile, "gettempdir", lambda: str(fake_tmp))
    got = vs._workspace()
    assert got == real_ws           # not the bodies_ snapshot dir


def test_workspace_honors_pointer_file(tmp_path, monkeypatch):
    # F3(b): the MCP-written pointer (pd_mcp_current.txt) wins over the newest
    # pd_mcp_* dir so the two processes agree even without the env var.
    import phone_designer.viewer_server as vs
    monkeypatch.delenv("PHONE_DESIGNER_MCP_WORKSPACE", raising=False)
    fake_tmp = tmp_path / "tmp"
    fake_tmp.mkdir()
    (fake_tmp / "pd_mcp_newer0000").mkdir()          # a newer decoy
    target = tmp_path / "the_real_ws"
    target.mkdir()
    (fake_tmp / vs._WORKSPACE_POINTER).write_text(str(target), encoding="utf-8")
    monkeypatch.setattr(vs.tempfile, "gettempdir", lambda: str(fake_tmp))
    assert vs._workspace() == target


# ── F5: server is threaded + rebinds immediately ─────────────────────────────

def test_server_class_is_threaded_and_reuses_address():
    import socketserver
    from phone_designer.viewer_server import _Server
    assert issubclass(_Server, socketserver.ThreadingMixIn)
    assert _Server.allow_reuse_address is True
    assert _Server.daemon_threads is True


# ── V3: GET /scene (feature catalog) + GET /section (cut-plane view) ──────────

def _make_box_2holes_ws(tmp_path):
    """A 40x30x8 box with TWO -Z holes; return (ws, body_id='part2'). Distinct
    from _make_workspace_body (one hole) so /scene has 2 holes to assert on."""
    from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
    r = GenerateFromSpec().apply(None, {"spec": [
        {"op": "box", "args": {"length_mm": 40, "width_mm": 30, "height_mm": 8}},
        {"op": "hole", "args": {"position": [10, 0, 8], "diameter_mm": 6,
                                "depth_mm": 8, "direction": "-Z"}},
        {"op": "hole", "args": {"position": [-10, 0, 8], "diameter_mm": 6,
                                "depth_mm": 8, "direction": "-Z"}}]})
    ws = tmp_path / "ws"
    ws.mkdir()
    assert _write_step(r.body, str(ws / "part2.step"))
    return ws


def test_scene_returns_two_holes_with_inrange_face_indices(tmp_path):
    # VERIFY: /scene on a box+2holes returns 2 holes each with a nonempty
    # face_indices IN RANGE 0..n_faces-1 (those ARE the GLB primitive indices
    # the browser highlights — the 1:1 keystone).
    from phone_designer.viewer_server import _scene_for
    ws = _make_box_2holes_ws(tmp_path)
    scene = _scene_for(ws, "part2")
    assert scene is not None and scene["ok"] is True
    n = scene["n_faces"]
    assert n == 8                                   # box(6) + 2 hole bores
    holes = scene["features"]["holes"]
    assert len(holes) == 2
    for h in holes:
        fis = h["face_indices"]
        assert fis and all(0 <= i < n for i in fis)  # nonempty + in-range
        assert h["diameters_mm"] and h["depth_mm"] > 0
    # bbox present (initial_bbox_mm from the catalog)
    bb = scene["bbox_mm"]
    assert bb is not None and len(bb) == 6
    # strict-JSON-safe: round-trips through json without NaN/inf tokens
    import json
    assert json.loads(json.dumps(scene)) == scene


def test_scene_cached_next_to_step_and_reused(tmp_path):
    # /scene caches the JSON next to the STEP (<id>.scene.json) so the SLOW
    # extract_feature_catalog runs once; a second call reads the sidecar.
    from phone_designer.viewer_server import _scene_for
    ws = _make_box_2holes_ws(tmp_path)
    a = _scene_for(ws, "part2")
    sidecar = ws / "part2.scene.json"
    assert sidecar.exists()
    m0 = sidecar.stat().st_mtime_ns
    b = _scene_for(ws, "part2")
    assert b == a and sidecar.stat().st_mtime_ns == m0   # not rebuilt


def test_scene_cache_invalidates_on_step_overwrite_same_mtime(tmp_path):
    # F6 parity: overwriting the STEP with different geometry (even at the same
    # mtime) must rebuild the scene, not serve the stale catalog.
    import os
    from phone_designer.viewer_server import _scene_for
    ws = _make_box_2holes_ws(tmp_path)
    s1 = _scene_for(ws, "part2")
    assert len(s1["features"]["holes"]) == 2
    step = ws / "part2.step"
    st = step.stat()
    from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
    r = GenerateFromSpec().apply(None, {"spec": [
        {"op": "box", "args": {"length_mm": 40, "width_mm": 30,
                               "height_mm": 8}}]})   # NO holes now
    assert _write_step(r.body, str(step))
    os.utime(step, (st.st_atime, st.st_mtime))       # force same mtime
    s2 = _scene_for(ws, "part2")
    assert len(s2["features"]["holes"]) == 0         # rebuilt from new geometry


def test_scene_unknown_body_is_none(tmp_path):
    from phone_designer.viewer_server import _scene_for
    ws = _make_box_2holes_ws(tmp_path)
    assert _scene_for(ws, "nope") is None
    assert _scene_for(ws, "../../secret") is None    # F4 traversal refused


def test_section_returns_cut_glb_smaller_or_equal_with_primitives(tmp_path):
    # VERIFY: /section axis=z pos=0.5 returns a valid GLB with primitives>0 and
    # size <= the full body GLB (it is a CUT half). NEVER a new body_id.
    import pygltflib
    from phone_designer.viewer_server import _section_for, _glb_for
    ws = _make_box_2holes_ws(tmp_path)
    res = _section_for(ws, "part2", "z", 0.5)
    assert res is not None and res["ok"] is True and res["cut"] is True
    glb = res["glb"]
    assert glb.exists() and glb.name == "part2.sec.glb"
    g = pygltflib.GLTF2().load(str(glb))
    prims = sum(len(m.primitives) for m in g.meshes)
    assert prims > 0
    full = _glb_for(ws, "part2")
    assert glb.stat().st_size <= full.stat().st_size
    # section must NOT have minted a new stem (no new .step in the workspace).
    assert sorted(p.name for p in ws.glob("*.step")) == ["part2.step"]


def test_section_cached_by_axis_and_quantized_pos(tmp_path):
    # cache by (body_id, axis, quantized pos): same axis+pos reuses; a different
    # axis rebuilds. Slider drags within a 1/100th bucket stay a cache hit.
    from phone_designer.viewer_server import _section_for
    ws = _make_box_2holes_ws(tmp_path)
    a = _section_for(ws, "part2", "z", 0.5)
    m0 = a["glb"].stat().st_mtime_ns
    b = _section_for(ws, "part2", "z", 0.5)          # same → cache hit
    assert b["glb"].stat().st_mtime_ns == m0
    c = _section_for(ws, "part2", "x", 0.5)          # diff axis → rebuild
    assert c["glb"].stat().st_mtime_ns != m0
    assert sorted(p.name for p in ws.glob("*.step")) == ["part2.step"]


def test_section_falls_back_to_full_body_when_plane_misses(tmp_path):
    # honest fallback: a plane at pos=0.0 leaves NO negative pieces (split_body
    # → fm.split_no_cut). /section returns the FULL body GLB with cut=False and
    # a note instead of a 500. The user still sees the (uncut) body.
    from phone_designer.viewer_server import _section_for
    ws = _make_box_2holes_ws(tmp_path)
    res = _section_for(ws, "part2", "z", 0.0)
    assert res["ok"] is True and res["cut"] is False
    assert "full body" in res["note"] and res["glb"].exists()


def test_section_unknown_body_is_none(tmp_path):
    from phone_designer.viewer_server import _section_for
    ws = _make_box_2holes_ws(tmp_path)
    assert _section_for(ws, "nope", "z", 0.5) is None
    assert _section_for(ws, "../../secret", "z", 0.5) is None   # F4


def test_section_handler_rejects_bad_axis_and_pos(tmp_path):
    # /section validates axis ∈ {x,y,z} and pos ∈ [0,1] → 400 (not 500). Drive
    # the handler's validation branch directly with a tiny fake request.
    from phone_designer.viewer_server import _Handler, _AXIS_INDEX

    class _Fake(_Handler):
        def __init__(self):            # bypass BaseHTTPRequestHandler __init__
            self.command = "GET"
            self._status = None
            self._body = None

        def _send(self, code, body, ctype):
            self._status = code
            self._body = body

    assert "x" in _AXIS_INDEX and "y" in _AXIS_INDEX and "z" in _AXIS_INDEX
    ws = _make_box_2holes_ws(tmp_path)
    h = _Fake()
    h.ws = ws
    # bad axis → 400
    h._do_section({"body_id": ["part2"], "axis": ["w"], "pos": ["0.5"]})
    assert h._status == 400 and b"axis" in h._body
    # pos out of range → 400
    h._do_section({"body_id": ["part2"], "axis": ["z"], "pos": ["1.5"]})
    assert h._status == 400
    # non-numeric pos → 400
    h._do_section({"body_id": ["part2"], "axis": ["z"], "pos": ["abc"]})
    assert h._status == 400
    # missing/unsafe body_id → 400
    h._do_section({"body_id": ["../../x"], "axis": ["z"], "pos": ["0.5"]})
    assert h._status == 400
    # a VALID request streams a GLB (200-path: writes via the base _send override
    # is bypassed by the custom header write, so assert the model isn't a 400).
    hg = _Fake()
    hg.ws = ws
    captured = {}

    def _cap_response(code):
        captured["code"] = code

    hg.send_response = _cap_response
    hg.send_header = lambda *a, **k: None
    hg.end_headers = lambda: None

    class _Sink:
        def write(self, b):
            captured["wrote"] = len(b)

    hg.wfile = _Sink()
    hg._do_section({"body_id": ["part2"], "axis": ["z"], "pos": ["0.5"]})
    assert captured.get("code") == 200 and captured.get("wrote", 0) > 0


def test_scene_handler_rejects_missing_body_id(tmp_path):
    import json
    from phone_designer.viewer_server import _Handler
    ws = _make_box_2holes_ws(tmp_path)

    class _Fake(_Handler):
        def __init__(self):
            self.command = "GET"
            self._status = None
            self._body = None

        def _send(self, code, body, ctype):
            self._status = code
            self._body = body

    h = _Fake()
    h.ws = ws
    h._do_scene({})                       # no body_id → 400
    assert h._status == 400
    h._do_scene({"body_id": ["nope"]})    # unknown → 404
    assert h._status == 404
    h._do_scene({"body_id": ["part2"]})   # OK → 200 with the scene JSON
    assert h._status == 200
    payload = json.loads(h._body.decode())
    assert payload["ok"] and len(payload["features"]["holes"]) == 2
