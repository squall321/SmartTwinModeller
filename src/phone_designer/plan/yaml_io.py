"""Plan YAML io + schema migration baseline.

[[lat.md/concepts.md#schema-버전-관리]] 의 v1 → v2 핸들러 자리.
v1 만 있는 현재는 migration 없이 통과.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from phone_designer.plan.model import CURRENT_SCHEMA_VERSION, Plan


# Migration handlers: schema_version 별 dict 변환
_MIGRATIONS: dict[int, callable] = {
    # 1: identity (no-op, baseline)
}


def _migrate(plan_dict: dict[str, Any]) -> dict[str, Any]:
    """Plan dict 의 schema_version 을 CURRENT_SCHEMA_VERSION 까지 점진 마이그레이션."""
    v = plan_dict.get("schema_version", 1)
    while v < CURRENT_SCHEMA_VERSION:
        if v not in _MIGRATIONS:
            raise ValueError(
                f"plan schema_version={v} 에서 v{v + 1} 로 가는 migration 핸들러가 없음"
            )
        plan_dict = _MIGRATIONS[v](plan_dict)
        v = plan_dict.get("schema_version", v + 1)
    if v > CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"plan schema_version={v} 이 현재 지원 ({CURRENT_SCHEMA_VERSION}) 보다 높음"
        )
    return plan_dict


def load_plan(path: Path | str) -> Plan:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError(f"plan YAML 의 top-level 이 mapping 아님: {path}")
    migrated = _migrate(raw)
    return Plan.model_validate(migrated)


def save_plan(plan: Plan, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # mode='json' 으로 enum → str, tuple → list 등 JSON-호환 primitive 로 직렬화.
    # 그래야 yaml.safe_load 가 python object tag 없이 읽음.
    text = yaml.safe_dump(
        plan.model_dump(mode="json", exclude_none=True),
        sort_keys=False,
        allow_unicode=True,
    )
    path.write_text(text, encoding="utf-8")
