"""viewer_server — V1 web CAD viewer bridge (rotate + view a body in the browser).

A SEPARATE process from the stdio MCP server (mcp_server.py) — Claude Code owns
that stdio pipe, so the browser cannot share it. This bridge is a dependency-free
``http.server`` (no FastAPI/uvicorn needed for V1): it serves the static three.js
viewer + a body's GLB, resolving body_ids the SAME way the MCP tools do — through
the SHARED workspace (PHONE_DESIGNER_MCP_WORKSPACE): a body_id's STEP on disk →
GltfExport → GLB. Bodies are immutable (body_id lineage), so a GLB is cache-forever.

Architecture note (plans/WEB_VIEWER_PLAN.md): Claude stays the modelling brain;
this viewer only rotates + (later) picks. Per-face GLB primitives (verified: a
box+2holes = 8 OCCT faces = 8 GLB primitives) make V2 face-picking possible with
NO browser WASM kernel.

V2 adds face PICK: the viewer POSTs a raycast 3D point to /pick, the server
resolves the nearest OCCT face (faces_near_point) and returns {face_idx, centroid,
normal, surface_type} AND stashes it as the "current selection" in the workspace so
Claude can read it via the cad_get_selection MCP tool → compose a cad_modify spec
targeting exactly that face. Claude stays the modelling brain; the browser only
rotates + picks.

Run:  python -m phone_designer.viewer_server [--port 8765] [--workspace DIR]
Then open http://127.0.0.1:8765/ — it lists bodies (STEP files in the workspace)
and shows the chosen one in a rotate/zoom viewer.
"""
from __future__ import annotations

import argparse
import http.server
import json
import math
import os
import socketserver
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

_STATIC = Path(__file__).with_name("viewer_static")
_SELECTION_FILE = "_viewer_selection.json"   # in the workspace; read by cad_get_selection
# F3: the MCP server drops its live _WORKSPACE path here at import so the viewer
# (a separate process) binds the SAME dir even when the env var is unset.
_WORKSPACE_POINTER = "pd_mcp_current.txt"


def _safe_body_id(body_id: str) -> bool:
    """F4: reject a body_id that could escape the workspace via path traversal.
    body_ids are STEP stems — a flat name, never a path. Anything with a
    separator or a parent ref (``/``, ``\\``, ``..``) is refused."""
    if not body_id or not isinstance(body_id, str):
        return False
    if "/" in body_id or "\\" in body_id or ".." in body_id:
        return False
    return True


def _step_for(ws: Path, body_id: str) -> Path | None:
    """Resolve body_id → its STEP path, REFUSING traversal (F4). Returns None
    if the id is unsafe or the resolved path escapes the workspace / is absent."""
    if not _safe_body_id(body_id):
        return None
    step = ws / f"{body_id}.step"
    try:
        ws_r = ws.resolve()
        step_r = step.resolve()
    except (OSError, RuntimeError):
        return None
    # containment: the resolved STEP must live directly inside the workspace.
    if step_r.parent != ws_r:
        return None
    return step if step.exists() else None


def _load_faces(step: Path):
    """Import a STEP and return its ordered OCCT face list (== GLB primitive
    order, 1:1 — verified keystone). Shared by both pick paths."""
    os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")
    from phone_designer.skills._resolvers import _all_faces
    from phone_designer.skills.create.import_step import ImportStep
    body = ImportStep().apply(None, {"path": str(step)}).body
    shape = body.wrapped if hasattr(body, "wrapped") else body
    return _all_faces(shape)


def _face_selection(ws: Path, body_id: str, face_idx: int, faces, *,
                    distance_mm: float | None = None) -> dict:
    """Build + STASH the selection dict for one chosen OCCT face. The selector is
    the DURABLE faces_near_point on the face's own centroid (survives modify /
    STEP round-trips) — body-agnostic, so Claude drops it into a cad_modify spec
    targeting its OWN mcp body_id (see cad_get_selection / F2)."""
    from phone_designer.skills._resolvers import (
        _face_area,
        _face_center,
        _face_normal_at_center,
    )
    f = faces[face_idx]
    c = _face_center(f)
    n = _face_normal_at_center(f)
    try:
        from phone_designer.skills.inspect.classify_holes import _surface_kind
        stype = _surface_kind(f)
    except Exception:  # noqa: BLE001
        stype = "unknown"
    sel = {
        "ok": True, "body_id": body_id, "face_idx": int(face_idx),
        "centroid": [round(v, 4) for v in c],
        "normal": [round(v, 6) for v in n],
        "surface_type": stype, "area_mm2": round(_face_area(f), 3),
    }
    if distance_mm is not None:
        sel["distance_mm"] = round(distance_mm, 4)
    # durable selector Claude can drop straight into a cad_modify spec.
    sel["selector"] = {"kind": "faces_near_point",
                       "point": sel["centroid"], "tol_mm": 1.0}
    (ws / _SELECTION_FILE).write_text(json.dumps(sel, indent=2), encoding="utf-8")
    return sel


