"""match_standard_hole — atomic, read-only.

Given a measured hole diameter (and optional depth + desired fit kind), look
the value up against ``catalogs/standards/threads_metric.yaml`` (and
``threads_imperial.yaml`` when available) and return the top 3 closest standard
thread specs ranked by diameter proximity.

Body is not modified — post_conditions=[body_present].

extras["matches"] = [
    {
        "thread_spec":        "M3",
        "fit":                "medium",
        "catalog":            "metric",
        "catalog_diameter_mm": 3.4,
        "deviation_mm":       0.05,
        "confidence":         0.95,
    },
    ...
]
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# ──────────────────────────────────────────────────────────────────────────────
# Catalog loader (inline — per pack rules).


def _load(family, name):
    import yaml, pathlib
    # parents: [0]=inspect, [1]=skills, [2]=phone_designer, [3]=src, [4]=repo root
    root = pathlib.Path(__file__).resolve().parents[4]
    path = root / "catalogs" / family / f"{name}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())


# Fit kind → ordered list of catalog field names tried in order.
_FIT_FIELDS_METRIC: dict[str, list[tuple[str, str]]] = {
    "clearance": [
        ("clearance_close_mm", "close"),
        ("clearance_medium_mm", "medium"),
        ("clearance_coarse_mm", "coarse"),
    ],
    "tap": [
        ("tap_drill_mm", "tap"),
    ],
    "auto": [
        ("clearance_close_mm", "close"),
        ("clearance_medium_mm", "medium"),
        ("clearance_coarse_mm", "coarse"),
        ("tap_drill_mm", "tap"),
        ("outer_d_mm", "outer"),
    ],
}


def _gather_candidates(
    hole_d: float, fit_kind: str,
) -> list[dict[str, Any]]:
    """Build a flat list of {thread_spec, fit, catalog, catalog_diameter_mm,
    deviation_mm, confidence} candidates across metric + imperial catalogs."""
    out: list[dict[str, Any]] = []

    metric = _load("standards", "threads_metric")
    if metric is not None:
        fields = _FIT_FIELDS_METRIC.get(fit_kind, _FIT_FIELDS_METRIC["auto"])
        for spec, entry in metric.get("threads", {}).items():
            for field_name, fit_label in fields:
                cat_d = entry.get(field_name)
                if cat_d is None:
                    continue
                deviation = abs(hole_d - float(cat_d))
                out.append({
                    "thread_spec": spec,
                    "fit": fit_label,
                    "catalog": "metric",
                    "catalog_diameter_mm": round(float(cat_d), 4),
                    "deviation_mm": round(deviation, 4),
                    "confidence": round(1.0 / (1.0 + deviation), 4),
                })

    imperial = _load("standards", "threads_imperial")
    if imperial is not None:
        for spec, entry in imperial.get("threads", {}).items():
            for field_name in (
                "clearance_close_mm", "clearance_medium_mm",
                "clearance_coarse_mm", "tap_drill_mm", "outer_d_mm",
            ):
                cat_d = entry.get(field_name)
                if cat_d is None:
                    continue
                fit_label = (
                    "close" if "close" in field_name
                    else "medium" if "medium" in field_name
                    else "coarse" if "coarse" in field_name
                    else "tap" if "tap" in field_name
                    else "outer"
                )
                deviation = abs(hole_d - float(cat_d))
                out.append({
                    "thread_spec": spec,
                    "fit": fit_label,
                    "catalog": "imperial",
                    "catalog_diameter_mm": round(float(cat_d), 4),
                    "deviation_mm": round(deviation, 4),
                    "confidence": round(1.0 / (1.0 + deviation), 4),
                })

    return out


@skill(
    name="match_standard_hole",
    category="inspect",
    level="atomic",
    summary="Look a measured hole diameter up against the metric/imperial "
            "thread catalogs and return the top 3 closest standard specs by "
            "diameter proximity. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["standard_hole_match"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class MatchStandardHole(SkillBase):
    class Args(BaseModel):
        hole_diameter_mm: float = Field(gt=0, le=200)
        depth_mm: float | None = Field(default=None, gt=0, le=500)
        fit_kind: Literal["clearance", "tap", "auto"] = "auto"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        candidates = _gather_candidates(args.hole_diameter_mm, args.fit_kind)

        # Sort by deviation_mm ascending → take top 3.
        candidates.sort(key=lambda m: m["deviation_mm"])
        matches = candidates[:3]

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "matches": matches,
                "query": {
                    "hole_diameter_mm": args.hole_diameter_mm,
                    "depth_mm": args.depth_mm,
                    "fit_kind": args.fit_kind,
                },
            },
        )
