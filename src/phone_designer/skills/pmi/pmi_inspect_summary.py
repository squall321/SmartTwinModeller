"""pmi_inspect_summary — atomic, read-only.

Read every ``_pd_pmi_*`` / ``_pd_welds`` / ``_pd_datums`` / ``_pd_fcf`` /
``_pd_surface_texture`` tag on the body and report a single summary dict.
Useful as the last step before AP242 export to confirm what will be written.

extras schema::

    {"pmi_summary": {
        "dimensions": N,
        "datums": ["A", "B", "C"],
        "textures": N,
        "welds": N,
        "fcfs": N,
    }}

body unchanged — ``body_present`` post-condition.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="pmi_inspect_summary",
    category="pmi",
    level="atomic",
    summary="Summarise every PMI tag attached to the body (dimensions, "
            "datums, surface textures, welds, FCFs). Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["pmi_summary"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.01,
    post_conditions=[PostCondition(kind="body_present")],
)
class PmiInspectSummary(SkillBase):
    class Args(BaseModel):
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("pmi_inspect_summary: body is None")

        dims = list(getattr(body, "_pd_pmi_dimensions", []) or [])
        textures = list(getattr(body, "_pd_surface_texture", []) or [])
        welds = list(getattr(body, "_pd_welds", []) or [])
        fcfs = list(getattr(body, "_pd_fcf", []) or [])
        datums_dict = getattr(body, "_pd_datums", {}) or {}
        try:
            datum_labels = sorted(list(datums_dict.keys()))
        except Exception:
            datum_labels = []

        summary = {
            "dimensions": len(dims),
            "datums": datum_labels,
            "textures": len(textures),
            "welds": len(welds),
            "fcfs": len(fcfs),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"pmi_summary": summary},
        )
