"""CI test — every catalogs/<family>/<name>.yaml must parse + validate.

Mirrors scripts/validate_catalogs.py so the same gate runs both locally
(``python scripts/validate_catalogs.py``) and in pytest. If a new YAML is
added that the typed loader can't route, this test flags it before the
catalog ever reaches a generator skill.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from phone_designer.skills._catalog import validate_all


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_validate_all_catalog_files_clean() -> None:
    report = validate_all()
    assert report, "validate_all() must walk at least one catalog file"
    failing = {path: errs for path, errs in report.items() if errs}
    if failing:
        lines = ["catalog validation failed:"]
        for path, errs in sorted(failing.items()):
            lines.append(f"  {path}:")
            for e in errs:
                lines.append(f"    - {e}")
        raise AssertionError("\n".join(lines))


def test_validate_script_exits_zero() -> None:
    """Running scripts/validate_catalogs.py as a subprocess must return 0."""
    script = REPO_ROOT / "scripts" / "validate_catalogs.py"
    assert script.exists(), f"missing CI script: {script}"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"validate_catalogs.py exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
