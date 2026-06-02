"""launch_ui_panel — atomic, read-only.

Convenience skill: bring up the Inspector window with one (or both) of
the RE-visualisation panels attached:

  * ``catalog``   — :class:`phone_designer.ui.panels.feature_catalog_panel.
                    FeatureCatalogPanel`
  * ``plan_diff`` — :class:`phone_designer.ui.panels.plan_diff_panel.
                    PlanDiffPanel`
  * ``both``      — both, docked left/right of the 3D viewport.

The skill expects the host plan to already have populated the
``extras["feature_catalog"]`` (typically by running
``extract_feature_catalog`` upstream in the plan). The catalog is read
from ``self.extras_in`` if available, or freshly extracted from the
current body if not.

Args:
    panel:    which dock panel(s) to attach.
    plan_a:   optional plan YAML for left side of the diff panel.
    plan_b:   optional plan YAML for right side of the diff panel.
    headless: if True (test mode) just construct the window without
              entering the Qt event loop. Default ``False``.

Returns the body unchanged. Diagnostic metadata about the launch lands
in ``extras["ui_panel_launch"]``::

    {"panel": "both", "headless": True,
     "catalog_features": 17, "plan_a": "...", "plan_b": "...",
     "error": None}

post_condition: ``body_present`` (read-only).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# Catalog loader (inline per project convention)
def _load(family, name):
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parents[5]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


def _detect_headless() -> bool:
    """Return True when no display is available — CI, ssh w/o X, etc.

    On Windows we always assume a display unless ``PHONE_DESIGNER_UI_HEADLESS``
    is set. On Linux we check ``$DISPLAY`` / ``$WAYLAND_DISPLAY``. On Darwin
    we trust the runtime to surface UI.
    """
    if os.environ.get("PHONE_DESIGNER_UI_HEADLESS"):
        return True
    if os.name == "nt":
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


@skill(
    name="launch_ui_panel",
    category="inspect",
    level="atomic",
    summary="Launch the Inspector UI with the feature_catalog and/or plan_diff "
            "dock panel(s) attached. Read-only — body is returned unchanged. "
            "Set headless=True to construct the window without entering the "
            "Qt event loop (used by tests).",
    selector_kinds=[],
    history_rules={},
    produces_features=["ui_panel_launch_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.01,
    post_conditions=[PostCondition(kind="body_present")],
)
class LaunchUiPanel(SkillBase):
    class Args(BaseModel):
        panel: Literal["catalog", "plan_diff", "both"] = "both"
        plan_a: str | None = None
        plan_b: str | None = None
        headless: bool = False

    def _apply(self, body: Any, args: Args) -> SkillResult:
        report: dict[str, Any] = {
            "panel": args.panel,
            "plan_a": args.plan_a,
            "plan_b": args.plan_b,
            "headless": bool(args.headless),
            "catalog_features": 0,
            "error": None,
        }

        # Headless override — if no display is available always force headless
        # regardless of caller arg. Tests still get window construction.
        effective_headless = bool(args.headless) or _detect_headless()
        report["headless"] = effective_headless

        # Catalog source: if upstream populated extras_in or feature_catalog
        # on the body, use it; otherwise run extract_feature_catalog on the
        # current body.
        catalog: dict[str, Any] | None = None
        if body is not None and args.panel in ("catalog", "both"):
            try:
                from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
                    ExtractFeatureCatalog,
                )
                cat_res = ExtractFeatureCatalog().apply(body, {})
                catalog = cat_res.extras.get("feature_catalog")
            except Exception as exc:
                report["error"] = (
                    f"feature_catalog extraction failed: "
                    f"{type(exc).__name__}: {exc}"
                )

        if catalog is not None:
            n = sum(
                len(catalog.get(k) or [])
                for k in (
                    "holes", "pockets", "bosses", "ribs", "lugs",
                    "patterns", "standoffs", "sweep_features",
                    "loft_features", "revolve_features",
                )
            )
            report["catalog_features"] = n

        try:
            from phone_designer.ui.inspector_main import (
                InspectorMainWindow, run_inspector_with_panels,
            )
        except Exception as exc:
            report["error"] = (
                f"inspector import failed: {type(exc).__name__}: {exc}"
            )
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={"ui_panel_launch": report},
            )

        try:
            if effective_headless:
                # Construct the window but don't enter the event loop.
                # This is the path used by unit tests + import-only smoke tests.
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance() or QApplication([])  # noqa: F841
                win = InspectorMainWindow()
                win.attach_re_panels(
                    panel=args.panel,
                    catalog=catalog,
                    plan_a=Path(args.plan_a) if args.plan_a else None,
                    plan_b=Path(args.plan_b) if args.plan_b else None,
                )
                # Don't show() — test path
                report["window_constructed"] = True
            else:
                run_inspector_with_panels(
                    panel=args.panel,
                    catalog=catalog,
                    plan_a=Path(args.plan_a) if args.plan_a else None,
                    plan_b=Path(args.plan_b) if args.plan_b else None,
                )
                report["window_constructed"] = True
        except Exception as exc:
            report["error"] = (
                f"window launch failed: {type(exc).__name__}: {exc}"
            )

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"ui_panel_launch": report},
        )
