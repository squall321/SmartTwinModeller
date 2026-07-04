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

Run:  python -m phone_designer.viewer_server [--port 8765] [--workspace DIR]
Then open http://127.0.0.1:8765/ — it lists bodies (STEP files in the workspace)
and shows the chosen one in a rotate/zoom viewer.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

_STATIC = Path(__file__).with_name("viewer_static")


def _workspace() -> Path:
    d = os.environ.get("PHONE_DESIGNER_MCP_WORKSPACE")
    if d:
        return Path(d)
    # fall back to the newest pd_mcp_* temp workspace (what mcp_server made)
    tmp = Path(tempfile.gettempdir())
    cands = sorted(tmp.glob("pd_mcp_*"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
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


def _glb_for(ws: Path, body_id: str) -> Path | None:
    """Return the GLB for a body_id, building it from the STEP once (cached —
    the body is immutable, so a stale GLB never happens)."""
    step = ws / f"{body_id}.step"
    if not step.exists():
        return None
    glb = ws / f"{body_id}.glb"
    if glb.exists() and glb.stat().st_mtime >= step.stat().st_mtime:
        return glb
    # build it: import the STEP, GltfExport → GLB (reuses the verified skill)
    os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.io.gltf_export import GltfExport
    body = ImportStep().apply(None, {"path": str(step)}).body
    GltfExport().apply(body, {"path": str(glb)})
    return glb if glb.exists() else None


class _Handler(http.server.SimpleHTTPRequestHandler):
    ws: Path = Path(".")

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
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

    def log_message(self, *a):  # quieter
        pass


def serve(port: int = 8765, workspace: str | None = None) -> None:
    ws = Path(workspace) if workspace else _workspace()
    ws.mkdir(parents=True, exist_ok=True)
    _Handler.ws = ws
    with socketserver.TCPServer(("127.0.0.1", port), _Handler) as httpd:
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
