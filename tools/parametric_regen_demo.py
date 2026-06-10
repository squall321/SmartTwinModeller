#!/usr/bin/env python
"""Parametric regeneration demo v2 — thin CLI wrapper (plan item P4).

All logic lives in ``src/phone_designer/corpus/parametric_demo.py`` so the
pytest gate (tests/test_parametric_regeneration.py) imports the SAME code
with PYTHONPATH=src and no path hacks; this wrapper only makes the tool
runnable from a bare shell (it inserts <repo>/src itself).

Usage:
    $env:PYTHONPATH = "src"   # optional — the shim below covers it
    & "venv\\Scripts\\python.exe" tools/parametric_regen_demo.py
    ... [--files simple_watch_housing,occt__linkrods]
    ... [--json-out path] [--md-out path]

Exit 1 when any GATED cell (simple_watch housing / occt__linkrods) FAILs.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from phone_designer.corpus.parametric_demo import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
