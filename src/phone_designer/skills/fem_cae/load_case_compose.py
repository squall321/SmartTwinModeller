"""load_case_compose — atomic. Build a named load case from BCs + loads.

A solver run is a *load case*: a named bundle of (boundary conditions, loads,
analysis type). Real Abaqus/Calculix decks contain multiple ``*STEP`` blocks,
each with its own combination of restraints and loads.

This skill records the case spec on the body so the exporter emits one
``*STEP`` per case:

    body._pd_load_cases : list[dict] = [
        {
            "name":          str,
            "analysis_type": "static" | "modal" | "thermal" | "transient",
            "bcs":           [str, ...],   # names referring to body._pd_bcs records
            "loads":         [dict, ...],  # free-form load specs
        },
        ...
    ]

The exporter cross-references ``bcs`` against ``body._pd_bcs`` by index or
by selector-derived id; this skill simply validates and stores. Body
topology is not modified — metadata only.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def get_load_cases(body: Any) -> list[dict[str, Any]]:
    """Safe getter — never mutates the body."""
    return list(getattr(body, "_pd_load_cases", []) or [])


def _set_load_cases(body: Any, cases: list[dict[str, Any]]) -> None:
    if body is None:
        return
    try:
        body._pd_load_cases = cases
    except Exception:
        pass


@skill(
    name="load_case_compose",
    category="fem_cae",
    level="atomic",
    summary="Compose a named FEM load case bundling boundary conditions, "
            "applied loads, and analysis type. Appended to body._pd_load_cases. "
            "The downstream exporter emits one *STEP per case. Body topology "
            "unchanged.",
    selector_kinds=[],
    history_rules={"load_case": HistoryRule.MODIFIED_INHERIT},
    produces_features=["fem_load_case"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.duplicate_load_case", "fm.empty_load_case"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class LoadCaseCompose(SkillBase):
    class Args(BaseModel):
        case_name: str = Field(min_length=1, max_length=128)
        bcs: list[str] = Field(default_factory=list)
        loads: list[dict[str, Any]] = Field(default_factory=list)
        analysis_type: Literal["static", "modal", "thermal", "transient"] = "static"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("load_case_compose: body is None")

        # Require at least one of (bcs, loads) for non-modal cases — a pure
        # geometry step is meaningless. Modal analyses are allowed to have
        # no loads (free-free) but must still be addressable, so the name
        # alone is sufficient.
        if args.analysis_type != "modal" and not args.bcs and not args.loads:
            raise ValueError(
                f"load_case_compose: case {args.case_name!r} has neither "
                f"BCs nor loads — refusing to create an empty step"
            )

        existing = list(get_load_cases(body))
        existing_names = {c.get("name") for c in existing}
        if args.case_name in existing_names:
            raise ValueError(
                f"load_case_compose: load case named {args.case_name!r} "
                f"already exists on this body"
            )

        record = {
            "name":          args.case_name,
            "analysis_type": args.analysis_type,
            "bcs":           [str(b) for b in args.bcs],
            "loads":         [dict(ld) for ld in args.loads],
        }
        existing.append(record)
        _set_load_cases(body, existing)

        history = EntityHistoryMap(
            rules={"load_case": HistoryRule.MODIFIED_INHERIT},
        )
        return SkillResult(body=body, history=history)
