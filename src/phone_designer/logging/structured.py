"""loguru 기반 JSON Lines 구조화 로거.

집 컴 (Dev): INFO 콘솔 + DEBUG 파일
회사 컴 (Test): DEBUG 파일 + INFO 콘솔 + viewport snapshot 자동

[[lat.md/dev-test.md#로깅-시스템]] 의 spec 구현.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class LogEntry:
    """1 줄짜리 구조화 로그 entry."""
    ts: str
    level: str
    phase: str
    msg: str = ""
    extra: dict[str, Any] | None = None


def _json_sink(message) -> str:
    """loguru record → JSON Lines."""
    record = message.record
    entry = {
        "ts": record["time"].astimezone(timezone.utc).isoformat(),
        "level": record["level"].name,
        "phase": record["extra"].get("phase", "general"),
        "msg": record["message"],
    }
    extra = {k: v for k, v in record["extra"].items() if k != "phase"}
    if extra:
        entry["extra"] = extra
    return json.dumps(entry, ensure_ascii=False) + "\n"


def configure_for_test_environment(run_dir: Path) -> None:
    """회사 컴 시나리오 실행 시 호출. DEBUG 까지 JSON Lines 파일 + INFO 콘솔."""
    logger.remove()
    logger.add(
        run_dir / "log.jsonl",
        level="DEBUG",
        format=_json_sink,
        enqueue=False,
    )
    logger.add(sys.stderr, level="INFO")
    logger.bind(phase="boot").info("Test environment logging configured at {}", run_dir)


def configure_for_dev_environment(level: str = "INFO") -> None:
    """집 컴 인터랙티브 사용. INFO 콘솔만, 파일 없음."""
    logger.remove()
    logger.add(sys.stderr, level=level)


def log_event(phase: str, level: str, msg: str = "", **extra) -> None:
    """1 줄 로그 — phase + extra 메타데이터 포함."""
    bound = logger.bind(phase=phase, **extra)
    getattr(bound, level.lower())(msg)
