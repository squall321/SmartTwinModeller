"""HighlightOverlay — draw translucent colored markers on a pyvista plotter.

Used by :class:`phone_designer.ui.panels.feature_catalog_panel.
FeatureCatalogPanel` to highlight the face / edge / bbox of a feature
when the user clicks it in the catalog tree.

The overlay is intentionally **decoupled** from the underlying body
tessellation. We do not try to extract the exact OCCT face from the
catalog entry (that would require carrying the live ``TopoDS_Shape``
along with the catalog dict). Instead the overlay accepts the
feature's bbox + center + (optionally) a normal, and renders a simple
sphere / arrow / bounding-cube marker. This keeps the catalog dict
fully JSON-serialisable while still giving the user a clear visual.

API::

    overlay = HighlightOverlay(plotter)
    overlay.show_feature({
        "kind": "hole",
        "center": [10.0, 5.0, 0.0],
        "normal": [0, 0, 1],         # optional
        "diameter_mm": 3.2,          # optional → sphere radius hint
        "bbox": [xmin, ymin, zmin, xmax, ymax, zmax],   # optional
    }, color="red")
    overlay.clear()
"""
from __future__ import annotations

from typing import Any

import numpy as np

try:  # pragma: no cover — pyvista is an optional UI dep
    import pyvista as pv
except Exception:  # pragma: no cover
    pv = None  # type: ignore[assignment]


# Default color per feature kind. Used when caller omits the ``color`` kwarg.
_KIND_COLORS = {
    "hole":   "red",
    "pocket": "orange",
    "boss":   "lime",
    "rib":    "magenta",
    "lug":    "cyan",
    "pattern": "yellow",
    "sweep":  "deepskyblue",
    "loft":   "violet",
    "revolve": "gold",
}


class HighlightOverlay:
    """Translucent overlay for highlighting a single feature on the plotter.

    Owns at most one set of pyvista actors at a time. Calling ``show_feature``
    replaces the previous highlight. ``clear()`` removes everything.

    Parameters:
        plotter:
            A ``pyvista.Plotter`` or ``pyvistaqt.QtInteractor`` instance.
            Any object with ``add_mesh``, ``remove_actor`` and ``render`` is
            accepted (we only call those three methods).
    """

    def __init__(self, plotter: Any) -> None:
        self._plotter = plotter
        self._actors: list[Any] = []

    # ── public api ────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Remove every actor this overlay has added to the plotter."""
        for a in self._actors:
            try:
                self._plotter.remove_actor(a)
            except Exception:
                # plotter may have been closed already; ignore
                pass
        self._actors.clear()
        try:
            self._plotter.render()
        except Exception:
            pass

    def show_feature(
        self,
        feature: dict[str, Any],
        *,
        color: str | None = None,
        opacity: float = 0.45,
    ) -> None:
        """Render a translucent marker for ``feature``.

        ``feature`` is one entry from ``extras["feature_catalog"]`` —
        a hole / pocket / boss / pattern / sweep / loft / revolve dict.
        Unknown keys are tolerated; the overlay uses whichever of
        ``center``, ``bbox``, ``normal``, ``diameter_mm`` are present.
        """
        if pv is None:
            return  # pyvista unavailable — silently no-op

        self.clear()

        kind = str(feature.get("kind") or feature.get("type") or "feature").lower()
        col = color or _KIND_COLORS.get(kind, "red")

        # 1) bbox cube — drawn as wireframe so it doesn't obscure the body
        bbox = feature.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 6:
            try:
                xmin, ymin, zmin, xmax, ymax, zmax = (float(v) for v in bbox)
                if xmax > xmin and ymax > ymin and zmax > zmin:
                    cube = pv.Box(bounds=(xmin, xmax, ymin, ymax, zmin, zmax))
                    actor = self._plotter.add_mesh(
                        cube, color=col, opacity=opacity,
                        style="wireframe", line_width=2.5,
                        name=f"highlight_bbox_{id(self)}",
                    )
                    self._actors.append(actor)
            except Exception:
                pass

        # 2) center sphere — sized by diameter_mm if available, else 1mm
        center = feature.get("center") or feature.get("center_xy")
        if isinstance(center, (list, tuple)):
            try:
                if len(center) == 2:
                    cx, cy = float(center[0]), float(center[1])
                    cz = float(feature.get("anchor_z", 0.0))
                else:
                    cx, cy, cz = (float(v) for v in center[:3])
                d = float(feature.get("diameter_mm")
                          or feature.get("profile_diameter_mm")
                          or 2.0)
                r = max(d * 0.5, 0.5)
                sphere = pv.Sphere(radius=r, center=(cx, cy, cz))
                actor = self._plotter.add_mesh(
                    sphere, color=col, opacity=opacity,
                    name=f"highlight_center_{id(self)}",
                )
                self._actors.append(actor)
            except Exception:
                pass

        # 3) normal arrow — useful for showing hole / pocket orientation
        normal = feature.get("normal") or feature.get("normal_at_center")
        if (isinstance(normal, (list, tuple)) and len(normal) == 3
                and isinstance(center, (list, tuple)) and len(center) >= 2):
            try:
                if len(center) == 2:
                    cx, cy = float(center[0]), float(center[1])
                    cz = float(feature.get("anchor_z", 0.0))
                else:
                    cx, cy, cz = (float(v) for v in center[:3])
                nx, ny, nz = (float(v) for v in normal)
                mag = float(np.linalg.norm([nx, ny, nz]))
                if mag > 1e-6:
                    scale = max(
                        float(feature.get("depth_mm")
                              or feature.get("height_mm")
                              or 5.0),
                        2.0,
                    )
                    arrow = pv.Arrow(
                        start=(cx, cy, cz),
                        direction=(nx / mag, ny / mag, nz / mag),
                        scale=scale,
                    )
                    actor = self._plotter.add_mesh(
                        arrow, color=col, opacity=opacity,
                        name=f"highlight_normal_{id(self)}",
                    )
                    self._actors.append(actor)
            except Exception:
                pass

        try:
            self._plotter.render()
        except Exception:
            pass

    # ── convenience ───────────────────────────────────────────────────────

    @property
    def actors(self) -> list[Any]:
        """Return the live actor list (mostly for tests)."""
        return list(self._actors)
