"""_report_render — headless render helper for emit_quality_report (phase-2, 2026-06-14).

Render a body into base64-encoded PNG views so ``emit_quality_report`` can embed
them inline in its self-contained HTML (no external assets, no 3D viewer):

  (a) an isometric shaded view,
  (b) 2-3 cross-section views — the section outline (BRepAlgoAPI_Section, the
      SAME machinery as ``inspect/cross_section.py``) overlaid on the shaded part,
  (c) an OPTIONAL per-face heatmap view (deviation / wall-thickness / draft) when
      a per-face colour map is supplied (reuse ``color_by_property`` output).

Design constraints honoured here
--------------------------------
* Standalone read-only helper (NOT a ``@skill``) — it imports / reuses the
  existing pyvista→VTK tessellation path (``logging/viewport_capture._shape_to_polydata``)
  and the section machinery; it does NOT modify them.
* ``_face_count_guard`` runs BEFORE any tessellation — a 4000-face part SKIPS the
  render with ``render_status='skipped_too_big'`` instead of hanging the report.
* No GL context (CI / headless without Mesa, pyvista not installed, EGL/OSMesa
  missing) → every entry point CATCHES and returns a skip-marker
  (``render_status='skipped_no_gl'``); it NEVER raises. The JSON+HTML-without-images
  path in emit_quality_report must stay green regardless.

Public API
----------
``build_views(shape, *, sections=..., face_colormap=None, ...) -> dict``::

    {
      "render_status": "ok" | "skipped_no_gl" | "skipped_too_big" | "empty",
      "images": { "<view_id>": "<base64 PNG>", ... },   # bytes (base64), HTML-inline
      "views": [                                         # METADATA only (no bytes)
        {"id", "kind", "title", "plane_origin"?, "plane_normal"?,
         "area_mm2"?, "edge_count"?, "property"?, "width_px", "height_px"},
        ...
      ],
      "face_count": int,
      "note": str,                                       # human-readable status note
    }

The caller embeds ``images[id]`` as ``<img src="data:image/png;base64,...">`` and
reads ``views`` for the per-view title / measured-vs-estimate badge.
"""
from __future__ import annotations

import base64
import math
import os
from typing import Any

from phone_designer.skills._face_count_guard import DEFAULT_MAX_FACE_COUNT


# Default render window. Small enough that a base64 PNG embeds without bloating
# the HTML, large enough to read a section outline.
_DEFAULT_WINDOW = (640, 480)


def _occt_shape(shape: Any):
    return shape.wrapped if hasattr(shape, "wrapped") else shape


# ──────────────────────────────────────────────────────────────────────────────
# GL / pyvista availability — every render goes through here so a missing GL
# context, a missing pyvista, or a failed offscreen context degrades to a
# skip-marker instead of crashing the whole report.


