"""`analyze --timeout-s` watchdog path (2026-06-18).

A huge assembly can HANG in OCCT (174s import + a non-preemptible detector
thread-pool), and Windows has no in-process way to interrupt it. The `analyze`
CLI's `--timeout-s` runs the analysis in an isolated subprocess with a wall-clock
watchdog (reusing the batch harness), so a runaway part times out HONESTLY
instead of hanging the CLI. These tests pin: (1) the watchdog path produces a
valid report, and (2) a too-short budget yields an honest TIMEOUT + exit 1 (not a
hang).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from typer.testing import CliRunner

from phone_designer.cli import app

runner = CliRunner()


def _synth_step(tmp: Path) -> str:
    from build123d import Box, Cylinder
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    part = Box(20, 16, 8) - Cylinder(2.0, 8.1)
    out = str(tmp / "s.step")
    w = STEPControl_Writer()
    w.Transfer(part.wrapped, STEPControl_AsIs)
    w.Write(out)
    return out


def _all_output(result) -> str:
    txt = result.output or ""
    try:
        txt += result.stderr or ""
    except (ValueError, AttributeError):
        pass
    return txt


def test_watchdog_path_produces_report():
    with tempfile.TemporaryDirectory() as d:
        synth = _synth_step(Path(d))
        out = Path(d) / "r.html"
        result = runner.invoke(
            app, ["analyze", synth, "-o", str(out), "--timeout-s", "180"]
        )
        assert result.exit_code == 0, _all_output(result)
        assert "subprocess" in _all_output(result)
        assert out.exists()
        assert "<html" in out.read_text(encoding="utf-8").lower()


def test_watchdog_timeout_is_honest_not_a_hang():
    with tempfile.TemporaryDirectory() as d:
        synth = _synth_step(Path(d))
        # 1s budget can't even finish the subprocess import -> watchdog fires.
        result = runner.invoke(app, ["analyze", synth, "--timeout-s", "1"])
        assert result.exit_code == 1
        assert "TIMEOUT" in _all_output(result)


def test_default_path_stays_in_process():
    # default (no --timeout-s) keeps the in-process full-feature path.
    with tempfile.TemporaryDirectory() as d:
        synth = _synth_step(Path(d))
        result = runner.invoke(app, ["analyze", synth])
        assert result.exit_code == 0, _all_output(result)
        assert "subprocess" not in _all_output(result)
        assert "part:" in _all_output(result)
