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

V3 adds two read-only view endpoints:
  * GET /scene?body_id → the body's extract_feature_catalog (holes/pockets/bosses,
    each with id/type/face_indices/diameters_mm/depth_mm) + bbox_mm + n_faces. The
    face_indices ARE the GLB primitive indices the browser highlights (1:1). The
    catalog is SLOW on complex parts, so it is cached next to the STEP
    (<id>.scene.json) with a (size,mtime) sidecar that invalidates like the GLB.
  * GET /section?body_id&axis=x|y|z&pos=0..1 → a TRANSIENT GLB of the body CUT by
    an axis-aligned plane (split_body keep='negative') so the user sees inside.
    A section is a VIEW artifact — it streams <id>.sec.glb and NEVER mints a new
    body_id / .step (no lineage mutation). A missed/degenerate split honestly
    falls back to the full body GLB (X-Section-Cut:0).

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
from urllib.parse import parse_qs, unquote, urlparse

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


# ── V3 /scene: cached feature catalog (holes/pockets/bosses) per body ────────
# extract_feature_catalog can be SLOW on complex parts, so we run it ONCE and
# cache the strict-JSON-safe scene next to the STEP (<id>.scene.json) with a
# (size,mtime) sidecar that invalidates exactly like the GLB (F6 semantics).
_SCENE_LOCKS: dict[str, threading.Lock] = {}
_SCENE_LOCKS_GUARD = threading.Lock()


def _scene_lock(key: str) -> "threading.Lock":
    with _SCENE_LOCKS_GUARD:
        lk = _SCENE_LOCKS.get(key)
        if lk is None:
            lk = _SCENE_LOCKS[key] = threading.Lock()
        return lk


def _jsonify(obj):
    """Coerce a catalog fragment to strict-JSON-safe values (no NaN/inf, no
    tuples-as-keys, no OCCT handles). extract_feature_catalog stores plain
    lists/dicts/floats, but a stray NaN/inf (bbox on a degenerate face) would
    make json.dumps emit non-strict tokens the browser's JSON.parse rejects."""
    if isinstance(obj, dict):
        return {str(k): _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, int):
        return obj
    if obj is None or isinstance(obj, str):
        return obj
    # anything exotic (OCCT handle, numpy scalar) → its string form, never crash.
    try:
        f = float(obj)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return str(obj)


def _slim_feature(feat: dict, n_faces: int) -> dict:
    """Keep the browser-relevant keys of ONE feature and clamp its face_indices
    to the valid GLB-primitive range [0, n_faces). The face_indices ARE the GLB
    primitive indices the viewer highlights (1:1 keystone), so an out-of-range
    index (a detector referencing a face that isn't in _all_faces order) would
    highlight the wrong primitive — drop those defensively."""
    fis = [int(i) for i in (feat.get("face_indices") or [])
           if isinstance(i, (int, float)) and not isinstance(i, bool)
           and 0 <= int(i) < n_faces]
    out = {
        "id": feat.get("id"),
        "type": feat.get("type") or feat.get("kind"),
        "face_indices": fis,
    }
    # carry the size/geometry hints the sidebar shows, when the detector has them.
    for k in ("diameters_mm", "depth_mm", "top_d_mm", "height_mm",
              "diameter_or_size_mm", "center", "axis_origin"):
        if k in feat and feat[k] is not None:
            out[k] = feat[k]
    return _jsonify(out)


def _build_scene(step: Path) -> dict:
    """Import the STEP, run extract_feature_catalog ONCE, and return the
    strict-JSON-safe scene dict: {ok, features:{holes,pockets,bosses}, bbox_mm,
    n_faces}. Reuses _load_faces (traversal-safe, _all_faces order == GLB order)
    for n_faces and the same ImportStep body for the catalog."""
    os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    from phone_designer.skills._resolvers import _all_faces

    body = ImportStep().apply(None, {"path": str(step)}).body
    shape = body.wrapped if hasattr(body, "wrapped") else body
    n_faces = len(_all_faces(shape))
    cat = ExtractFeatureCatalog().apply(body, {}).extras.get(
        "feature_catalog", {}) or {}
    bbox = cat.get("initial_bbox_mm")

    def _kind(items):
        return [_slim_feature(f, n_faces) for f in (items or [])
                if isinstance(f, dict)]

    return {
        "ok": True,
        "features": {
            "holes":   _kind(cat.get("holes")),
            "pockets": _kind(cat.get("pockets")),
            "bosses":  _kind(cat.get("bosses")),
        },
        "bbox_mm": _jsonify(bbox) if bbox is not None else None,
        "n_faces": int(n_faces),
    }


