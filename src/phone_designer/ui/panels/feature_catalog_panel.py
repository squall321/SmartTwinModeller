"""FeatureCatalogPanel — tree-view of the RE feature catalog.

Renders one top-level node per family (holes, pockets, bosses, patterns,
ribs, lugs, sweep / loft / revolve features) and one child node per
detected feature. Selecting a feature in the tree fires the
``feature_selected(dict)`` Qt signal, which the host (typically the
:class:`InspectorMainWindow`) wires to a :class:`HighlightOverlay`
sitting on the 3D viewport.

Used by:
    - ``inspect/launch_ui_panel.py`` (CLI launcher)
    - ``cli.py``  → ``phone-designer inspect-re``
    - The Inspector window via ``InspectorMainWindow.attach_feature_catalog_panel``

The panel never owns the body — it consumes only the JSON-serialisable
``extras["feature_catalog"]`` dict produced by
:class:`phone_designer.skills.reverse_engineer.extract_feature_catalog.
ExtractFeatureCatalog`.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


# Ordered family list — controls the visual order in the tree.
_FAMILIES: list[tuple[str, str]] = [
    ("holes",            "Holes"),
    ("pockets",          "Pockets"),
    ("bosses",           "Bosses"),
    ("ribs",             "Ribs"),
    ("lugs",             "Lugs"),
    ("standoffs",        "Standoffs"),
    ("patterns",         "Patterns"),
    ("symmetries",       "Symmetries"),
    ("sweep_features",   "Sweep features"),
    ("loft_features",    "Loft features"),
    ("revolve_features", "Revolve features"),
    ("standard_matches", "Standard hole matches"),
]

# Pleasant family colors for the tree label.
_FAMILY_COLORS = {
    "holes":            "#c0392b",
    "pockets":          "#d35400",
    "bosses":           "#27ae60",
    "ribs":             "#8e44ad",
    "lugs":             "#16a085",
    "standoffs":        "#2c3e50",
    "patterns":         "#f39c12",
    "symmetries":       "#7f8c8d",
    "sweep_features":   "#2980b9",
    "loft_features":    "#9b59b6",
    "revolve_features": "#e67e22",
    "standard_matches": "#34495e",
}


def _short_label(family: str, idx: int, feat: dict[str, Any]) -> str:
    """Render a one-line summary for a feature row."""
    # Prefer "id" if present
    fid = feat.get("id", idx)
    parts = [f"{family[:-1] if family.endswith('s') else family} #{fid}"]
    if "diameter_mm" in feat:
        parts.append(f"d={feat['diameter_mm']:.2f}mm")
    if "profile_diameter_mm" in feat:
        parts.append(f"d≈{feat['profile_diameter_mm']:.2f}mm")
    if "depth_mm" in feat:
        parts.append(f"depth={feat['depth_mm']:.2f}mm")
    if "height_mm" in feat:
        parts.append(f"h={feat['height_mm']:.2f}mm")
    if "kind" in feat:
        parts.append(f"[{feat['kind']}]")
    if "thread_spec" in feat:
        parts.append(f"thread={feat['thread_spec']}")
    if "count" in feat:
        parts.append(f"n={feat['count']}")
    return "  ·  ".join(parts)


class FeatureCatalogPanel(QWidget):
    """Tree view of an ``extras["feature_catalog"]`` dict.

    Signals:
        feature_selected(dict):
            Emitted whenever the user selects a feature row in the tree.
            The payload is the original feature dict, augmented with two
            convenience keys::

                {"_family": "holes",   # parent family name
                 "_index":  3,         # 0-based index inside family
                 ... original feature fields ...}

        cleared():
            Emitted when ``set_catalog(None)`` is called or the user clicks
            the "Clear" button.
    """

    feature_selected = Signal(dict)
    cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._catalog: dict[str, Any] | None = None
        self._build_ui()

    # ── ui ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        header = QHBoxLayout()
        title = QLabel("<b>Feature Catalog</b>")
        header.addWidget(title)
        header.addStretch(1)
        self._summary_label = QLabel("(no catalog loaded)")
        self._summary_label.setStyleSheet("color: #555;")
        header.addWidget(self._summary_label)
        root.addLayout(header)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Feature", "Details"])
        self.tree.setColumnCount(2)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self.tree, stretch=1)

        # Footer — Expand / Collapse / Clear
        footer = QHBoxLayout()
        btn_exp = QPushButton("Expand all")
        btn_exp.clicked.connect(self.tree.expandAll)
        footer.addWidget(btn_exp)

        btn_col = QPushButton("Collapse all")
        btn_col.clicked.connect(self.tree.collapseAll)
        footer.addWidget(btn_col)

        footer.addStretch(1)

        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self._on_clear_clicked)
        footer.addWidget(btn_clear)

        root.addLayout(footer)

    # ── public api ────────────────────────────────────────────────────────

    def set_catalog(self, catalog: dict[str, Any] | None) -> None:
        """Replace the displayed catalog. ``None`` clears the panel."""
        self._catalog = catalog or None
        self.tree.clear()

        if not catalog:
            self._summary_label.setText("(no catalog loaded)")
            self.cleared.emit()
            return

        total = 0
        for family_key, family_label in _FAMILIES:
            entries = catalog.get(family_key) or []
            if not isinstance(entries, list) or not entries:
                continue

            parent = QTreeWidgetItem(self.tree)
            parent.setText(0, f"{family_label}  ({len(entries)})")
            parent.setText(1, "")
            font = QFont()
            font.setBold(True)
            parent.setFont(0, font)
            color = _FAMILY_COLORS.get(family_key)
            if color:
                parent.setForeground(0, QColor(color))
            parent.setData(0, Qt.UserRole, {"_family": family_key,
                                            "_is_family_header": True})

            for i, feat in enumerate(entries):
                if not isinstance(feat, dict):
                    # tolerate exotic shapes (e.g., symmetry tuples)
                    feat = {"raw": feat}

                child = QTreeWidgetItem(parent)
                child.setText(0, _short_label(family_key, i, feat))
                # Detail col → compact key=value of remaining fields.
                detail = _detail_string(feat)
                child.setText(1, detail)

                payload = dict(feat)
                payload["_family"] = family_key
                payload["_index"] = i
                child.setData(0, Qt.UserRole, payload)
                total += 1

            self.tree.addTopLevelItem(parent)
            parent.setExpanded(True)

        base_thick = catalog.get("base_thickness_mm")
        if base_thick is not None:
            self._summary_label.setText(
                f"{total} features  ·  base thickness ≈ {base_thick:.2f} mm"
            )
        else:
            self._summary_label.setText(f"{total} features")

        # Auto-size columns
        for c in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(c)

    def selected_feature(self) -> dict[str, Any] | None:
        """Return the dict payload of the currently selected feature (or None)."""
        items = self.tree.selectedItems()
        if not items:
            return None
        data = items[0].data(0, Qt.UserRole)
        if isinstance(data, dict) and not data.get("_is_family_header"):
            return data
        return None

    # ── internals ─────────────────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        feat = self.selected_feature()
        if feat is not None:
            self.feature_selected.emit(feat)

    def _on_clear_clicked(self) -> None:
        self.set_catalog(None)


def _detail_string(feat: dict[str, Any]) -> str:
    """One-line  ``key=value`` formatting of the *interesting* feature fields.

    Skips bulky / structural keys (bbox, path_points, center) which are
    already visually represented via the highlight overlay.
    """
    SKIP = {"id", "kind", "bbox", "path_points", "center", "center_xy",
             "diameter_mm", "depth_mm", "height_mm", "profile_diameter_mm",
             "thread_spec", "count", "_family", "_index", "_is_family_header"}
    bits: list[str] = []
    for k, v in feat.items():
        if k in SKIP or k.startswith("_"):
            continue
        if isinstance(v, float):
            bits.append(f"{k}={v:.3f}")
        elif isinstance(v, (int, str, bool)) or v is None:
            bits.append(f"{k}={v}")
        elif isinstance(v, (list, tuple)) and len(v) <= 4:
            bits.append(f"{k}={list(v)}")
        else:
            bits.append(f"{k}=…")
    return "  ".join(bits[:8])
