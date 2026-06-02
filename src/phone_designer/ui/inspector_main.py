"""Inspector main window — Phase 4 의 첫 본격 UI.

Layout (P0 의 minimal scope):
  ┌──────────────────────────────────────────────────┐
  │ File  View  Plan  Help                           │
  ├──────────────┬───────────────────────────────────┤
  │ Step list    │                                   │
  │              │       VTK Viewport                │
  │ - s1 ✓       │       (pyvistaqt)                 │
  │ - s2 ✓       │                                   │
  │ - s3 ✗       │                                   │
  │ - s4 (skip)  │                                   │
  │              │                                   │
  │ [▢ Compare]  │                                   │
  │ ref: ...     │                                   │
  ├──────────────┴───────────────────────────────────┤
  │ Status: plan OK | duration 1.2s | step 2/4       │
  └──────────────────────────────────────────────────┘

플랜 실행 = 백그라운드 thread 또는 즉시. 현재는 즉시 (small plans).
각 step 의 SkillResult.body → tessellate → cache. step 클릭 = viewport 갱신.

본 모듈은 [[ui]] spec 의 단계적 구현 시작점.
다음 P1 항목: Component palette, Plan editor (drag-reorder), Chat panel, DFM panel, Test 메뉴.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
import pyvista as pv
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)
from pyvistaqt import QtInteractor


# ──────────────────────────────────────────────────────────────────────────────
# Helpers


def _step_to_polydata(part_or_shape) -> pv.PolyData:
    """SkillResult.body 또는 OCCT shape → pyvista PolyData."""
    from phone_designer.logging.viewport_capture import _shape_to_polydata
    shape = getattr(part_or_shape, "wrapped", part_or_shape)
    return _shape_to_polydata(shape)


def _load_step_polydata(step_path: Path) -> pv.PolyData:
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from phone_designer.logging.viewport_capture import _shape_to_polydata

    reader = STEPControl_Reader()
    status = reader.ReadFile(str(step_path))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"STEP read failed: {step_path}")
    reader.TransferRoots()
    return _shape_to_polydata(reader.OneShape())


# ──────────────────────────────────────────────────────────────────────────────
# Main window


class InspectorMainWindow(QMainWindow):
    """Plan inspector + viewport.

    Methods:
        load_plan(path):     plan YAML 실행 + step list / viewport 채움
        load_step(path):     단일 STEP 표시
        set_reference(path): 비교용 reference STEP (반투명 빨강 overlay)
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Phone Designer — Inspector")
        self.resize(1400, 900)

        self._plan = None                            # phone_designer.plan.model.Plan
        self._step_results: dict[str, object] = {}   # step_id → SkillResult
        self._step_meshes: dict[str, pv.PolyData] = {}
        self._reference_mesh: pv.PolyData | None = None
        self._reference_actor = None
        self._current_actor = None

        self._build_ui()
        self._build_menus()
        self.statusBar().showMessage("Ready. File → Open Plan / Open STEP.")

    # ── UI 구성 ──────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        # ── 좌측: step list + compare panel ──
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(6, 6, 6, 6)

        left_layout.addWidget(QLabel("<b>Plan Steps</b>"))
        self.step_list = QListWidget()
        self.step_list.setSelectionMode(QListWidget.SingleSelection)
        self.step_list.currentItemChanged.connect(self._on_step_selected)
        left_layout.addWidget(self.step_list, stretch=1)

        # Compare panel
        left_layout.addWidget(QLabel("<b>Compare</b>"))
        self.compare_toggle = QCheckBox("Show reference (semi-transparent red)")
        self.compare_toggle.toggled.connect(self._refresh_viewport)
        left_layout.addWidget(self.compare_toggle)

        self.reference_label = QLabel("ref: (none)")
        self.reference_label.setWordWrap(True)
        left_layout.addWidget(self.reference_label)

        ref_btn = QPushButton("Set Reference STEP...")
        ref_btn.clicked.connect(self._on_set_reference)
        left_layout.addWidget(ref_btn)

        left.setMinimumWidth(260)
        splitter.addWidget(left)

        # ── 우측: viewport ──
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.plotter = QtInteractor(right)
        self.plotter.set_background("white")
        self.plotter.add_axes()
        right_layout.addWidget(self.plotter.interactor)
        splitter.addWidget(right)

        splitter.setSizes([300, 1100])

        self.setStatusBar(QStatusBar())

    def _build_menus(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        a_open_plan = QAction("Open Plan (YAML)...", self)
        a_open_plan.setShortcut(QKeySequence("Ctrl+O"))
        a_open_plan.triggered.connect(self._on_open_plan)
        file_menu.addAction(a_open_plan)

        a_open_step = QAction("Open STEP...", self)
        a_open_step.triggered.connect(self._on_open_step)
        file_menu.addAction(a_open_step)

        a_set_ref = QAction("Set Reference STEP...", self)
        a_set_ref.triggered.connect(self._on_set_reference)
        file_menu.addAction(a_set_ref)

        file_menu.addSeparator()
        a_quit = QAction("Quit", self)
        a_quit.setShortcut(QKeySequence("Ctrl+Q"))
        a_quit.triggered.connect(self.close)
        file_menu.addAction(a_quit)

        view_menu = mb.addMenu("&View")
        a_iso = QAction("Isometric", self); a_iso.triggered.connect(lambda: self._set_view("iso"))
        view_menu.addAction(a_iso)
        for v in ("xy", "yz", "xz"):
            act = QAction(f"View {v.upper()}", self)
            act.triggered.connect(lambda checked=False, vv=v: self._set_view(vv))
            view_menu.addAction(act)
        view_menu.addSeparator()
        a_reset = QAction("Reset Camera", self); a_reset.setShortcut(QKeySequence("R"))
        a_reset.triggered.connect(lambda: self.plotter.reset_camera())
        view_menu.addAction(a_reset)

        plan_menu = mb.addMenu("&Plan")
        a_rerun = QAction("Re-execute Current Plan", self)
        a_rerun.setShortcut(QKeySequence("F5"))
        a_rerun.triggered.connect(self._on_rerun_plan)
        plan_menu.addAction(a_rerun)

    # ── 액션 핸들러 ──────────────────────────────────────────────────────

    def _on_open_plan(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open plan YAML", "plans", "Plans (*.yaml *.yml)"
        )
        if path:
            self.load_plan(Path(path))

    def _on_open_step(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open STEP", "fixtures", "STEP (*.step *.stp)"
        )
        if path:
            self.load_step(Path(path))

    def _on_set_reference(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open reference STEP", "fixtures", "STEP (*.step *.stp)"
        )
        if path:
            self.set_reference(Path(path))

    def _on_rerun_plan(self):
        if self._plan is None:
            QMessageBox.information(self, "Re-execute", "현재 로드된 plan 없음.")
            return
        self.load_plan(self._plan_path)

    def _on_step_selected(self, current, previous):
        if current is None:
            return
        step_id = current.data(Qt.UserRole)
        self._show_step(step_id)

    # ── Plan / STEP 로드 ────────────────────────────────────────────────

    def load_plan(self, plan_path: Path):
        """plan YAML 실행 + 좌측 step list / step 별 mesh cache."""
        from phone_designer.plan.executor import PlanExecutor
        from phone_designer.plan.yaml_io import load_plan

        self._plan_path = plan_path
        try:
            self._plan = load_plan(plan_path)
        except Exception as e:
            self._show_error("Plan 로드 실패", e)
            return

        self.setWindowTitle(f"Phone Designer — Inspector — {plan_path.name}")
        self.statusBar().showMessage(f"Executing plan: {plan_path.name}")
        QApplication.processEvents()

        # Executor 실행
        try:
            self._executor_result = PlanExecutor(self._plan).run()
        except Exception as e:
            self._show_error("Plan 실행 중 예외", e)
            return

        # step → mesh 캐싱
        self._step_results = dict(self._executor_result.step_results)
        self._step_meshes.clear()
        for step_id, skill_result in self._step_results.items():
            try:
                self._step_meshes[step_id] = _step_to_polydata(skill_result.body)
            except Exception as exc:
                # tessellation 실패 — 이 step skip
                self.statusBar().showMessage(
                    f"[warn] step {step_id} mesh 변환 실패: {exc}", 5000
                )

        # step list 갱신
        self.step_list.clear()
        for step in self._plan.steps:
            marker = {"pass": "✓", "fail": "✗", "skipped": "—",
                       "pending": "?"}.get(step.status.value, "?")
            item = QListWidgetItem(f"{marker}  {step.id}  {step.skill}")
            item.setData(Qt.UserRole, step.id)
            if step.status.value == "fail":
                item.setForeground(QColor("#c0392b"))
                if step.failure:
                    item.setToolTip(
                        f"{step.failure.error_type}: {step.failure.message}"
                    )
            elif step.status.value == "skipped":
                item.setForeground(QColor("#888"))
            self.step_list.addItem(item)

        # 마지막 PASS step 선택
        last_pass = None
        for i in range(self.step_list.count()):
            item = self.step_list.item(i)
            step_id = item.data(Qt.UserRole)
            step = self._plan.find_step(step_id)
            if step and step.status.value == "pass":
                last_pass = i
        if last_pass is not None:
            self.step_list.setCurrentRow(last_pass)
        else:
            self.statusBar().showMessage(
                f"Plan {self._executor_result.outcome} — no PASS step to show", 0
            )

        # status bar
        n_pass = sum(1 for s in self._plan.steps if s.status.value == "pass")
        n_fail = sum(1 for s in self._plan.steps if s.status.value == "fail")
        self.statusBar().showMessage(
            f"Plan {self._executor_result.outcome} | "
            f"{n_pass} pass, {n_fail} fail | "
            f"duration {self._executor_result.error_count} errors"
        )

    def load_step(self, step_path: Path):
        """단일 STEP 표시 (plan 없이)."""
        try:
            mesh = _load_step_polydata(step_path)
        except Exception as e:
            self._show_error("STEP 로드 실패", e)
            return
        self.setWindowTitle(f"Phone Designer — Inspector — {step_path.name}")
        self._show_mesh(mesh, label=step_path.name)
        self.statusBar().showMessage(
            f"STEP loaded: {mesh.n_points} verts, {mesh.n_cells} faces"
        )

    def set_reference(self, ref_path: Path):
        try:
            self._reference_mesh = _load_step_polydata(ref_path)
        except Exception as e:
            self._show_error("Reference 로드 실패", e)
            return
        self.reference_label.setText(f"ref: {ref_path.name}")
        self.compare_toggle.setChecked(True)
        self._refresh_viewport()

    # ── Viewport 갱신 ───────────────────────────────────────────────────

    def _show_step(self, step_id: str):
        mesh = self._step_meshes.get(step_id)
        if mesh is None:
            self.statusBar().showMessage(f"step {step_id}: mesh 없음", 3000)
            return
        self._show_mesh(mesh, label=f"step {step_id}")

    def _show_mesh(self, mesh: pv.PolyData, label: str = ""):
        if self._current_actor is not None:
            self.plotter.remove_actor(self._current_actor)
        self._current_actor = self.plotter.add_mesh(
            mesh, color="lightblue", show_edges=True, edge_color="gray",
            name="current",
        )
        self._refresh_overlay()
        self.plotter.reset_camera()
        if label:
            self.statusBar().showMessage(label, 3000)

    def _refresh_overlay(self):
        if self._reference_actor is not None:
            self.plotter.remove_actor(self._reference_actor)
            self._reference_actor = None
        if self.compare_toggle.isChecked() and self._reference_mesh is not None:
            self._reference_actor = self.plotter.add_mesh(
                self._reference_mesh, color="red", opacity=0.35,
                show_edges=False, name="reference",
            )

    def _refresh_viewport(self):
        # compare toggle 변경 시
        self._refresh_overlay()
        self.plotter.render()

    def _set_view(self, kind: str):
        cam_map = {
            "iso": "iso", "xy": "xy", "yz": "yz", "xz": "xz",
        }
        if kind in cam_map:
            self.plotter.camera_position = cam_map[kind]
            self.plotter.reset_camera()
            self.plotter.render()

    # ── Util ─────────────────────────────────────────────────────────────

    def _show_error(self, title: str, exc: Exception):
        msg = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
        QMessageBox.critical(self, title, msg)
        self.statusBar().showMessage(f"[error] {title}", 5000)

    # ── RE-visualisation dock panels ────────────────────────────────────

    def attach_re_panels(
        self,
        *,
        panel: str = "both",
        catalog: dict | None = None,
        plan_a: Path | None = None,
        plan_b: Path | None = None,
    ) -> None:
        """Attach the feature catalog tree and/or the plan diff side-panel.

        Used by both the ``launch_ui_panel`` skill and the
        ``phone-designer inspect-re`` CLI. Safe to call more than once —
        previously-attached dock widgets are removed first.
        """
        # Lazy imports — these modules drag in PySide6 widgets we don't need
        # for the default Inspector path.
        from phone_designer.ui.panels.feature_catalog_panel import (
            FeatureCatalogPanel,
        )
        from phone_designer.ui.panels.plan_diff_panel import PlanDiffPanel
        from phone_designer.ui.widgets.highlight_overlay import HighlightOverlay

        # Tear down any previous panels we own.
        for attr in ("_catalog_dock", "_plan_diff_dock"):
            old = getattr(self, attr, None)
            if old is not None:
                try:
                    self.removeDockWidget(old)
                    old.deleteLater()
                except Exception:
                    pass
                setattr(self, attr, None)

        if panel in ("catalog", "both"):
            self._catalog_panel = FeatureCatalogPanel(self)
            self._catalog_panel.set_catalog(catalog)
            self._highlight_overlay = HighlightOverlay(self.plotter)
            self._catalog_panel.feature_selected.connect(
                self._highlight_overlay.show_feature
            )
            self._catalog_panel.cleared.connect(self._highlight_overlay.clear)
            self._catalog_dock = QDockWidget("RE Feature Catalog", self)
            self._catalog_dock.setWidget(self._catalog_panel)
            self.addDockWidget(Qt.LeftDockWidgetArea, self._catalog_dock)

        if panel in ("plan_diff", "both"):
            self._plan_diff_panel = PlanDiffPanel(self)
            self._plan_diff_panel.set_plans(plan_a, plan_b)
            self._plan_diff_dock = QDockWidget("Plan Diff", self)
            self._plan_diff_dock.setWidget(self._plan_diff_panel)
            self.addDockWidget(Qt.RightDockWidgetArea, self._plan_diff_dock)


def run_inspector_with_panels(
    *,
    panel: str = "both",
    catalog: dict | None = None,
    plan_a: Path | None = None,
    plan_b: Path | None = None,
    step: Path | None = None,
) -> int:
    """Launch the inspector pre-loaded with RE dock panels.

    Mirrors :func:`run_inspector` but also attaches the feature-catalog
    tree and/or plan-diff panel before entering the Qt event loop.
    """
    app = QApplication.instance() or QApplication(sys.argv)
    window = InspectorMainWindow()
    if step is not None:
        try:
            window.load_step(step)
        except Exception:
            pass
    window.attach_re_panels(
        panel=panel, catalog=catalog, plan_a=plan_a, plan_b=plan_b,
    )
    window.show()
    return app.exec()


# ──────────────────────────────────────────────────────────────────────────────
# Entry


def run_inspector(
    plan: Path | None = None,
    step: Path | None = None,
    reference: Path | None = None,
) -> int:
    """Inspector 실행. plan / step / reference 모두 optional."""
    app = QApplication.instance() or QApplication(sys.argv)
    window = InspectorMainWindow()
    if plan is not None:
        window.load_plan(plan)
    elif step is not None:
        window.load_step(step)
    if reference is not None:
        window.set_reference(reference)
    window.show()
    return app.exec()
