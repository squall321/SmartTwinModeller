"""match_standard_oring — atomic, read-only.

Given a measured O-ring cross-section (W, cs_mm) + inner Ø (id_mm), look the
ring up against ``catalogs/standards/o_rings_as568.yaml`` and return the top 3
closest AS568 dash sizes ranked by combined cs + id deviation.

Body is not modified — post_conditions=[body_present].

extras["matches"] = [
    {
        "dash":          "AS568-013",
        "catalog_cs":    1.78,
        "catalog_id":    10.82,
        "deviation":     {"cs_mm": 0.0, "id_mm": 0.05, "total_mm": 0.05},
        "confidence":    0.95,
    },
    ...
]
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family, name):
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parents[4]
    path = root / "catalogs" / family / f"{name}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())


@skill(
    name="match_standard_oring",
    category="inspect",
    level="atomic",
    summary="Match a measured O-ring (cross-section + inner Ø) against the "
            "AS568 catalog and return the top 3 closest dash sizes. "
            "Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["standard_oring_match"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class MatchStandardORing(SkillBase):
    class Args(BaseModel):
        cs_mm: float = Field(gt=0, le=50, description="cross-section (W)")
        id_mm: float = Field(gt=0, le=500, description="inner diameter")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        data = _load("standards", "o_rings_as568")
        rings = (data or {}).get("o_rings", {})

        candidates: list[dict[str, Any]] = []
        for dash, entry in rings.items():
            cs = float(entry.get("cs_mm", 0.0))
            iid = float(entry.get("id_mm", 0.0))
            d_cs = abs(args.cs_mm - cs)
            d_id = abs(args.id_mm - iid)
            total = d_cs + d_id
            candidates.append({
                "dash": str(dash),
                "catalog_cs": round(cs, 4),
                "catalog_id": round(iid, 4),
                "deviation": {
                    "cs_mm":    round(d_cs, 4),
                    "id_mm":    round(d_id, 4),
                    "total_mm": round(total, 4),
                },
                "confidence": round(1.0 / (1.0 + total), 4),
            })

        candidates.sort(key=lambda m: m["deviation"]["total_mm"])
        matches = candidates[:3]

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "matches": matches,
                "query": {"cs_mm": args.cs_mm, "id_mm": args.id_mm},
            },
        )
