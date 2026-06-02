"""PlanDiffPanel — side-by-side YAML plan viewer with step-level diff.

Loads two plan YAMLs (typically the *original* reference plan and the
*reconstructed* plan produced by the RE pipeline) and shows them side
by side with line-level highlighting:

    * green   — line exists only in the right-hand (added)
    * red     — line exists only in the left-hand (removed)
    * yellow  — line changed (present in both with different content)

The diff is computed step-by-step (each plan step → a YAML block) so
re-ordered steps are visible as removed-then-added rather than a noisy
character diff. This is much more readable than a raw ``difflib`` line
diff on the full file.

API::

    panel = PlanDiffPanel()
    panel.set_plans(Path("plans/original.yaml"), Path("plans/regen.yaml"))
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# Background colors for diff highlighting (slightly translucent).
_COLOR_ADDED   = QColor(180, 240, 180)   # light green
_COLOR_REMOVED = QColor(255, 200, 200)   # light red
_COLOR_CHANGED = QColor(255, 240, 170)   # light yellow
_COLOR_HEADER  = QColor(220, 230, 245)   # blue-grey


def _load_yaml(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return None


def _steps(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract the ``steps`` list (tolerate dict-shaped plans without it)."""
    if not isinstance(plan, dict):
        return []
    steps = plan.get("steps")
    if isinstance(steps, list):
        return [s for s in steps if isinstance(s, dict)]
    return []


def _step_id(step: dict[str, Any]) -> str:
    return str(step.get("id") or step.get("step_id") or step.get("skill") or "<?>")


def _step_yaml_block(step: dict[str, Any]) -> str:
    """Render one step as a YAML block (sorted keys) for diffing."""
    try:
        return yaml.safe_dump(step, sort_keys=True, allow_unicode=True).rstrip()
    except Exception:
        return repr(step)


