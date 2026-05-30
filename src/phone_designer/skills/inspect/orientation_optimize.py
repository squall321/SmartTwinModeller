"""orientation_optimize — atomic, read-only 3D printing DFM inspect.

Given a list of candidate build directions, run the three other 3D printing
inspect skills (overhang_check, support_volume_estimate, bridge_length_check)
on each candidate and combine their outputs into a single ``score``. The
candidate with the lowest score wins (lower is better — fewer overhangs, less
support, shorter bridges).

Score formula:
    score = overhang_count * w_oh
          + support_volume / 1000 * w_sv
          + max_bridge * w_br

Default weights heavily penalise unsupported bridges (printing fails) and
support volume (waste + post-processing), while overhang count is a softer
guide.

extras["best_orientation"] schema:
    {
        "direction": (x, y, z),
        "score": float,
        "per_candidate": [
            {"direction": (...), "overhang_count": int,
             "support_volume_mm3": float, "max_bridge_mm": float,
             "score": float},
            ...
        ],
    }
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="orientation_optimize",
    category="inspect",
    level="atomic",
    summary="Try several build directions and pick the best one by combined "
            "score over overhang_count, support_volume and max bridge length. "
            "Lower score is better. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["orientation_report"],
    preserves=["body_topology"],
    manufacturing={
        "fdm":  {"extras": {"orient_for": "minimum_support"}},
        "sla":  {"extras": {"orient_for": "minimum_support"}},
        "sls":  {"extras": {"orient_for": "minimum_footprint"}},
    },
    failure_modes=["fm.no_candidates"],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="body_present")],
)
class OrientationOptimize(SkillBase):
    class Args(BaseModel):
        candidates: list[tuple[float, float, float]] = Field(
            default=[(0.0, 0.0, 1.0), (1.0, 0.0, 0.0),
                     (0.0, 1.0, 0.0), (0.0, 0.0, -1.0)],
        )
        max_overhang_deg: float = Field(default=45.0, ge=0.0, le=90.0)
        support_density: float = Field(default=0.2, ge=0.0, le=1.0)
        max_bridge_mm: float = Field(default=10.0, gt=0.0, le=500.0)
        weight_overhang: float = Field(default=1.0, ge=0.0)
        weight_support: float = Field(default=1.0, ge=0.0)
        weight_bridge: float = Field(default=2.0, ge=0.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.inspect.bridge_length_check import (
            BridgeLengthCheck,
        )
        from phone_designer.skills.inspect.overhang_check import OverhangCheck
        from phone_designer.skills.inspect.support_volume_estimate import (
            SupportVolumeEstimate,
        )

        if not args.candidates:
            raise ValueError(
                "orientation_optimize: candidates must contain at least one direction",
            )

        per_candidate: list[dict[str, Any]] = []
        best_dir = args.candidates[0]
        best_score = float("inf")

        for cand in args.candidates:
            try:
                oh = OverhangCheck().apply(body, {
                    "build_direction": cand,
                    "max_angle_deg": args.max_overhang_deg,
                }).extras
                sv = SupportVolumeEstimate().apply(body, {
                    "build_direction": cand,
                    "max_overhang_deg": args.max_overhang_deg,
                    "support_density": args.support_density,
                }).extras
                br = BridgeLengthCheck().apply(body, {
                    "max_bridge_mm": args.max_bridge_mm,
                    "build_direction": cand,
                }).extras
            except Exception as exc:
                per_candidate.append({
                    "direction": tuple(round(c, 4) for c in cand),
                    "error": str(exc),
                    "overhang_count": -1,
                    "support_volume_mm3": -1.0,
                    "max_bridge_mm": -1.0,
                    "score": float("inf"),
                })
                continue

            oh_count = int(oh["overhang_summary"]["support_face_count"])
            sv_vol = float(sv["support_estimate"]["total_support_volume_mm3"])
            br_max = float(br["bridge_summary"]["max_unsupported_length_mm"])

            score = (
                oh_count * args.weight_overhang
                + (sv_vol / 1000.0) * args.weight_support
                + br_max * args.weight_bridge
            )
            entry = {
                "direction": tuple(round(c, 4) for c in cand),
                "overhang_count": oh_count,
                "support_volume_mm3": round(sv_vol, 4),
                "max_bridge_mm": round(br_max, 4),
                "score": round(score, 4),
            }
            per_candidate.append(entry)
            if score < best_score:
                best_score = score
                best_dir = cand

        report = {
            "direction": tuple(round(c, 4) for c in best_dir),
            "score": round(best_score, 4) if best_score != float("inf") else None,
            "per_candidate": per_candidate,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"best_orientation": report},
        )
