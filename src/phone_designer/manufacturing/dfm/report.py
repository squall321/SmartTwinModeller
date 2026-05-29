"""DFMReport — wall / draft / undercut 위반 모음."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DFMSeverity(str, Enum):
    OK      = "ok"
    WARN    = "warn"
    VIOLATE = "violate"


@dataclass
class DFMViolation:
    """1 위반 항목."""
    kind: str                       # "wall_thickness" | "draft" | "undercut"
    severity: DFMSeverity
    process: str                    # 어느 공정 기준
    message: str
    location: tuple[float, float, float] | None = None
    measured_value: float | None = None
    required_value: float | None = None
    confidence: float = 1.0         # 0..1, sampling 신뢰도

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity.value,
            "process": self.process,
            "message": self.message,
            "location": list(self.location) if self.location else None,
            "measured": self.measured_value,
            "required": self.required_value,
            "confidence": self.confidence,
        }


@dataclass
class DFMReport:
    """1 plan / shape 의 DFM 검증 결과."""
    process: str
    confidence: float = 1.0
    wall_violations: list[DFMViolation] = field(default_factory=list)
    draft_violations: list[DFMViolation] = field(default_factory=list)
    undercut_violations: list[DFMViolation] = field(default_factory=list)

    @property
    def all_violations(self) -> list[DFMViolation]:
        return self.wall_violations + self.draft_violations + self.undercut_violations

    @property
    def outcome(self) -> DFMSeverity:
        if any(v.severity == DFMSeverity.VIOLATE for v in self.all_violations):
            return DFMSeverity.VIOLATE
        if any(v.severity == DFMSeverity.WARN for v in self.all_violations):
            return DFMSeverity.WARN
        return DFMSeverity.OK

    def summary(self) -> str:
        v = len([x for x in self.all_violations if x.severity == DFMSeverity.VIOLATE])
        w = len([x for x in self.all_violations if x.severity == DFMSeverity.WARN])
        return (
            f"DFM({self.process}): outcome={self.outcome.value}, "
            f"violate={v}, warn={w}, confidence={self.confidence:.2f}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "process": self.process,
            "outcome": self.outcome.value,
            "confidence": self.confidence,
            "wall_violations": [v.to_dict() for v in self.wall_violations],
            "draft_violations": [v.to_dict() for v in self.draft_violations],
            "undercut_violations": [v.to_dict() for v in self.undercut_violations],
        }
