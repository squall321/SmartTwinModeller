"""match_standard_bearing — atomic, read-only.

Given any subset of (outer_d_mm, bore_d_mm, width_mm), look the bearing up
against ``catalogs/standards/bearings_metric.yaml`` and return the top 3
closest standard deep-groove ball bearings ranked by total dimensional
deviation (only the supplied dimensions are scored).

Body is not modified — post_conditions=[body_present].

extras["matches"] = [
    {
        "bearing_spec": "608",
        "catalog_dims": {"bore_id_mm": 8, "outer_d_mm": 22, "width_mm": 7},
        "deviation":    {"bore_id_mm": 0.05, "outer_d_mm": 0.1, "width_mm": 0.0,
                          "total_mm": 0.15},
        "confidence":   0.87,
    },
    ...
]
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

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


def _score_entry(
    entry: dict,
    outer: float | None,
    bore: float | None,
    width: float | None,
) -> dict[str, float]:
    """Return per-dim deviation dict + total_mm. Missing dims are skipped."""
    dev: dict[str, float] = {}
    total = 0.0
    if outer is not None and "outer_d_mm" in entry:
        d = abs(outer - float(entry["outer_d_mm"]))
        dev["outer_d_mm"] = round(d, 4)
        total += d
    if bore is not None and "bore_id_mm" in entry:
        d = abs(bore - float(entry["bore_id_mm"]))
        dev["bore_id_mm"] = round(d, 4)
        total += d
    if width is not None and "width_mm" in entry:
        d = abs(width - float(entry["width_mm"]))
        dev["width_mm"] = round(d, 4)
        total += d
    dev["total_mm"] = round(total, 4)
    return dev


@skill(
    name="match_standard_bearing",
    category="inspect",
    level="atomic",
    summary="Match a measured bearing (any subset of outer Ø / bore Ø / width) "
            "against the metric deep-groove ball bearing catalog and return "
            "the top 3 closest standard designations. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["standard_bearing_match"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class MatchStandardBearing(SkillBase):
    class Args(BaseModel):
        outer_d_mm: float | None = Field(default=None, gt=0, le=500)
        bore_d_mm: float | None = Field(default=None, gt=0, le=500)
        width_mm: float | None = Field(default=None, gt=0, le=200)

        @model_validator(mode="after")
        def _at_least_one(self):
            if (
                self.outer_d_mm is None
                and self.bore_d_mm is None
                and self.width_mm is None
            ):
                raise ValueError(
                    "match_standard_bearing: at least one of "
                    "outer_d_mm / bore_d_mm / width_mm must be given."
                )
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        data = _load("standards", "bearings_metric")
        bearings = (data or {}).get("bearings", {})

        candidates: list[dict[str, Any]] = []
        for spec, entry in bearings.items():
            dev = _score_entry(entry, args.outer_d_mm, args.bore_d_mm, args.width_mm)
            candidates.append({
                "bearing_spec": str(spec),
                "catalog_dims": {
                    "bore_id_mm": float(entry.get("bore_id_mm", 0.0)),
                    "outer_d_mm": float(entry.get("outer_d_mm", 0.0)),
                    "width_mm":   float(entry.get("width_mm", 0.0)),
                },
                "deviation": dev,
                "confidence": round(1.0 / (1.0 + dev["total_mm"]), 4),
            })

        candidates.sort(key=lambda m: m["deviation"]["total_mm"])
        matches = candidates[:3]

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "matches": matches,
                "query": {
                    "outer_d_mm": args.outer_d_mm,
                    "bore_d_mm":  args.bore_d_mm,
                    "width_mm":   args.width_mm,
                },
            },
        )
