"""run_dfm — wall + draft + undercut 일괄 실행."""
from __future__ import annotations

from typing import Any

from phone_designer.manufacturing.dfm.draft import draft_violations
from phone_designer.manufacturing.dfm.report import (
    DFMReport,
    DFMSeverity,
    DFMViolation,
)
from phone_designer.manufacturing.dfm.undercut import undercut_violations
from phone_designer.manufacturing.dfm.wall_thickness import wall_thickness_raymarch


def run_dfm(
    shape,
    *,
    process_code: str,
    pull_direction: tuple[float, float, float] = (0, 0, 1),
    n_samples_per_face: int = 5,
) -> DFMReport:
    """body 의 wall / draft / undercut 일괄 검사.

    process_code 의 rules 에서 min_wall_mm / min_draft_deg / undercut_allowed 적용.
    """
    from phone_designer.manufacturing.processes import registry

    proc = registry.get(process_code)
    rules = proc.rules

    report = DFMReport(process=process_code, confidence=0.7)   # ray-march v0 신뢰도

    # wall thickness
    min_wall = rules.get("min_wall_mm")
    if min_wall is not None:
        report.wall_violations = wall_thickness_raymarch(
            shape,
            min_wall_mm=float(min_wall),
            process_code=process_code,
            n_samples_per_face=n_samples_per_face,
        )

    # draft
    min_draft = rules.get("min_draft_deg")
    if min_draft is not None:
        report.draft_violations = draft_violations(
            shape,
            min_draft_deg=float(min_draft),
            process_code=process_code,
            pull_direction=pull_direction,
        )

    # undercut
    if rules.get("undercut_allowed") is False:
        report.undercut_violations = undercut_violations(
            shape,
            process_code=process_code,
            pull_direction=pull_direction,
        )

    return report
