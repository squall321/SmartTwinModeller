"""tolerance_stack — atomic, read-only.

Compute a 1-D worst-case tolerance stack from a chain of dimensions. Each
dimension contributes its ``nominal`` and ± deviation to the cumulative
stack:

    nominal_total = Σ nominal_i
    worst_max     = nominal_total + Σ plus_i
    worst_min     = nominal_total + Σ (-minus_i)
    rss_plus      = sqrt(Σ plus_i²)        # statistical (RSS) bounds
    rss_minus     = sqrt(Σ minus_i²)

Each ``dimension`` is a dict ``{"name": str, "nominal": float, "plus":
float, "minus": float}``. ``plus`` and ``minus`` are signed magnitudes
(both positive numbers; e.g. ±0.1 → plus=0.1, minus=0.1).

The body is not used for computation — this skill is a pure analytical
helper that LLM passes a dimension chain to. Body is required only because
the SkillBase contract expects it; it is returned unchanged.

extras schema:
    {
      "stack": [
        (dim_name, nominal, plus, minus),
        ...
      ],
      "nominal_total": float,
      "worst_case_max": float,
      "worst_case_min": float,
      "worst_case_plus": float,         # max - nominal_total
      "worst_case_minus": float,        # nominal_total - min
      "rss_plus": float,
      "rss_minus": float,
      "dim_count": int
    }

body unchanged (post ``body_present``).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


@skill(
    name="tolerance_stack",
    category="inspect",
    level="atomic",
    summary="1-D tolerance stack analysis from a dimension chain. Computes "
            "worst-case (linear sum) and RSS (statistical) ± bounds plus per-"
            "dimension tuples. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["tolerance_stack_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class ToleranceStack(SkillBase):
    class Args(BaseModel):
        dimensions: list[dict] = Field(default_factory=list)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        # body is untouched — but verify the OCCT shape resolves so we don't
        # silently skip the post-condition check.
        _ = _occt_shape(body)

        stack: list[tuple[str, float, float, float]] = []
        nominal_total = 0.0
        sum_plus = 0.0
        sum_minus = 0.0
        sumsq_plus = 0.0
        sumsq_minus = 0.0

        for entry in args.dimensions:
            name = str(entry.get("name", f"dim_{len(stack)}"))
            nominal = float(entry.get("nominal", 0.0))
            plus = float(entry.get("plus", 0.0))
            minus = float(entry.get("minus", 0.0))
            # Coerce ± to non-negative magnitudes
            plus = abs(plus)
            minus = abs(minus)

            stack.append((name, nominal, plus, minus))
            nominal_total += nominal
            sum_plus += plus
            sum_minus += minus
            sumsq_plus += plus * plus
            sumsq_minus += minus * minus

        worst_max = nominal_total + sum_plus
        worst_min = nominal_total - sum_minus
        rss_plus = math.sqrt(sumsq_plus)
        rss_minus = math.sqrt(sumsq_minus)

        extras = {
            "stack": stack,
            "nominal_total": round(nominal_total, 6),
            "worst_case_max": round(worst_max, 6),
            "worst_case_min": round(worst_min, 6),
            "worst_case_plus": round(sum_plus, 6),
            "worst_case_minus": round(sum_minus, 6),
            "rss_plus": round(rss_plus, 6),
            "rss_minus": round(rss_minus, 6),
            "dim_count": len(stack),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