# Bump when the scene's DETECTION changes (classify_holes/extract_feature_catalog
# behaviour), not just its schema. Audit finding: a scene cached before the
# classify_holes arc-gate kept serving 4 phantom Ø24 "holes" — the STEP never
# changed, so size+mtime alone can never notice a detector-code change.
_SCENE_VER = 2


def _scene_for(ws: Path, body_id: str) -> dict | None:
    """Return the cached scene dict for body_id, (re)building it from the STEP
    when the sidecar is stale (F6 size+mtime invalidation + _SCENE_VER detector
    version). Returns None only when the body_id is unsafe/unknown (→ 404)."""
    step = _step_for(ws, body_id)   # F4 traversal guard
    if step is None:
        return None
    scene = ws / f"{body_id}.scene.json"
    src = ws / f"{body_id}.scene.src"

    def _fresh() -> bool:
        if not scene.exists() or not src.exists():
            return False
        st = step.stat()
        try:
            rec = json.loads(src.read_text(encoding="utf-8"))
            size, mtime = rec[0], rec[1]
            ver = rec[2] if len(rec) > 2 else 0   # pre-versioned sidecar → stale
        except (OSError, ValueError, IndexError, TypeError):
            return False
        return (int(size) == st.st_size and float(mtime) >= st.st_mtime
                and int(ver) == _SCENE_VER)

    if _fresh():
        try:
            return json.loads(scene.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass   # corrupt cache → rebuild below
    lk = _scene_lock(f"{ws}::{body_id}")
    with lk:
        if _fresh():
            try:
                return json.loads(scene.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        st = step.stat()
        data = _build_scene(step)
        text = json.dumps(data)   # strict JSON (no NaN/inf via _jsonify)
        tmp = ws / f"{body_id}.scene.tmp{os.getpid()}"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, scene)    # atomic publish
        src.write_text(json.dumps([st.st_size, st.st_mtime, _SCENE_VER]),
                       encoding="utf-8")
        return data


# ── V3 /section: transient cut-half GLB (a VIEW artifact — no lineage edit) ──
# split_body(keep='negative', plane) returns a cut-half SOLID; we export it to a
# TRANSIENT <id>.sec.glb (NEVER a new body_id / .step — sections must not touch
# lineage). Cached by (body_id, axis, quantized pos) so slider drags reuse work.
_SECTION_LOCKS: dict[str, threading.Lock] = {}
_SECTION_LOCKS_GUARD = threading.Lock()
_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def _section_lock(key: str) -> "threading.Lock":
    with _SECTION_LOCKS_GUARD:
        lk = _SECTION_LOCKS.get(key)
        if lk is None:
            lk = _SECTION_LOCKS[key] = threading.Lock()
        return lk


def _quantize_pos(pos: float) -> int:
    """Quantize a 0..1 slider position to 1/100ths so nearby drags hit the same
    cache slot (100 buckets is finer than the eye needs, coarse enough to cache)."""
    return max(0, min(100, int(round(float(pos) * 100.0))))


def _section_for(ws: Path, body_id: str, axis: str, pos: float) -> dict | None:
    """Cut body_id by an axis-aligned plane at fractional ``pos`` (0..1 along the
    body's bbox on that axis), export the negative (lower) half to a TRANSIENT
    <id>.sec.glb, and return {ok, glb, cut, note}. Returns None only for an
    unsafe/unknown body_id (→ 404). If the split misses / is degenerate we fall
    back to the FULL body GLB with an honest note (ok stays True — the viewer
    still shows something, just not cut). NEVER creates a new .step / body_id."""
    step = _step_for(ws, body_id)   # F4 traversal guard
    if step is None:
        return None
    ax = axis.lower()
    ai = _AXIS_INDEX.get(ax)
    if ai is None:
        return None   # validated at the handler, defensive here too
    q = _quantize_pos(pos)
    glb = ws / f"{body_id}.sec.glb"
    src = ws / f"{body_id}.sec.src"

    def _fresh() -> bool:
        if not glb.exists() or not src.exists():
            return False
        st = step.stat()
        try:
            size, mtime, cax, cq = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (int(size) == st.st_size and float(mtime) >= st.st_mtime
                and cax == ax and int(cq) == q)

    if _fresh():
        return {"ok": True, "glb": glb, "cut": True, "note": ""}
    lk = _section_lock(f"{ws}::{body_id}")
    with lk:
        if _fresh():
            return {"ok": True, "glb": glb, "cut": True, "note": ""}
        os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")
        from phone_designer.skills.create.import_step import ImportStep
        from phone_designer.skills.io.gltf_export import GltfExport
        from phone_designer.skills.transform.split_body import SplitBody

        st = step.stat()
        body = ImportStep().apply(None, {"path": str(step)}).body
        shape = body.wrapped if hasattr(body, "wrapped") else body
        # bbox on the chosen axis → the plane origin at fraction pos.
        bb = _shape_bbox(shape)
        cut = True
        note = ""
        cut_body = None
        if bb is None:
            cut, note = False, "no bbox (degenerate body); showing full body"
        else:
            lo, hi = bb[ai], bb[ai + 3]
            frac = max(0.0, min(1.0, float(pos)))
            origin = [0.0, 0.0, 0.0]
            origin[ai] = lo + (hi - lo) * frac
            normal = [0.0, 0.0, 0.0]
            normal[ai] = 1.0
            try:
                res = SplitBody().apply(body, {
                    "plane_origin_mm": origin, "plane_normal": normal,
                    "keep": "negative"})
                cut_body = res.body
            except Exception as exc:  # noqa: BLE001
                cut = False
                note = f"split failed ({type(exc).__name__}); showing full body"
        export_body = cut_body if cut_body is not None else body
        tmp = ws / f"{body_id}.sec.glb.tmp{os.getpid()}"
        GltfExport().apply(export_body, {"path": str(tmp), "binary": True})
        if not tmp.exists():
            return {"ok": False, "error": "section export produced no GLB"}
        os.replace(tmp, glb)   # atomic publish
        src.write_text(json.dumps([st.st_size, st.st_mtime, ax, q]),
                       encoding="utf-8")
        return {"ok": True, "glb": glb, "cut": cut, "note": note}


# ── /components: the face→component map the multi-body front-end needs ───────
# A multi-body STEP → GLB is ONE merged mesh, but the GLB emits ONE primitive
# per OCCT face IN _all_faces ORDER (1:1 — verified keystone). So a face_idx→
# component map lets the browser color / select / isolate per component using
# only the face_idx RANGES the server provides (no per-component GLB grouping).
#
# Build (verified keystone): _load_faces(step) is the global ordered face list
# (== GLB primitives). For each solid from iter_solid_components(shape), match
# ITS faces back to the global index by TopoDS IsSame — a plate+bolt gives
# {comp0: faces[0-5], comp1: faces[6-8]}, every face mapped. We use the RAW
# iter_solid_components split (milliseconds), NOT analyze_assembly's heavier
# dedup, to stay responsive. A single-solid body → n_components=1 (honest, not
# an error). Cached per body (<id>.components.json + .src, F6 size+mtime).
_COMPONENTS_LOCKS: dict[str, threading.Lock] = {}
_COMPONENTS_LOCKS_GUARD = threading.Lock()


def _components_lock(key: str) -> "threading.Lock":
    with _COMPONENTS_LOCKS_GUARD:
        lk = _COMPONENTS_LOCKS.get(key)
        if lk is None:
            lk = _COMPONENTS_LOCKS[key] = threading.Lock()
        return lk


def _solid_props(solid):
    """(|volume_mm3|, centroid[3]) for one solid via BRepGProp — abs() because
    an ill-oriented imported solid can integrate to a negative signed volume."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(solid, g)
    c = g.CentreOfMass()
    return abs(float(g.Mass())), (c.X(), c.Y(), c.Z())


def _build_components(step: Path) -> dict:
    """Import the STEP once and build the strict-JSON-safe component map:
    {ok, n_components, components:[{comp_id, face_indices, n_faces, volume_mm3,
    centroid, bbox_mm, label}]}. face_indices index the GLOBAL _all_faces list
    (== GLB primitive order, 1:1 keystone). Uses the RAW iter_solid_components
    split for responsiveness (NOT analyze_assembly)."""
    os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")
    from phone_designer.skills._resolvers import _all_faces
    from phone_designer.skills.assembly._compound import iter_solid_components
    from phone_designer.skills.create.import_step import ImportStep

    body = ImportStep().apply(None, {"path": str(step)}).body
    shape = body.wrapped if hasattr(body, "wrapped") else body
    faces = _all_faces(shape)            # global ordered list == GLB primitives
    total = len(faces)

    components: list[dict] = []
    for ci, solid in enumerate(iter_solid_components(shape)):
        # match this solid's faces back to the GLOBAL index by TopoDS IsSame
        # (the verified keystone). Skip a global face already claimed so a face
        # shared/duplicated across solids maps to exactly one component.
        sub = _all_faces(solid)
        fis: list[int] = []
        for sf in sub:
            for gi, gf in enumerate(faces):
                if gi not in fis and sf.IsSame(gf):
                    fis.append(gi)
                    break
        fis.sort()
        vol, centroid = _solid_props(solid)
        bbox = _shape_bbox(solid)
        components.append(_jsonify({
            "comp_id": ci,
            "face_indices": fis,
            "n_faces": len(fis),
            "volume_mm3": vol,
            "centroid": list(centroid),
            "bbox_mm": list(bbox) if bbox is not None else None,
            # keep it FAST: a raw split has no class/standard-part naming, so a
            # plain honest default label (the browser can rename on select).
            "label": f"component {ci}",
        }))
    return {
        "ok": True,
        "n_components": len(components),
        "n_faces": int(total),
        "components": components,
    }


def _components_for(ws: Path, body_id: str) -> dict | None:
    """Return the cached component map for body_id, (re)building it from the
    STEP when the sidecar is stale (same F6 size+mtime invalidation as /scene).
    Returns None only when the body_id is unsafe/unknown (→ handler 404s)."""
    step = _step_for(ws, body_id)   # F4 traversal guard
    if step is None:
        return None
    comp = ws / f"{body_id}.components.json"
    src = ws / f"{body_id}.components.src"

    def _fresh() -> bool:
        if not comp.exists() or not src.exists():
            return False
        st = step.stat()
        try:
            size, mtime = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return int(size) == st.st_size and float(mtime) >= st.st_mtime

    if _fresh():
        try:
            return json.loads(comp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass   # corrupt cache → rebuild below
    lk = _components_lock(f"{ws}::{body_id}")
    with lk:
        if _fresh():
            try:
                return json.loads(comp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        st = step.stat()
        data = _build_components(step)
        text = json.dumps(data)   # strict JSON (no NaN/inf via _jsonify)
        tmp = ws / f"{body_id}.components.tmp{os.getpid()}"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, comp)     # atomic publish
        src.write_text(json.dumps([st.st_size, st.st_mtime]), encoding="utf-8")
        return data


def _shape_bbox(shape):
    """(xmin,ymin,zmin,xmax,ymax,zmax) optimal bbox, or None if void/failed."""
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        bb = Bnd_Box()
        try:
            BRepBndLib.AddOptimal_s(shape, bb)
        except Exception:  # noqa: BLE001
            BRepBndLib.Add_s(shape, bb)
        if bb.IsVoid():
            return None
        return tuple(float(c) for c in bb.Get())
    except Exception:  # noqa: BLE001
        return None


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
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
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
            if path == "/scene":
                return self._do_scene(parse_qs(parsed.query))
            if path == "/components":
                return self._do_components(parse_qs(parsed.query))
            if path == "/section":
                return self._do_section(parse_qs(parsed.query))
            # any other static asset under viewer_static/
            asset = _STATIC / path.lstrip("/")
            if asset.is_file() and _STATIC in asset.resolve().parents:
                ct = "application/javascript" if asset.suffix == ".js" else "text/plain"
                return self._send(200, asset.read_bytes(), ct)
            return self._send(404, b"not found", "text/plain")
        except Exception as exc:  # noqa: BLE001
            self._send(500, f"{type(exc).__name__}: {exc}".encode(), "text/plain")

    def _do_scene(self, qs: dict):
        """GET /scene?body_id → {ok, features:{holes,pockets,bosses}, bbox_mm,
        n_faces}. The feature face_indices ARE the GLB primitive indices the
        browser highlights (1:1 keystone). Cached per body (immutable-ish, F6)."""
        body_id = (qs.get("body_id") or [""])[0]
        if not _safe_body_id(body_id):     # F4: refuse traversal / empty id
            return self._send(400, json.dumps(
                {"ok": False, "error": "missing or unsafe body_id"}).encode(),
                "application/json")
        scene = _scene_for(self.ws, body_id)
        if scene is None:
            return self._send(404, json.dumps(
                {"ok": False, "error": f"unknown body_id '{body_id}'"}).encode(),
                "application/json")
        return self._send(200, json.dumps(scene).encode(), "application/json")

    def _do_components(self, qs: dict):
        """GET /components?body_id → {ok, n_components, n_faces, components:[
        {comp_id, face_indices, n_faces, volume_mm3, centroid, bbox_mm, label}]}.
        The face_indices are the GLOBAL _all_faces indices == GLB primitive
        indices (1:1 keystone), so the browser colors / selects / isolates a
        component by its face_idx range. A single-solid body → n_components=1
        (honest, not an error). Cached per body (F6 size+mtime)."""
        body_id = (qs.get("body_id") or [""])[0]
        if not _safe_body_id(body_id):     # F4: refuse traversal / empty id
            return self._send(400, json.dumps(
                {"ok": False, "error": "missing or unsafe body_id"}).encode(),
                "application/json")
        comps = _components_for(self.ws, body_id)
        if comps is None:
            return self._send(404, json.dumps(
                {"ok": False, "error": f"unknown body_id '{body_id}'"}).encode(),
                "application/json")
        return self._send(200, json.dumps(comps).encode(), "application/json")

    def _do_section(self, qs: dict):
        """GET /section?body_id&axis=x|y|z&pos=<0..1> → a fresh TRANSIENT GLB of
        the body CUT by a plane (keep='negative') so the user sees inside. Bad
        axis / pos → 400. A missed/degenerate split falls back to the FULL body
        GLB with an honest note header. NEVER creates a new .step / body_id."""
        body_id = (qs.get("body_id") or [""])[0]
        if not _safe_body_id(body_id):     # F4
            return self._send(400, json.dumps(
                {"ok": False, "error": "missing or unsafe body_id"}).encode(),
                "application/json")
        axis = (qs.get("axis") or ["z"])[0].lower()
        if axis not in _AXIS_INDEX:        # validate axis ∈ {x,y,z} → 400
            return self._send(400, json.dumps(
                {"ok": False, "error": f"axis must be x|y|z, got {axis!r}"}
            ).encode(), "application/json")
        pos_raw = (qs.get("pos") or ["0.5"])[0]
        try:
            pos = float(pos_raw)
        except (TypeError, ValueError):
            return self._send(400, json.dumps(
                {"ok": False, "error": f"pos must be a number in [0,1], got "
                 f"{pos_raw!r}"}).encode(), "application/json")
        if not math.isfinite(pos) or pos < 0.0 or pos > 1.0:  # validate pos∈[0,1]
            return self._send(400, json.dumps(
                {"ok": False, "error": f"pos must be in [0,1], got {pos}"}
            ).encode(), "application/json")
        res = _section_for(self.ws, body_id, axis, pos)
        if res is None:
            return self._send(404, json.dumps(
                {"ok": False, "error": f"unknown body_id '{body_id}'"}).encode(),
                "application/json")
        if not res.get("ok"):
            return self._send(500, json.dumps(res).encode(), "application/json")
        glb = res["glb"]
        data = glb.read_bytes()
        # honest header: whether the plane actually cut (vs full-body fallback).
        self.send_response(200)
        self.send_header("Content-Type", "model/gltf-binary")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Section-Cut", "1" if res.get("cut") else "0")
        if res.get("note"):
            self.send_header("X-Section-Note", res["note"])
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

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