def pick_face_by_index(ws: Path, body_id: str, face_idx: int) -> dict:
    """F1 (PREFERRED, EXACT): the browser already knows the exact hit primitive
    — it sends that primitive's traverse index. Because the GLB emits ONE
    primitive per OCCT face IN _all_faces ORDER (1:1 — verified keystone), the
    server uses ``faces[face_idx]`` DIRECTLY: no coordinate conversion, no
    centroid search, no mispick. Returns {ok, body_id, face_idx, centroid,
    normal, surface_type, area_mm2, selector} + stashes it for cad_get_selection.
    """
    step = _step_for(ws, body_id)
    if step is None:
        return {"ok": False, "error": f"unknown body_id '{body_id}'"}
    try:
        fi = int(face_idx)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"face_idx not an integer: {face_idx!r}"}
    faces = _load_faces(step)
    if fi < 0 or fi >= len(faces):
        return {"ok": False,
                "error": f"face_idx {fi} out of range (0..{len(faces) - 1})"}
    return _face_selection(ws, body_id, fi, faces)


def pick_face(ws: Path, body_id: str, point) -> dict:
    """FALLBACK (point-based): resolve the OCCT face nearest a 3D world `point`
    on body_id's STEP and STASH it as the workspace current-selection. F1 makes
    /pick prefer {face_idx} (exact); this remains for callers that only have a
    coordinate. Point components must be finite (F7)."""
    step = _step_for(ws, body_id)
    if step is None:
        return {"ok": False, "error": f"unknown body_id '{body_id}'"}
    try:
        px, py, pz = float(point[0]), float(point[1]), float(point[2])
    except (TypeError, ValueError, IndexError):
        return {"ok": False, "error": f"point must be 3 numbers, got {point!r}"}
    if not all(math.isfinite(v) for v in (px, py, pz)):
        return {"ok": False, "error": "point has non-finite (NaN/inf) component"}
    from phone_designer.skills._resolvers import _face_center
    faces = _load_faces(step)
    best_i, best_d2 = -1, None
    for i, f in enumerate(faces):
        cx, cy, cz = _face_center(f)
        d2 = (cx - px) ** 2 + (cy - py) ** 2 + (cz - pz) ** 2
        if best_d2 is None or d2 < best_d2:
            best_d2, best_i = d2, i
    if best_i < 0:
        return {"ok": False, "error": "no faces"}
    return _face_selection(ws, body_id, best_i, faces,
                           distance_mm=best_d2 ** 0.5)


def _workspace() -> Path:
    """Resolve the workspace the MCP server is writing bodies into. Priority:
    (1) PHONE_DESIGNER_MCP_WORKSPACE env; (2) F3 pointer file the MCP wrote at
    import (gettempdir()/pd_mcp_current.txt); (3) newest pd_mcp_* temp dir —
    EXCLUDING pd_mcp_bodies_* (the BodyStore snapshot dir, not a real workspace).
    Warns to stderr if >1 candidate remains (ambiguous coupling)."""
    d = os.environ.get("PHONE_DESIGNER_MCP_WORKSPACE")
    if d:
        return Path(d)
    tmp = Path(tempfile.gettempdir())
    # (2) the self-healing pointer the MCP server drops at import time.
    pointer = tmp / _WORKSPACE_POINTER
    if pointer.exists():
        try:
            p = Path(pointer.read_text(encoding="utf-8").strip())
            if p.is_dir():
                return p
        except OSError:
            pass
    # (3) newest pd_mcp_* dir, but NOT the pd_mcp_bodies_* snapshot dir (F3 glob
    # pollution: the BodyStore snapshot dir is created mid-session and would
    # otherwise win as 'newest', binding the viewer to the wrong directory).
    cands = [p for p in tmp.glob("pd_mcp_*")
             if p.is_dir() and not p.name.startswith("pd_mcp_bodies_")]
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if len(cands) > 1:
        print(f"WARNING: {len(cands)} candidate MCP workspaces in {tmp}; "
              f"binding to newest ({cands[0].name}). Set "
              f"PHONE_DESIGNER_MCP_WORKSPACE to pin.", file=sys.stderr)
    return cands[0] if cands else tmp


