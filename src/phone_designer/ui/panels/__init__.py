"""Inspector dock panels — RE catalog tree + plan diff side-by-side.

These widgets are designed to be embedded in :class:`phone_designer.ui.
inspector_main.InspectorMainWindow` via QDockWidget or as standalone
top-level windows. They have no hard dependency on the inspector window
itself so they can be unit-tested in isolation.
"""
from phone_designer.ui.panels.feature_catalog_panel import FeatureCatalogPanel
from phone_designer.ui.panels.plan_diff_panel import PlanDiffPanel

__all__ = ["FeatureCatalogPanel", "PlanDiffPanel"]
