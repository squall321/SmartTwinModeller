"""Pytest 공통 설정.

테스트 마커는 pyproject.toml 에 등록되어 있다 ([tool.pytest.ini_options]):
  - slow         (>10s)
  - llm          (real LLM 호출, CI skip)
  - ui           (Qt event loop 필요)
  - requires_oem (OEM CAD 필요, 회사 컴 only)
"""
from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """OEM CAD 없으면 requires_oem 자동 skip."""
    oem_path = os.environ.get(
        "PHONE_DESIGNER_OEM_REF",
        "reference/galaxy_watch/converted.step",
    )
    oem_available = os.path.exists(oem_path)
    if oem_available:
        return
    skip_marker = pytest.mark.skip(reason=f"OEM CAD not found at {oem_path}")
    for item in items:
        if "requires_oem" in item.keywords:
            item.add_marker(skip_marker)
