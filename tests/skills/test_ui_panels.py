"""Smoke tests for the RE UI panels.

These tests construct the widgets without entering the Qt event loop. They
verify the import graph is intact and the widgets can be populated with
representative payloads. Skipped on CI / headless machines where no display
server is available.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# Skip the entire module if no display is available. PySide6 / pyvistaqt
# both crash hard at import time when QPA cannot find a platform plugin,
# so we have to guard at the module level.
def _has_display() -> bool:
    if os.environ.get("PHONE_DESIGNER_UI_HEADLESS"):
        return False
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or
                     os.environ.get("WAYLAND_DISPLAY"))
    # Windows / Darwin — always assume display unless override is set
    return True


pytestmark = pytest.mark.skipif(
    not _has_display(),
    reason="UI smoke test requires a display (set PHONE_DESIGNER_UI_HEADLESS to skip)",
)


@pytest.fixture(scope="module")
def qapp():
    """Provide a singleton QApplication for the whole test module."""
    pyside = pytest.importorskip("PySide6.QtWidgets")
    app = pyside.QApplication.instance() or pyside.QApplication([])
    yield app


# ── widget imports ──────────────────────────────────────────────────────


def test_imports_resolve():
    """The panel + widget + skill modules must import cleanly."""
    from phone_designer.ui.panels.feature_catalog_panel import (  # noqa: F401
        FeatureCatalogPanel,
    )
    from phone_designer.ui.panels.plan_diff_panel import PlanDiffPanel  # noqa: F401
    from phone_designer.ui.widgets.highlight_overlay import (  # noqa: F401
        HighlightOverlay,
    )
    from phone_designer.skills.inspect.launch_ui_panel import (  # noqa: F401
        LaunchUiPanel,
    )


# ── feature catalog panel ───────────────────────────────────────────────


def test_feature_catalog_panel_constructs_empty(qapp):
    from phone_designer.ui.panels.feature_catalog_panel import FeatureCatalogPanel

    panel = FeatureCatalogPanel()
    assert panel.tree.topLevelItemCount() == 0
    panel.set_catalog(None)
    assert panel.tree.topLevelItemCount() == 0


def test_feature_catalog_panel_populates_tree(qapp):
    from phone_designer.ui.panels.feature_catalog_panel import FeatureCatalogPanel

    catalog = {
        "holes": [
            {"id": 0, "center": [0.0, 0.0, 0.0], "diameter_mm": 3.2,
             "depth_mm": 5.0, "kind": "through"},
            {"id": 1, "center": [10.0, 0.0, 0.0], "diameter_mm": 4.0,
             "depth_mm": 6.0, "kind": "blind"},
        ],
        "pockets": [
            {"id": 0, "center": [5.0, 5.0, 0.0], "depth_mm": 2.0,
             "bbox": [0, 0, 0, 10, 10, 2]},
        ],
        "bosses": [],
        "patterns": [
            {"id": 0, "kind": "linear", "count": 4},
        ],
        "base_thickness_mm": 1.5,
    }
    panel = FeatureCatalogPanel()
    panel.set_catalog(catalog)

    # holes (2) + pockets (1) + patterns (1) = 3 top-level family rows
    assert panel.tree.topLevelItemCount() == 3
    # Sum of children matches total feature count
    total_children = sum(
        panel.tree.topLevelItem(i).childCount()
        for i in range(panel.tree.topLevelItemCount())
    )
    assert total_children == 4


def test_feature_catalog_panel_emits_selection_signal(qapp):
    from phone_designer.ui.panels.feature_catalog_panel import FeatureCatalogPanel

    catalog = {"holes": [{"id": 42, "center": [1, 2, 3], "diameter_mm": 5.0}]}
    panel = FeatureCatalogPanel()
    panel.set_catalog(catalog)

    received: list[dict] = []
    panel.feature_selected.connect(received.append)

    # Select the first child of the first top-level item
    hole_parent = panel.tree.topLevelItem(0)
    assert hole_parent is not None
    assert hole_parent.childCount() == 1
    child = hole_parent.child(0)
    panel.tree.setCurrentItem(child)

    assert len(received) == 1
    assert received[0]["_family"] == "holes"
    assert received[0]["_index"] == 0
    assert received[0]["diameter_mm"] == 5.0


# ── plan diff panel ─────────────────────────────────────────────────────


def test_plan_diff_panel_constructs_empty(qapp):
    from phone_designer.ui.panels.plan_diff_panel import PlanDiffPanel

    panel = PlanDiffPanel()
    panel.set_plans(None, None)
    # No exceptions = pass; left/right are empty.
    assert panel._left_text.toPlainText() == ""
    assert panel._right_text.toPlainText() == ""


def test_plan_diff_panel_renders_two_plans(qapp, tmp_path: Path):
    from phone_designer.ui.panels.plan_diff_panel import PlanDiffPanel

    a = tmp_path / "a.yaml"
    a.write_text(
        "plan_name: a\nsteps:\n"
        "  - id: s1\n    skill: create.box\n    args: {length_mm: 10}\n"
        "  - id: s2\n    skill: modify_pocket.hole\n    args: {diameter_mm: 3.2}\n",
        encoding="utf-8",
    )
    b = tmp_path / "b.yaml"
    b.write_text(
        "plan_name: b\nsteps:\n"
        "  - id: s1\n    skill: create.box\n    args: {length_mm: 12}\n"
        "  - id: s3\n    skill: modify_boss.rib\n    args: {height_mm: 2.0}\n",
        encoding="utf-8",
    )

    panel = PlanDiffPanel()
    panel.set_plans(a, b)

    left = panel._left_text.toPlainText()
    right = panel._right_text.toPlainText()
    assert "step: s1" in left
    assert "step: s2" in left
    assert "step: s1" in right
    assert "step: s3" in right


# ── highlight overlay ───────────────────────────────────────────────────


def test_highlight_overlay_clear_is_safe_without_plotter(qapp):
    from phone_designer.ui.widgets.highlight_overlay import HighlightOverlay

    class _DummyPlotter:
        def __init__(self):
            self.calls = []

        def add_mesh(self, *a, **k):
            self.calls.append(("add_mesh", a, k))
            return object()

        def remove_actor(self, actor):
            self.calls.append(("remove_actor", actor))

        def render(self):
            self.calls.append(("render",))

    plot = _DummyPlotter()
    overlay = HighlightOverlay(plot)
    overlay.clear()  # no-op when empty
    overlay.show_feature({
        "kind": "hole",
        "center": [0.0, 0.0, 0.0],
        "diameter_mm": 3.0,
        "bbox": [-1, -1, -1, 1, 1, 1],
        "normal": [0, 0, 1],
        "depth_mm": 5.0,
    })
    # At minimum the plotter received some add_mesh calls
    add_calls = [c for c in plot.calls if c[0] == "add_mesh"]
    assert len(add_calls) >= 1
    overlay.clear()
    assert overlay.actors == []


# ── launch_ui_panel skill (headless mode) ───────────────────────────────


def test_launch_ui_panel_headless_plan_diff(qapp, tmp_path: Path):
    """LaunchUiPanel(panel='plan_diff', headless=True) constructs the window."""
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.inspect.launch_ui_panel import LaunchUiPanel

    body = Box().apply(None, {
        "length_mm": 20.0, "width_mm": 10.0, "height_mm": 5.0,
    }).body

    a = tmp_path / "a.yaml"
    a.write_text("plan_name: a\nsteps: []\n", encoding="utf-8")
    b = tmp_path / "b.yaml"
    b.write_text("plan_name: b\nsteps: []\n", encoding="utf-8")

    res = LaunchUiPanel().apply(body, {
        "panel": "plan_diff",
        "plan_a": str(a),
        "plan_b": str(b),
        "headless": True,
    })
    report = res.extras["ui_panel_launch"]
    assert report["panel"] == "plan_diff"
    assert report["headless"] is True
    assert res.body is body  # body returned unchanged


def test_launch_ui_panel_headless_both_panels(qapp):
    """LaunchUiPanel(panel='both', headless=True) constructs window + catalog."""
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.inspect.launch_ui_panel import LaunchUiPanel

    body = Box().apply(None, {
        "length_mm": 30.0, "width_mm": 20.0, "height_mm": 4.0,
    }).body

    res = LaunchUiPanel().apply(body, {"panel": "both", "headless": True})
    report = res.extras["ui_panel_launch"]
    assert report["panel"] == "both"
    assert report["headless"] is True
    # catalog_features may be 0 (empty box) but should be a present int
    assert isinstance(report.get("catalog_features"), int)