def _list_bodies(ws: Path) -> list[dict]:
    """Every STEP in the workspace is a viewable body (body_id = stem)."""
    out = []
    for step in sorted(ws.glob("*.step"), key=lambda p: p.stat().st_mtime,
                       reverse=True):
        out.append({"body_id": step.stem, "step": step.name,
                    "mtime": step.stat().st_mtime,
                    "size_kb": round(step.stat().st_size / 1024, 1)})
    return out


# F6: per-body build lock so two concurrent /model requests don't race on the
# same GLB (F5 makes the server multithreaded). A cache sidecar records the
# (size,mtime) the GLB was built from so a same-mtime OVERWRITE still invalidates.
_GLB_LOCKS: dict[str, threading.Lock] = {}
_GLB_LOCKS_GUARD = threading.Lock()


def _glb_lock(key: str) -> "threading.Lock":
    with _GLB_LOCKS_GUARD:
        lk = _GLB_LOCKS.get(key)
        if lk is None:
            lk = _GLB_LOCKS[key] = threading.Lock()
        return lk


def _glb_for(ws: Path, body_id: str) -> Path | None:
    """Return the GLB for a body_id, building it from the STEP when stale (F6).

    The old ``body is immutable`` assumption is FALSE: cad_generate/cad_export
    can OVERWRITE ``<id>.step`` with new geometry. So we invalidate on the STEP's
    (size, mtime) recorded in a ``.glb.src`` sidecar — a strict change (different
    size OR a strictly newer mtime) rebuilds, and even a same-mtime overwrite of
    a DIFFERENT size is caught. Build is atomic (temp + os.replace) under a
    per-body lock so concurrent /model requests never serve a half-written GLB."""
    step = _step_for(ws, body_id)   # F4 traversal guard
    if step is None:
        return None
    glb = ws / f"{body_id}.glb"
    src = ws / f"{body_id}.glb.src"

    def _fresh() -> bool:
        if not glb.exists() or not src.exists():
            return False
        st = step.stat()
        try:
            size, mtime = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        # strict: rebuild if size differs OR the STEP is strictly newer than the
        # snapshot we built from. (== mtime + == size ⇒ genuinely unchanged.)
        return int(size) == st.st_size and float(mtime) >= st.st_mtime

    if _fresh():
        return glb
    lk = _glb_lock(f"{ws}::{body_id}")
    with lk:
        if _fresh():          # another thread may have built it while we waited
            return glb
        os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")
        from phone_designer.skills.create.import_step import ImportStep
        from phone_designer.skills.io.gltf_export import GltfExport
        st = step.stat()
        body = ImportStep().apply(None, {"path": str(step)}).body
        # tmp path has a non-.glb suffix, so force binary=True (GltfExport else
        # infers text glTF from the extension → a JSON file renamed to .glb).
        tmp = ws / f"{body_id}.glb.tmp{os.getpid()}"
        GltfExport().apply(body, {"path": str(tmp), "binary": True})
        if not tmp.exists():
            return None
        os.replace(tmp, glb)   # atomic publish
        src.write_text(json.dumps([st.st_size, st.st_mtime]), encoding="utf-8")
        return glb if glb.exists() else None