def _ensure_offscreen_env() -> None:
    """Set the headless env hints pyvista/Qt look for (idempotent)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # PyVista respects PYVISTA_OFF_SCREEN for global offscreen default.
    os.environ.setdefault("PYVISTA_OFF_SCREEN", "true")


def probe_gl() -> tuple[bool, str]:
    """(gl_ok, note). True iff pyvista can open an offscreen context and write a
    non-empty screenshot on THIS machine. Never raises."""
    _ensure_offscreen_env()
    try:
        import numpy as np
        import pyvista as pv
    except Exception as exc:  # pyvista / VTK not importable
        return False, f"pyvista unavailable: {type(exc).__name__}: {exc}"
    try:
        pl = pv.Plotter(off_screen=True, window_size=[64, 48])
        pl.add_mesh(pv.Sphere(radius=1.0), color="lightgray")
        img = pl.screenshot(return_img=True)
        pl.close()
        if img is None or not np.asarray(img).any():
            return False, "offscreen screenshot empty (no GL pixels)"
        return True, "offscreen GL ok"
    except Exception as exc:  # no GL context / EGL / OSMesa missing
        return False, f"no GL context: {type(exc).__name__}: {exc}"


# ──────────────────────────────────────────────────────────────────────────────
# geometry helpers (reuse the established idioms — do not reinvent)


def _bbox(shape) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """OCCT optimal bbox — (min, max). Mirrors inspect_geometry._bbox."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        try:
            BRepBndLib.Add_s(shape, bb)
        except Exception:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    if bb.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def _section_polylines(shape, origin, normal, samples_per_edge: int = 48):
    """Section outline as a list of 3D polylines — BRepAlgoAPI_Section, the
    SAME machinery used by cross_section.py / _section_curve.py. Returns
    (polylines, edge_count, total_length_mm). Empty on a miss; never raises."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

    from phone_designer.skills._resolvers import _all_edges
    from phone_designer.skills.inspect.cross_section import _sample_edge

    nx, ny, nz = normal
    if (nx * nx + ny * ny + nz * nz) < 1e-18:
        return [], 0, 0.0
    try:
        plane = gp_Pln(gp_Pnt(*origin), gp_Dir(nx, ny, nz))
        section = BRepAlgoAPI_Section(shape, plane)
        section.ComputePCurveOn1(False)
        section.Approximation(False)
        section.Build()
        if not section.IsDone():
            return [], 0, 0.0
        edges = _all_edges(section.Shape())
        polylines: list[list[tuple[float, float, float]]] = []
        total = 0.0
        for e in edges:
            try:
                pts, L = _sample_edge(e, samples_per_edge)
            except Exception:
                pts, L = [], 0.0
            if pts:
                polylines.append(pts)
            total += L
        return polylines, len(polylines), total
    except Exception:
        return [], 0, 0.0


def _default_section_planes(shape):
    """Up to three principal mid-plane sections through the part centre, named
    by the cutting-plane normal. Returns [(view_id, title, origin, normal), ...]."""
    (mn, mx) = _bbox(shape)
    c = (
        0.5 * (mn[0] + mx[0]),
        0.5 * (mn[1] + mx[1]),
        0.5 * (mn[2] + mx[2]),
    )
    ext = (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])
    # Order by descending extent of the in-plane span so the most informative
    # cut comes first; cap at 3.
    planes = [
        ("section_z", "Section — XY mid-plane (normal +Z)", c, (0.0, 0.0, 1.0), ext[0] * ext[1]),
        ("section_y", "Section — XZ mid-plane (normal +Y)", c, (0.0, 1.0, 0.0), ext[0] * ext[2]),
        ("section_x", "Section — YZ mid-plane (normal +X)", c, (1.0, 0.0, 0.0), ext[1] * ext[2]),
    ]
    planes.sort(key=lambda p: p[4], reverse=True)
    return [(vid, title, o, n) for (vid, title, o, n, _span) in planes]


# ──────────────────────────────────────────────────────────────────────────────
# rendering


def _polydata(shape):
    """OCCT shape → pyvista.PolyData via the established tessellation path."""
    from phone_designer.logging.viewport_capture import _shape_to_polydata

    return _shape_to_polydata(shape)


def _png_b64_from_plotter(plotter) -> str | None:
    """screenshot → PNG bytes → base64 ascii. None on any failure."""
    import numpy as np

    try:
        img = plotter.screenshot(return_img=True)
    except Exception:
        return None
    arr = np.asarray(img) if img is not None else None
    if arr is None or arr.size == 0 or not arr.any():
        return None
    # Encode to PNG. Prefer imageio (pyvista ships it); fall back to PIL.
    png: bytes | None = None
    try:
        import imageio.v2 as imageio  # type: ignore

        import io

        buf = io.BytesIO()
        imageio.imwrite(buf, arr, format="png")
        png = buf.getvalue()
    except Exception:
        try:
            import io

            from PIL import Image  # type: ignore

            buf = io.BytesIO()
            Image.fromarray(arr).save(buf, format="PNG")
            png = buf.getvalue()
        except Exception:
            png = None
    if not png:
        return None
    return base64.b64encode(png).decode("ascii")


def _render_iso(polydata, window) -> str | None:
    """Isometric shaded view with edges. base64 PNG or None."""
    import pyvista as pv

    pl = None
    try:
        pl = pv.Plotter(off_screen=True, window_size=list(window))
        pl.background_color = "white"
        pl.add_mesh(
            polydata, color="lightgray", show_edges=True,
            edge_color="#444444", line_width=1,
        )
        try:
            pl.camera_position = "iso"
        except Exception:
            pass
        pl.reset_camera()
        return _png_b64_from_plotter(pl)
    except Exception:
        return None
    finally:
        if pl is not None:
            try:
                pl.close()
            except Exception:
                pass


def _render_section(polydata, polylines, normal, window) -> str | None:
    """Shaded part (ghosted) + the section outline drawn as bright lines, viewed
    down the cutting-plane normal. base64 PNG or None."""
    import numpy as np
    import pyvista as pv

    pl = None
    try:
        pl = pv.Plotter(off_screen=True, window_size=list(window))
        pl.background_color = "white"
        # Ghost the solid so the section outline reads clearly on top.
        pl.add_mesh(polydata, color="#e8eef5", opacity=0.35, show_edges=False)
        for poly in polylines:
            if len(poly) < 2:
                continue
            pts = np.asarray(poly, dtype=float)
            # pyvista line: [n, i0, i1, ...] cell array for a polyline.
            n = pts.shape[0]
            cells = np.empty(n + 1, dtype=np.int64)
            cells[0] = n
            cells[1:] = np.arange(n, dtype=np.int64)
            line = pv.PolyData(pts)
            line.lines = cells
            pl.add_mesh(line, color="#cf222e", line_width=3)
        # Look straight down the cutting-plane normal.
        nx, ny, nz = normal
        nmag = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        view = (nx / nmag, ny / nmag, nz / nmag)
        try:
            pl.view_vector(view)
        except Exception:
            pass
        pl.reset_camera()
        return _png_b64_from_plotter(pl)
    except Exception:
        return None
    finally:
        if pl is not None:
            try:
                pl.close()
            except Exception:
                pass


def _render_heatmap(shape, polydata, face_colormap, window):
    """Per-face heatmap view. ``face_colormap`` is the ``color_by_property``
    output: {"face_colors":[{"face_idx","value","color_rgb"}], "property":...}.

    We re-tessellate per face so the colour maps 1:1 onto faces (the cached
    _shape_to_polydata mesh loses face identity). Returns (b64|None, meta|None).
    """
    import numpy as np
    import pyvista as pv

    face_colors = (face_colormap or {}).get("face_colors") or []
    if not face_colors:
        return None, None
    prop = (face_colormap or {}).get("property", "value")

    pl = None
    try:
        from phone_designer.skills._resolvers import _all_faces
        from phone_designer.skills.inspect.face_tessellate import _extract_face_mesh
        from OCP.BRepMesh import BRepMesh_IncrementalMesh

        occ = _occt_shape(shape)
        BRepMesh_IncrementalMesh(occ, 0.2, False, 0.5, True).Perform()
        faces = _all_faces(occ)
        color_by_idx = {fc["face_idx"]: fc.get("color_rgb", (0.5, 0.5, 0.5))
                        for fc in face_colors}

        pl = pv.Plotter(off_screen=True, window_size=list(window))
        pl.background_color = "white"
        any_face = False
        for i, f in enumerate(faces):
            try:
                fm = _extract_face_mesh(f, 0.2)
            except Exception:
                continue
            verts = fm.get("vertices") or []
            tris = fm.get("triangles") or []
            if len(verts) < 3 or not tris:
                continue
            pts = np.asarray(verts, dtype=float)
            cells = np.empty((len(tris), 4), dtype=np.int64)
            cells[:, 0] = 3
            cells[:, 1:] = np.asarray(tris, dtype=np.int64)
            mesh = pv.PolyData(pts, cells.ravel())
            rgb = color_by_idx.get(i, (0.7, 0.7, 0.7))
            pl.add_mesh(mesh, color=tuple(float(c) for c in rgb), show_edges=False)
            any_face = True
        if not any_face:
            return None, None
        try:
            pl.camera_position = "iso"
        except Exception:
            pass
        pl.reset_camera()
        b64 = _png_b64_from_plotter(pl)
        if b64 is None:
            return None, None
        meta = {
            "id": "heatmap",
            "kind": "heatmap",
            "title": f"Per-face heatmap — {prop}",
            "property": prop,
            "width_px": int(window[0]),
            "height_px": int(window[1]),
        }
        return b64, meta
    except Exception:
        return None, None
    finally:
        if pl is not None:
            try:
                pl.close()
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
# public orchestrator


def _skip(status: str, note: str, face_count: int = 0) -> dict[str, Any]:
    return {
        "render_status": status,
        "images": {},
        "views": [],
        "face_count": int(face_count),
        "note": note,
    }


def build_views(
    shape: Any,
    *,
    max_section_views: int = 3,
    face_colormap: dict | None = None,
    window: tuple[int, int] = _DEFAULT_WINDOW,
    max_face_count: int | None = DEFAULT_MAX_FACE_COUNT,
) -> dict[str, Any]:
    """Render iso + section (+ optional heatmap) views of ``shape``.

    Args:
        shape: build123d body OR raw TopoDS_Shape.
        max_section_views: cap on the number of mid-plane section views (0-3).
        face_colormap: optional ``color_by_property`` extras dict → heatmap view.
        window: render window (px, px).
        max_face_count: face-count guard (``_face_count_guard`` default). A part
            above this SKIPS the render (status ``skipped_too_big``) BEFORE any
            tessellation so a 4000-face part never hangs the report.

    Returns the views dict documented at module top. Never raises — a missing GL
    context yields ``render_status='skipped_no_gl'`` and the report renders
    without images.
    """
    if shape is None:
        return _skip("empty", "no body to render")

    occ = _occt_shape(shape)

    # ── face-count guard BEFORE tessellation ────────────────────────────────────
    face_count = 0
    if max_face_count is not None:
        try:
            from phone_designer.skills._resolvers import _all_faces

            face_count = len(_all_faces(occ))
        except Exception:
            face_count = 0
        if face_count > max_face_count:
            return _skip(
                "skipped_too_big",
                f"face count {face_count} > {max_face_count} — render skipped",
                face_count,
            )

    # ── GL probe — degrade to a skip-marker, never crash the report ─────────────
    gl_ok, gl_note = probe_gl()
    if not gl_ok:
        return _skip("skipped_no_gl", gl_note, face_count)

    # ── tessellate once for the shaded views ────────────────────────────────────
    try:
        polydata = _polydata(occ)
    except Exception as exc:
        return _skip(
            "skipped_no_gl",
            f"tessellation/polydata failed: {type(exc).__name__}: {exc}",
            face_count,
        )

    images: dict[str, str] = {}
    views: list[dict[str, Any]] = []

    # (a) isometric shaded view
    iso = _render_iso(polydata, window)
    if iso:
        images["iso"] = iso
        views.append({
            "id": "iso", "kind": "iso", "title": "Isometric view",
            "width_px": int(window[0]), "height_px": int(window[1]),
        })

    # (b) section views (BRepAlgoAPI_Section outline overlaid on a ghosted solid)
    n_sec = max(0, min(int(max_section_views), 3))
    for (vid, title, origin, normal) in _default_section_planes(occ)[:n_sec]:
        polylines, edge_count, total_len = _section_polylines(occ, origin, normal)
        if edge_count == 0:
            continue
        b64 = _render_section(polydata, polylines, normal, window)
        if not b64:
            continue
        # |signed area| of the largest section loop for the badge / metadata.
        area = 0.0
        try:
            from phone_designer.skills.inspect._section_curve import recover_section_loop

            loop = recover_section_loop(occ, origin, normal)
            area = float(loop.get("area_mm2", 0.0) or 0.0)
        except Exception:
            area = 0.0
        images[vid] = b64
        views.append({
            "id": vid, "kind": "section", "title": title,
            "plane_origin": [round(c, 4) for c in origin],
            "plane_normal": [round(c, 4) for c in normal],
            "edge_count": int(edge_count),
            "section_length_mm": round(float(total_len), 4),
            "area_mm2": round(area, 4),
            "width_px": int(window[0]), "height_px": int(window[1]),
        })

    # (c) optional heatmap view
    if face_colormap:
        hb64, hmeta = _render_heatmap(occ, polydata, face_colormap, window)
        if hb64 and hmeta:
            images["heatmap"] = hb64
            views.append(hmeta)

    if not images:
        # GL was ok but nothing rendered (degenerate part) — honest skip.
        return _skip("empty", "no views produced (empty/degenerate body)", face_count)

    return {
        "render_status": "ok",
        "images": images,
        "views": views,
        "face_count": int(face_count),
        "note": "rendered " + ", ".join(images.keys()),
    }
