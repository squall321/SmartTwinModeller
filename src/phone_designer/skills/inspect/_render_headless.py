"""GL-free shaded renderer — turn a body into PNG previews with ZERO GPU/GL.

The problem: build_views (pyvista/VTK) needs a GL context, so on a headless CI
runner / a machine without Mesa it returns skipped_no_gl — an LLM client (or a
Claude Code session) driving the MCP server then cannot SEE what it modelled.

The fix: walk the OCCT ``BRepMesh`` triangulation and rasterize it ourselves with
numpy (a painter/z-buffer + Lambert shading) into a PNG via Pillow. Both are
already dependencies (numpy, Pillow 12). This runs ANYWHERE — no GL, no Qt, no
display — and is fully deterministic (same body + same view => byte-identical PNG),
which makes it CI-safe and cache-friendly.

Cameras: an orthographic projection along a fixed direction per named view
(iso / front / top / right / left / back / bottom), auto-fit to the body bbox.
Not photoreal — a clean shaded solid a human (or a vision model) can read for
"did the modelling do what I meant".
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

# view name -> (camera direction pointing AT the body, up hint). The camera looks
# from -dir toward the origin; +Z is up unless the view looks down/up Z.
_VIEWS = {
    "iso":    ((-1.0, -1.0, -1.0), (0.0, 0.0, 1.0)),
    "front":  ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),   # look along +Y (-Y face toward us)
    "back":   ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "right":  ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "left":   ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "top":    ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),   # look down -Z
    "bottom": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
}


def _occt(shape):
    return shape.wrapped if hasattr(shape, "wrapped") else shape


def _triangles(shape, deflection: float):
    """Return (verts Nx3, tris Mx3 int) in world coords from the OCCT mesh."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    s = _occt(shape)
    BRepMesh_IncrementalMesh(s, deflection, False, 0.5, True).Perform()

    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    ex = TopExp_Explorer(s, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face_s(ex.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            base = len(verts)
            n = tri.NbNodes()
            for i in range(1, n + 1):
                p = tri.Node(i).Transformed(trsf)
                verts.append((p.X(), p.Y(), p.Z()))
            reversed_ = face.Orientation() == TopAbs_REVERSED
            for i in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(i).Get()
                if reversed_:
                    b, c = c, b
                tris.append((base + a - 1, base + b - 1, base + c - 1))
        ex.Next()
    return np.asarray(verts, dtype=float), np.asarray(tris, dtype=np.int64)


def _basis(view_dir, up_hint):
    d = np.asarray(view_dir, dtype=float)
    d /= (np.linalg.norm(d) or 1.0)          # forward (camera -> body)
    up = np.asarray(up_hint, dtype=float)
    right = np.cross(d, up)
    if np.linalg.norm(right) < 1e-9:          # up parallel to view: pick another
        up = np.array([1.0, 0.0, 0.0])
        right = np.cross(d, up)
    right /= (np.linalg.norm(right) or 1.0)
    true_up = np.cross(right, d)
    true_up /= (np.linalg.norm(true_up) or 1.0)
    return right, true_up, d


def render_view(shape, view: str = "iso", *, size: int = 640,
                deflection: float | None = None) -> tuple[np.ndarray, dict]:
    """Render ONE named view to an RGB uint8 array (size x size x 3). No GL.

    Returns (image_array, info). info = {view, n_triangles, empty}.
    """
    if view not in _VIEWS:
        raise ValueError(f"fm.unknown_view: '{view}' not in {sorted(_VIEWS)}")
    view_dir, up_hint = _VIEWS[view]

    # auto deflection from bbox diagonal so any part gets a clean mesh.
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(_occt(shape), bb)
    bg = np.full((size, size, 3), 245, dtype=np.uint8)
    if bb.IsVoid():
        return bg, {"view": view, "n_triangles": 0, "empty": True}
    xmn, ymn, zmn, xmx, ymx, zmx = bb.Get()
    diag = math.sqrt((xmx - xmn) ** 2 + (ymx - ymn) ** 2 + (zmx - zmn) ** 2) or 1.0
    if deflection is None:
        deflection = max(diag / 400.0, 1e-3)

    verts, tris = _triangles(shape, deflection)
    if len(tris) == 0:
        return bg, {"view": view, "n_triangles": 0, "empty": True}

    right, true_up, fwd = _basis(view_dir, up_hint)
    center = verts.mean(axis=0)
    rel = verts - center
    u = rel @ right          # screen x
    v = rel @ true_up        # screen y
    depth = rel @ fwd        # +depth = farther along view dir

    # orthographic fit with a small margin
    span = max(u.max() - u.min(), v.max() - v.min(), 1e-6) * 1.12
    cu = (u.max() + u.min()) / 2.0
    cv = (v.max() + v.min()) / 2.0
    scale = (size - 1) / span
    px = ((u - cu) * scale + size / 2.0)
    py = (size / 2.0 - (v - cv) * scale)     # flip y for image coords

    # per-triangle Lambert shade from the view direction (headlamp) + ambient.
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    e1 = verts[b] - verts[a]
    e2 = verts[c] - verts[a]
    nrm = np.cross(e1, e2)
    ln = np.linalg.norm(nrm, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    nrm = nrm / ln
    lambert = np.abs(nrm @ (-fwd))           # |n·light|, two-sided
    shade = (0.30 + 0.70 * lambert)          # ambient 0.30
    base_col = np.array([70, 130, 200], dtype=float)   # steel blue solid

    # z-buffer painter's rasterizer (numpy, per-triangle scanline via bbox fill).
    img = bg.astype(float).copy()
    zbuf = np.full((size, size), np.inf)
    tp = np.stack([px, py], axis=1)
    td = depth
    for i in range(len(tris)):
        ia, ib, ic = tris[i]
        x0, y0 = tp[ia]; x1, y1 = tp[ib]; x2, y2 = tp[ic]
        minx = int(max(0, math.floor(min(x0, x1, x2))))
        maxx = int(min(size - 1, math.ceil(max(x0, x1, x2))))
        miny = int(max(0, math.floor(min(y0, y1, y2))))
        maxy = int(min(size - 1, math.ceil(max(y0, y1, y2))))
        if minx > maxx or miny > maxy:
            continue
        denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(denom) < 1e-9:
            continue
        ys, xs = np.mgrid[miny:maxy + 1, minx:maxx + 1]
        w0 = ((y1 - y2) * (xs - x2) + (x2 - x1) * (ys - y2)) / denom
        w1 = ((y2 - y0) * (xs - x2) + (x0 - x2) * (ys - y2)) / denom
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue
        zvals = w0 * td[ia] + w1 * td[ib] + w2 * td[ic]
        sub_z = zbuf[miny:maxy + 1, minx:maxx + 1]
        closer = inside & (zvals < sub_z)
        if not closer.any():
            continue
        sub_z[closer] = zvals[closer]
        col = np.clip(base_col * shade[i], 0, 255)
        sub_img = img[miny:maxy + 1, minx:maxx + 1]
        sub_img[closer] = col

    return np.clip(img, 0, 255).astype(np.uint8), {
        "view": view, "n_triangles": int(len(tris)), "empty": False}


def render_views_to_pngs(shape, out_dir: str, views=("iso", "front", "top"),
                         *, size: int = 640, stem: str = "preview") -> dict:
    """Render each view to <out_dir>/<stem>_<view>.png. Returns
    {'images': {view: path}, 'skipped': False, 'renderer': 'headless_raster',
     'note': ...}. NEVER raises for a normal body; a per-view failure records
    that view as None with the reason (honest, not a black PNG)."""
    from PIL import Image
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    images: dict[str, str | None] = {}
    notes: list[str] = []
    for v in views:
        try:
            arr, info = render_view(shape, v, size=size)
            if info["empty"]:
                images[v] = None
                notes.append(f"{v}: empty (no mesh)")
                continue
            p = out / f"{stem}_{v}.png"
            Image.fromarray(arr, "RGB").save(str(p))
            images[v] = str(p)
        except Exception as exc:  # noqa: BLE001
            images[v] = None
            notes.append(f"{v}: {type(exc).__name__}: {exc}")
    return {
        "images": images,
        "skipped": False,
        "renderer": "headless_raster",
        "note": "GL-free numpy z-buffer render" + (
            " | " + "; ".join(notes) if notes else ""),
    }
