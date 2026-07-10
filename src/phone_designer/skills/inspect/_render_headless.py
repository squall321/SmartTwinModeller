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

Depth cue: flat Lambert alone gave coplanar-normal faces at DIFFERENT depths
the IDENTICAL color (a housing's top flange vs its cavity floor are both +Z —
an open cavity was indistinguishable from a solid top). Shading is therefore
modulated by TWO deterministic depth terms — a per-face recession factor
(carries the visible separation; constant across a planar face so a flat plate
stays uniform) and a gentle per-pixel z-buffer fog (nearer = brighter). See
render_view for the tuning rationale.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

# ── depth-cue tuning (see render_view) ───────────────────────────────────────
# per-face recession: the deepest-recessed face keeps this fraction of its
# Lambert shade (1.0 would disable the cue). 0.72 puts a housing's top flange
# vs its cavity floor/inner walls ~15-30 gray levels apart on every channel.
_DEPTH_FACE_LO = 0.72
# per-pixel z-buffer fog: the farthest pixel loses this fraction (nearer =
# brighter). Kept gentle so a planar face slanted across the depth range (a box
# top in iso spans ~75-90% of it) stays visually uniform (<6 gray levels).
_DEPTH_FOG = 0.03

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

    # ── deterministic depth cue ──────────────────────────────────────────────
    # Flat Lambert gives coplanar-normal faces at different depths the SAME
    # color (top flange vs cavity floor: both +Z). Two modulations fix that:
    #  (1) per-face recession: d = n·(centroid - center) is CONSTANT across a
    #      planar face (a flat plate stays perfectly uniform) yet separates
    #      parallel faces — for camera-facing parallel planes a larger offset
    #      along the shared normal is NEARER the camera, so it shades brighter;
    #      recessed surfaces (cavity floors, hole walls) shade darker in every
    #      view. This term carries the required >=8-gray-level separation.
    #  (2) per-pixel z-buffer fog in the raster loop below (nearer = brighter).
    # A single per-pixel term cannot do both jobs: in iso a full-footprint top
    # face spans most of the scene depth range, so any fog strong enough to
    # split flange/floor by >=8 levels would band a flat top far beyond the
    # <6-level uniformity budget. Both terms are pure numpy, no randomness.
    tri_centroid = (verts[a] + verts[b] + verts[c]) / 3.0
    plane_off = np.einsum("ij,ij->i", nrm, tri_centroid - center)
    off_lo = plane_off.min()
    off_span = plane_off.max() - off_lo
    if off_span > 1e-9:
        t_face = (plane_off - off_lo) / off_span
    else:                                    # single plane / sphere: no cue
        t_face = np.ones_like(plane_off)
    shade = shade * (_DEPTH_FACE_LO + (1.0 - _DEPTH_FACE_LO) * t_face)
    base_col = np.array([70, 130, 200], dtype=float)   # steel blue solid

    # per-pixel fog normalization from the scene depth range (z-buffer values
    # are barycentric blends of vertex depths, so they stay inside this range).
    depth_lo = float(depth.min())
    depth_span = float(depth.max() - depth_lo)

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
        # per-pixel z-buffer fog: nearer (smaller depth) = brighter.
        if depth_span > 1e-9:
            t_pix = np.clip((zvals[closer] - depth_lo) / depth_span, 0.0, 1.0)
        else:
            t_pix = np.zeros(int(closer.sum()))
        fog = 1.0 - _DEPTH_FOG * t_pix
        col = np.clip(base_col[None, :] * shade[i] * fog[:, None], 0, 255)
        sub_img = img[miny:maxy + 1, minx:maxx + 1]
        sub_img[closer] = col

    # projection metadata (plain floats — JSON-safe) so a caller/test can map a
    # WORLD point to its pixel via project_to_pixel() and sample colors.
    projection = {
        "center": [float(x) for x in center],
        "right": [float(x) for x in right],
        "up": [float(x) for x in true_up],
        "fwd": [float(x) for x in fwd],
        "cu": float(cu), "cv": float(cv),
        "scale": float(scale), "size": int(size),
    }
    return np.clip(img, 0, 255).astype(np.uint8), {
        "view": view, "n_triangles": int(len(tris)), "empty": False,
        "projection": projection}


def project_to_pixel(point_xyz, info) -> tuple[int, int]:
    """Map a WORLD point to (col, row) pixel coords of a render_view image.

    Uses the ``projection`` metadata returned in render_view's info dict —
    lets a test (or a measuring caller) sample the pixel where a known feature
    (flange top, cavity floor, ...) lands, without duplicating camera math."""
    pr = info["projection"]
    rel = np.asarray(point_xyz, dtype=float) - np.asarray(pr["center"])
    u = float(rel @ np.asarray(pr["right"]))
    v = float(rel @ np.asarray(pr["up"]))
    x = (u - pr["cu"]) * pr["scale"] + pr["size"] / 2.0
    y = pr["size"] / 2.0 - (v - pr["cv"]) * pr["scale"]
    return int(round(x)), int(round(y))


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
