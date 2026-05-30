"""CI gate: validate every catalogs/<family>/<name>.yaml against the typed
loader (src/phone_designer/skills/_catalog.py).

Exit codes:
  0 — every catalog file resolves to a non-empty, schema-valid entry dict.
  1 — at least one catalog failed (missing file, YAML parse error,
      pydantic validation error, or empty entries block).

Output: one line per failing file with the error list, then a summary.

Usage (from repo root):
    venv/Scripts/python.exe scripts/validate_catalogs.py
"""
from __future__ import annotations

import sys
import pathlib


def _ensure_import_path() -> None:
    """Allow running the script directly without `pip install -e .`."""
    here = pathlib.Path(__file__).resolve().parent
    src = here.parent / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main() -> int:
    _ensure_import_path()
    from phone_designer.skills._catalog import validate_all

    report = validate_all()
    bad = {path: errs for path, errs in report.items() if errs}

    if not bad:
        print(f"catalog OK — {len(report)} files validated.")
        return 0

    print(f"catalog FAILED — {len(bad)}/{len(report)} files have errors.\n")
    for path, errs in sorted(bad.items()):
        print(f"  {path}:")
        for e in errs:
            print(f"    - {e}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