class PlanDiffPanel(QWidget):
    """Two QTextEdit side-by-side, with step-block diff highlighting.

    Signals:
        plans_loaded(int, int):
            (left_steps, right_steps) — emitted after a successful load.
    """

    plans_loaded = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plan_a_path: Path | None = None
        self._plan_b_path: Path | None = None
        self._plan_a: dict[str, Any] | None = None
        self._plan_b: dict[str, Any] | None = None
        self._build_ui()

    # ── ui ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Plan Diff</b>"))
        header.addStretch(1)

        self._status_label = QLabel("(no plans loaded)")
        self._status_label.setStyleSheet("color: #555;")
        header.addWidget(self._status_label)

        btn_swap = QPushButton("Swap ↔")
        btn_swap.setToolTip("Swap the left and right plan")
        btn_swap.clicked.connect(self._on_swap_clicked)
        header.addWidget(btn_swap)
        root.addLayout(header)

        splitter = QSplitter(Qt.Horizontal, self)

        # Left side
        left_wrap = QWidget()
        left_l = QVBoxLayout(left_wrap)
        left_l.setContentsMargins(0, 0, 0, 0)
        self._left_label = QLabel("<i>plan_a — (none)</i>")
        left_l.addWidget(self._left_label)
        self._left_text = self._make_text_edit()
        left_l.addWidget(self._left_text, stretch=1)
        splitter.addWidget(left_wrap)

        # Right side
        right_wrap = QWidget()
        right_l = QVBoxLayout(right_wrap)
        right_l.setContentsMargins(0, 0, 0, 0)
        self._right_label = QLabel("<i>plan_b — (none)</i>")
        right_l.addWidget(self._right_label)
        self._right_text = self._make_text_edit()
        right_l.addWidget(self._right_text, stretch=1)
        splitter.addWidget(right_wrap)

        splitter.setSizes([500, 500])
        root.addWidget(splitter, stretch=1)

        # Legend
        legend = QHBoxLayout()
        for label, color in (
            ("added", _COLOR_ADDED),
            ("removed", _COLOR_REMOVED),
            ("changed", _COLOR_CHANGED),
            ("step header", _COLOR_HEADER),
        ):
            sw = QLabel(f"  {label}  ")
            sw.setAutoFillBackground(True)
            p = sw.palette()
            p.setColor(sw.backgroundRole(), color)
            sw.setPalette(p)
            legend.addWidget(sw)
        legend.addStretch(1)
        root.addLayout(legend)

    def _make_text_edit(self) -> QTextEdit:
        ed = QTextEdit()
        ed.setReadOnly(True)
        ed.setLineWrapMode(QTextEdit.NoWrap)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(10)
        ed.setFont(f)
        return ed

    # ── public api ────────────────────────────────────────────────────────

    def set_plans(
        self,
        plan_a: Path | str | None,
        plan_b: Path | str | None,
    ) -> None:
        """Load both plans and refresh the diff. Either may be ``None``."""
        self._plan_a_path = Path(plan_a) if plan_a else None
        self._plan_b_path = Path(plan_b) if plan_b else None
        self._plan_a = _load_yaml(self._plan_a_path)
        self._plan_b = _load_yaml(self._plan_b_path)
        self._render()

    # ── rendering ─────────────────────────────────────────────────────────

    def _render(self) -> None:
        self._left_text.clear()
        self._right_text.clear()

        self._left_label.setText(
            f"<i>plan_a — {self._plan_a_path.name if self._plan_a_path else '(none)'}</i>"
        )
        self._right_label.setText(
            f"<i>plan_b — {self._plan_b_path.name if self._plan_b_path else '(none)'}</i>"
        )

        steps_a = _steps(self._plan_a)
        steps_b = _steps(self._plan_b)

        ids_a = [_step_id(s) for s in steps_a]
        ids_b = [_step_id(s) for s in steps_b]

        # Build maps id → step. Duplicate ids are unusual but tolerated
        # (last-write-wins, mirroring the diff semantics).
        map_a = {sid: s for sid, s in zip(ids_a, steps_a)}
        map_b = {sid: s for sid, s in zip(ids_b, steps_b)}

        only_a = [sid for sid in ids_a if sid not in map_b]
        only_b = [sid for sid in ids_b if sid not in map_a]
        common = [sid for sid in ids_a if sid in map_b]

        changed: list[str] = []
        for sid in common:
            if _step_yaml_block(map_a[sid]) != _step_yaml_block(map_b[sid]):
                changed.append(sid)

        self._status_label.setText(
            f"left={len(steps_a)} steps  ·  right={len(steps_b)} steps  ·  "
            f"+{len(only_b)}  −{len(only_a)}  ~{len(changed)}"
        )

        # Render LEFT (plan_a) — display every step in plan_a order.
        for sid in ids_a:
            if sid in only_a:
                self._append_step_block(
                    self._left_text, sid, map_a[sid],
                    block_color=_COLOR_REMOVED,
                )
            elif sid in changed:
                self._append_changed_block(
                    self._left_text, sid, map_a[sid], map_b[sid],
                    is_left=True,
                )
            else:
                self._append_step_block(self._left_text, sid, map_a[sid])

        # Render RIGHT (plan_b) — display every step in plan_b order.
        for sid in ids_b:
            if sid in only_b:
                self._append_step_block(
                    self._right_text, sid, map_b[sid],
                    block_color=_COLOR_ADDED,
                )
            elif sid in changed:
                self._append_changed_block(
                    self._right_text, sid, map_a[sid], map_b[sid],
                    is_left=False,
                )
            else:
                self._append_step_block(self._right_text, sid, map_b[sid])

        # Reset scroll to top
        self._left_text.moveCursor(QTextCursor.Start)
        self._right_text.moveCursor(QTextCursor.Start)

        self.plans_loaded.emit(len(steps_a), len(steps_b))

    def _append_step_block(
        self,
        ed: QTextEdit,
        sid: str,
        step: dict[str, Any],
        block_color: QColor | None = None,
    ) -> None:
        """Append `### sid` header + YAML block. Optionally tint the whole block."""
        cur = ed.textCursor()
        cur.movePosition(QTextCursor.End)

        # Header line
        hdr_fmt = QTextCharFormat()
        hdr_fmt.setBackground(_COLOR_HEADER)
        hdr_fmt.setFontWeight(QFont.Bold)
        cur.insertText(f"### step: {sid}\n", hdr_fmt)

        body_fmt = QTextCharFormat()
        if block_color is not None:
            body_fmt.setBackground(block_color)

        for line in _step_yaml_block(step).split("\n"):
            cur.insertText(line + "\n", body_fmt)
        cur.insertText("\n", QTextCharFormat())

    def _append_changed_block(
        self,
        ed: QTextEdit,
        sid: str,
        step_a: dict[str, Any],
        step_b: dict[str, Any],
        *,
        is_left: bool,
    ) -> None:
        """Append a step where both sides differ — line-level diff."""
        a_lines = _step_yaml_block(step_a).split("\n")
        b_lines = _step_yaml_block(step_b).split("\n")

        diff = list(difflib.ndiff(a_lines, b_lines))

        cur = ed.textCursor()
        cur.movePosition(QTextCursor.End)

        hdr_fmt = QTextCharFormat()
        hdr_fmt.setBackground(_COLOR_CHANGED)
        hdr_fmt.setFontWeight(QFont.Bold)
        cur.insertText(f"### step: {sid}   ~ changed\n", hdr_fmt)

        plain_fmt = QTextCharFormat()
        add_fmt = QTextCharFormat();   add_fmt.setBackground(_COLOR_ADDED)
        rem_fmt = QTextCharFormat();   rem_fmt.setBackground(_COLOR_REMOVED)

        for line in diff:
            if line.startswith("  "):    # unchanged
                cur.insertText(line[2:] + "\n", plain_fmt)
            elif line.startswith("- "):  # only in left
                if is_left:
                    cur.insertText(line[2:] + "\n", rem_fmt)
                # right side skips removed lines (they aren't there)
            elif line.startswith("+ "):  # only in right
                if not is_left:
                    cur.insertText(line[2:] + "\n", add_fmt)
                # left side skips added lines
            elif line.startswith("? "):
                continue  # difflib hint — ignore
        cur.insertText("\n", plain_fmt)

    # ── footer actions ────────────────────────────────────────────────────

    def _on_swap_clicked(self) -> None:
        self.set_plans(self._plan_b_path, self._plan_a_path)
