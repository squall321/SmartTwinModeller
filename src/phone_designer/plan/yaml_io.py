"""Plan YAML io + schema migration baseline.

[[lat.md/concepts.md#schema-버전-관리]] 의 v1 → v2 핸들러 자리.
v1 만 있는 현재는 migration 없이 통과.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from phone_designer.plan.model import CURRENT_SCHEMA_VERSION, Plan


def _migrate_v1_to_v2(plan_dict: dict[str, Any]) -> dict[str, Any]:
    """v1 → v2 마이그레이션 — 내용상 identity.

    v2 는 순수 ADDITIVE: 추가된 것은 optional top-level ``parameters`` 테이블
    (+ step args 안의 ``{"$expr": ...}`` 노드) 뿐이므로, 모든 v1 문서는 이미
    parameters=None 인 유효한 v2 문서다.

    BYTE-STABILITY (corpus-regress blocking gate): 여기서 schema_version 을
    2 로 고쳐 쓰지 **않는다**. param-less plan 은 끝까지
    ``schema_version: 1`` 을 유지해야 save(load(v1_yaml)) 가 새 키/버전 churn
    없이 byte-identical 하다. (Plan 모델의 after-validator 가 parameters 를
    실제로 쓰는 plan 만 v2 로 승격한다.)
    """
    return plan_dict


# Migration handlers: schema_version 별 dict 변환
_MIGRATIONS: dict[int, callable] = {
    1: _migrate_v1_to_v2,
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
        # additive migration 은 byte-stability 를 위해 dict 의 schema_version
        # 을 의도적으로 그대로 둘 수 있다 — cursor 는 항상 전진시켜 identity
        # 핸들러가 무한 루프하지 않게 한다. (버전을 더 크게 올려 쓴 핸들러의
        # 선언은 존중.)
        v = max(plan_dict.get("schema_version", v + 1), v + 1)
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
