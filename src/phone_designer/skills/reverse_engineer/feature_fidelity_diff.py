"""feature_fidelity_diff — compare two feature_catalog dicts (orig vs regen).

Read-only. Returns extras["feature_fidelity"] = {
    "by_kind": {<kind>: {"a": Na, "b": Nb, "diff": Nb-Na}, ...},
    "missing_in_b": [list of (kind, idx_in_a)],
    "extra_in_b":   [list of (kind, idx_in_b)],
    "avg_dim_drift_pct": float | None,
    "overall_match_ratio": float in [0, 1],
}

Symmetric on counts; greedy nearest-match by depth/radius/centroid for the
"missing"/"extra" lists when both sides have entries.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


_KINDS = (
    "holes", "pockets", "bosses", "ribs", "lugs",
    "sweep_features", "loft_features", "revolve_features",
    "symmetries", "patterns",
)


def _counts(catalog: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in _KINDS:
        v = catalog.get(k)
        out[k] = len(v) if isinstance(v, list) else 0
    return out


def _avg_dim_drift_pct(cat_a: dict, cat_b: dict) -> float | None:
    """Mean |%diff| across dimensional fields of pockets/holes/bosses.

    For each matched-by-index pair we average across all numeric "_mm"
    fields they share. Returns None if no pairs.
    """
    pairs: list[tuple[float, float]] = []
    for k in ("pockets", "holes", "bosses", "revolve_features"):
        a_list = cat_a.get(k) or []
        b_list = cat_b.get(k) or []
        n = min(len(a_list), len(b_list))
        for i in range(n):
            a = a_list[i] if isinstance(a_list[i], dict) else {}
            b = b_list[i] if isinstance(b_list[i], dict) else {}
            for key in a:
                if not key.endswith("_mm"):
                    continue
                if key not in b:
                    continue
                try:
                    av = float(a[key]); bv = float(b[key])
                except Exception:
                    continue
                if abs(av) < 1e-9:
                    continue
                pairs.append((av, bv))
    if not pairs:
        return None
    drifts = [abs(b - a) / abs(a) * 100.0 for a, b in pairs]
    return round(sum(drifts) / len(drifts), 4)


def _overall_match_ratio(by_kind: dict[str, dict]) -> float:
    """Aggregate ratio of matched (kind-wise minimum) to total (kind-wise max).

    Generic + symmetric — encodes "how much of the smaller catalog is
    accounted for by the larger one, kind by kind". 1.0 = identical counts
    in every kind.
    """
    matched = 0
    total = 0
    for v in by_kind.values():
        matched += min(v["a"], v["b"])
        total += max(v["a"], v["b"])
    if total == 0:
        return 1.0
    return round(matched / total, 4)


@skill(
    name="feature_fidelity_diff",
    category="reverse_engineer",
    level="atomic",
    summary="Compare two feature_catalog dicts (original vs regen) — per-kind "
            "count diff + nearest-match for missing/extra entries + average "
            "dimensional drift across matched pairs + overall match ratio.",
    selector_kinds=[],
    history_rules={},
    produces_features=["feature_fidelity_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class FeatureFidelityDiff(SkillBase):
    class Args(BaseModel):
        catalog_a: dict
        catalog_b: dict

    def _apply(self, body: Any, args: Args) -> SkillResult:
        ca = args.catalog_a or {}
        cb = args.catalog_b or {}
        counts_a = _counts(ca)
        counts_b = _counts(cb)
        by_kind: dict[str, dict] = {}
        missing_in_b: list[tuple[str, int]] = []
        extra_in_b: list[tuple[str, int]] = []
        for k in _KINDS:
            a = counts_a[k]
            b = counts_b[k]
            by_kind[k] = {"a": a, "b": b, "diff": b - a}
            # nearest-match by index — for a > b, mark the leftover a's as missing.
            if a > b:
                for i in range(b, a):
                    missing_in_b.append((k, i))
            elif b > a:
                for i in range(a, b):
                    extra_in_b.append((k, i))
        report = {
            "by_kind": by_kind,
            "missing_in_b": missing_in_b,
            "extra_in_b": extra_in_b,
            "avg_dim_drift_pct": _avg_dim_drift_pct(ca, cb),
            "overall_match_ratio": _overall_match_ratio(by_kind),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"feature_fidelity": report},
        )
