"""modal_analysis_setup — atomic. Tag the body with modal analysis settings.

Modal analysis (extraction of natural frequencies and mode shapes) requires
two pieces of solver configuration:

  * the number of modes to extract, and
  * the frequency window of interest.

This skill annotates the body so a downstream FEM exporter can emit the
correct ``*FREQUENCY`` (Abaqus) / ``*FREQUENCY`` (Calculix) / ``EIGENMODES``
block:

    body._pd_modal = {
        "count":       int,
        "range":       (low_hz, high_hz),
    }

Body topology is not modified — metadata only.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def get_modal(body: Any) -> dict[str, Any] | None:
    """Safe getter — returns None when the body has no modal tag yet."""
    return getattr(body, "_pd_modal", None)


def _set_modal(body: Any, modal: dict[str, Any]) -> None:
    if body is None:
        return
    try:
        body._pd_modal = modal
    except Exception:
        pass


@skill(
    name="modal_analysis_setup",
    category="fem_cae",
    level="atomic",
    summary="Tag the body with modal-analysis configuration (mode count and "
            "frequency range). Consumed by downstream FEM exporters to emit "
            "the correct frequency-extraction step. Body topology unchanged.",
    selector_kinds=[],
    history_rules={"modal_setup": HistoryRule.MODIFIED_INHERIT},
    produces_features=["fem_modal_setup"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.invalid_mode_count", "fm.invalid_frequency_range"],
    cost_hint=0.02,
    post_conditions=[PostCondition(kind="body_present")],
)
class ModalAnalysisSetup(SkillBase):
    class Args(BaseModel):
        mode_count: int = Field(default=10, ge=1, le=10000)
        frequency_range_hz: tuple[float, float] = Field(default=(0.0, 1000.0))

        @field_validator("frequency_range_hz")
        @classmethod
        def _check_range(cls, v):
            lo, hi = float(v[0]), float(v[1])
            if lo < 0.0 or hi <= lo:
                raise ValueError(
                    f"modal_analysis_setup: frequency_range_hz must satisfy "
                    f"0 ≤ low < high (got {v})"
                )
            return (lo, hi)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("modal_analysis_setup: body is None")

        modal = {
            "count": int(args.mode_count),
            "range": (float(args.frequency_range_hz[0]),
                      float(args.frequency_range_hz[1])),
        }
        _set_modal(body, modal)

        history = EntityHistoryMap(
            rules={"modal_setup": HistoryRule.MODIFIED_INHERIT},
        )
        return SkillResult(body=body, history=history)