class _Handler(http.server.SimpleHTTPRequestHandler):
    ws: Path = Path(".")

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # F4: NO Access-Control-Allow-Origin:* — the viewer is same-origin (it
        # fetches /model, /pick, /api from its own 127.0.0.1 origin). A CORS
        # wildcard let any web page in the browser POST /pick and probe local
        # STEP files (CSRF / DNS-rebinding). Dropping it restores same-origin.
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if path in ("/", "/index.html"):
                html = (_STATIC / "index.html").read_bytes()
                return self._send(200, html, "text/html; charset=utf-8")
            if path == "/api/bodies":
                data = json.dumps({"bodies": _list_bodies(self.ws),
                                   "workspace": str(self.ws)}).encode()
                return self._send(200, data, "application/json")
            if path.startswith("/model/") and path.endswith(".glb"):
                body_id = path[len("/model/"):-len(".glb")]
                if not _safe_body_id(body_id):   # F4: refuse traversal outright
                    return self._send(404, b"refused body_id", "text/plain")
                glb = _glb_for(self.ws, body_id)
                if glb is None:
                    return self._send(404, b"unknown body_id", "text/plain")
                return self._send(200, glb.read_bytes(), "model/gltf-binary")
            # any other static asset under viewer_static/
            asset = _STATIC / path.lstrip("/")
            if asset.is_file() and _STATIC in asset.resolve().parents:
                ct = "application/javascript" if asset.suffix == ".js" else "text/plain"
                return self._send(200, asset.read_bytes(), ct)
            return self._send(404, b"not found", "text/plain")
        except Exception as exc:  # noqa: BLE001
            self._send(500, f"{type(exc).__name__}: {exc}".encode(), "text/plain")

    @staticmethod
    def _pick_request_error(payload) -> str | None:
        """F7: validate a /pick body → return an error string (→ 400) or None if
        the request SHAPE is acceptable (semantic checks like face_idx range /
        NaN point live in the pick fns and return {ok:False} with 200). Rejects a
        malformed CLIENT request (missing body_id, neither face_idx nor point,
        wrong types, non-finite point) with an honest 400 instead of a 500."""
        if not isinstance(payload, dict):
            return "body must be a JSON object"
        body_id = payload.get("body_id")
        if not body_id or not isinstance(body_id, str):
            return "need a string body_id"
        fi = payload.get("face_idx")
        pt = payload.get("point")
        if fi is None and pt is None:
            return "need face_idx (preferred) or point"
        if fi is not None:
            if isinstance(fi, bool) or not isinstance(fi, int):
                return f"face_idx must be an integer, got {fi!r}"
        elif pt is not None:
            if (not isinstance(pt, (list, tuple)) or len(pt) != 3
                    or not all(isinstance(v, (int, float))
                               and not isinstance(v, bool) for v in pt)):
                return f"point must be 3 numbers, got {pt!r}"
            if not all(math.isfinite(float(v)) for v in pt):
                return "point has a non-finite (NaN/inf) component"
        return None

    def do_POST(self):  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if path == "/pick":
                err = self._pick_request_error(payload)   # F7: 400 on bad input
                if err is not None:
                    return self._send(400, json.dumps(
                        {"ok": False, "error": err}).encode(), "application/json")
                body_id = payload.get("body_id", "")
                # F1: PREFER {face_idx} (exact, GLB primitive index == OCCT face
                # index). Fall back to {point} (nearest-centroid) only if no idx.
                if payload.get("face_idx") is not None:
                    res = pick_face_by_index(self.ws, body_id,
                                             payload.get("face_idx"))
                else:
                    res = pick_face(self.ws, body_id, payload.get("point"))
                return self._send(200, json.dumps(res).encode(), "application/json")
            return self._send(404, b"not found", "text/plain")
        except Exception as exc:  # noqa: BLE001
            self._send(500, json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"}).encode(),
                "application/json")

    def log_message(self, *a):  # quieter
        pass


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """F5: threaded + reuse-address server.

    - ThreadingMixIn: a slow /model GLB build (tessellate+write) no longer blocks
      /api/bodies, other GLBs, or /pick — the viewer stays responsive.
    - allow_reuse_address: Ctrl-C restart rebinds immediately (no TIME_WAIT
      'address already in use').
    - daemon_threads: outstanding request threads don't block interpreter exit.
    """
    allow_reuse_address = True
    daemon_threads = True


def serve(port: int = 8765, workspace: str | None = None) -> None:
    ws = Path(workspace) if workspace else _workspace()
    ws.mkdir(parents=True, exist_ok=True)
    _Handler.ws = ws
    with _Server(("127.0.0.1", port), _Handler) as httpd:
        print(f"CAD viewer: http://127.0.0.1:{port}/  (workspace: {ws})")
        print("  Ctrl-C to stop. Bodies = STEP files in the workspace.")
        httpd.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description="Web CAD viewer bridge (V1)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--workspace", default=None,
                    help="body workspace (default: $PHONE_DESIGNER_MCP_WORKSPACE "
                         "or the newest pd_mcp_* temp dir)")
    args = ap.parse_args()
    serve(args.port, args.workspace)


if __name__ == "__main__":
    main()
