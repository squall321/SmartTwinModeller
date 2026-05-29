"""Scenario runner 의 dispatch + requires_oem skip + 기본 kind 동작 검증."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


def test_dispatch_known_kinds():
    """모든 dispatch kind 가 import 가능."""
    from phone_designer.scenarios.runner import _DISPATCH
    # Phase 0~2 까지의 kind 들
    expected = {
        "import_check", "run_python", "check_file_exists", "system_info",
        "log_summary", "viewport_offscreen", "read_xde_step",
        "check_parts", "face_count", "compare",
    }
    assert expected <= set(_DISPATCH.keys())


def test_requires_oem_skip_when_oem_missing(tmp_path, monkeypatch):
    """OEM CAD 없을 때 requires_oem=true 시나리오가 SKIPPED 처리되는지."""
    monkeypatch.setenv("PHONE_DESIGNER_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("PHONE_DESIGNER_OEM_REF", str(tmp_path / "nonexistent.step"))

    yaml_path = tmp_path / "test_oem.yaml"
    yaml_path.write_text(
        yaml.dump({
            "name": "test_oem_skip",
            "description": "should skip",
            "phase": 2,
            "requires_oem": True,
            "inputs": [],
            "steps": [
                {"kind": "import_check", "modules": ["json"]},
            ],
        }),
        encoding="utf-8",
    )

    from phone_designer.scenarios.runner import run_scenario
    result = run_scenario(yaml_path, send_mail=False)
    assert result.outcome == "SKIPPED"


def test_import_check_kind_works(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONE_DESIGNER_RUN_DIR", str(tmp_path))

    yaml_path = tmp_path / "test_imports.yaml"
    yaml_path.write_text(
        yaml.dump({
            "name": "test_imports",
            "description": "smoke",
            "phase": 0,
            "inputs": [],
            "steps": [
                {"kind": "import_check", "modules": ["json", "os", "sys"]},
            ],
        }),
        encoding="utf-8",
    )

    from phone_designer.scenarios.runner import run_scenario
    result = run_scenario(yaml_path, send_mail=False)
    assert result.outcome == "PASS"


def test_meta_json_emitted(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONE_DESIGNER_RUN_DIR", str(tmp_path))

    yaml_path = tmp_path / "test.yaml"
    yaml_path.write_text(
        yaml.dump({
            "name": "test_meta",
            "description": "smoke",
            "phase": 0,
            "inputs": [],
            "steps": [{"kind": "import_check", "modules": ["json"]}],
        }),
        encoding="utf-8",
    )

    from phone_designer.scenarios.runner import run_scenario
    result = run_scenario(yaml_path, send_mail=False)

    meta_path = result.run_dir / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["scenario"] == "test_meta"
    assert meta["outcome"] == "PASS"


def test_unknown_kind_skipped_not_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONE_DESIGNER_RUN_DIR", str(tmp_path))

    yaml_path = tmp_path / "test.yaml"
    yaml_path.write_text(
        yaml.dump({
            "name": "test_unknown",
            "description": "smoke",
            "phase": 0,
            "inputs": [],
            "steps": [
                {"kind": "this_kind_does_not_exist"},
                {"kind": "import_check", "modules": ["json"]},
            ],
        }),
        encoding="utf-8",
    )

    from phone_designer.scenarios.runner import run_scenario
    result = run_scenario(yaml_path, send_mail=False)
    # unknown kind 가 SKIP 으로 처리되어 overall PASS
    assert result.outcome == "PASS"
    statuses = [s.outcome for s in result.steps]
    assert "SKIP" in statuses
