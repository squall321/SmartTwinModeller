"""구조화 로깅 + viewport 캡처 + 번들/메일 (Phase 0).

[[lat.md/dev-test.md#로깅-시스템]] 참조.
"""
from phone_designer.logging.structured import (
    configure_for_test_environment,
    configure_for_dev_environment,
    log_event,
    LogEntry,
)

__all__ = [
    "configure_for_test_environment",
    "configure_for_dev_environment",
    "log_event",
    "LogEntry",
]
