"""predict_post_conditions — atomic, read-only.

For LLM what-if planning: given a `skill_name` + the body's current volume +
proposed `target_args`, predict (without executing) how the skill's declared
post_conditions will resolve. Estimate ΔV magnitude from skill-family
heuristics when possible.

Heuristic ΔV estimators (atomic skills only; macros fall through to "unknown"):
    hole:                ΔV ≈ -π·(d/2)²·depth  (depth=200 if through)
    extrude_pocket:      ΔV ≈ -sketch_area_mm2 × depth_mm
    extrude_plateau:     ΔV ≈ +sketch_area_mm2 × height_mm
    extrude_through:     ΔV ≈ -sketch_area_mm2 × (estimated_thickness or 50)
    counterbore_hole:    sum of two cylinders (heuristic)
    countersink_hole:    cylinder + cone (heuristic)
    Otherwise: derive sign from post_conditions but mark magnitude=None.

sketch_area_mm2 estimation (for SketchSpec dicts):
    circle:  π·(d/2)²
    rect:    w·h
    polygon: regular polygon area, n_sides·s²·cot(π/n)/4  (best-effort)
    composite/path/text: None

result.extras["prediction"] schema:
    {
        "skill_name": str,
        "volume_delta_estimate_mm3": float | None,
        "volume_delta_sign": "negative" | "positive" | "unchanged" | "unknown",
        "post_conditions_expected": [
            {"kind": str, "min_delta_mm3": float, "allow_no_change": bool,
             "predicted_pass": bool}, ...
        ],
        "current_volume_mm3": float,
        "predicted_volume_mm3": float | None,
        "notes": [str, ...],   # human-readable reasoning bullets
        "error": str | None,   # unknown skill → message, no exception
    }
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import registry, skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _sketch_area(sketch: Any) -> float | None:
    """Best-effort area estimator for a SketchSpec dict."""
    if not isinstance(sketch, dict):
        return None
    kind = sketch.get("kind")
    if kind == "circle":
        d = sketch.get("diameter_mm")
        if isinstance(d, (int, float)) and d > 0:
            return math.pi * (d / 2.0) ** 2
        r = sketch.get("radius_mm")
        if isinstance(r, (int, float)) and r > 0:
            return math.pi * r ** 2
    if kind == "rect" or kind == "rectangle":
        w = sketch.get("width_mm")
        h = sketch.get("height_mm")
        if isinstance(w, (int, float)) and isinstance(h, (int, float)):
            return float(w) * float(h)
    if kind == "polygon":
        n = sketch.get("n_sides") or sketch.get("sides")
        s = sketch.get("side_mm") or sketch.get("size_mm")
        if isinstance(n, int) and n >= 3 and isinstance(s, (int, float)):
            return (n * s * s) / (4.0 * math.tan(math.pi / n))
    return None


def _estimate_hole(args: dict) -> float | None:
    d = args.get("diameter_mm")
    depth = args.get("depth_mm")
    if not isinstance(d, (int, float)) or d <= 0:
        return None
    # through-hole heuristic — assume 200mm of travel (matches Hole._apply).
    depth_val = depth if isinstance(depth, (int, float)) and depth > 0 else 200.0
    return -(math.pi * (d / 2.0) ** 2 * depth_val)


def _estimate_pocket(args: dict) -> float | None:
    sketch = args.get("sketch")
    depth = args.get("depth_mm")
    area = _sketch_area(sketch)
    if area is None or not isinstance(depth, (int, float)) or depth <= 0:
        return None
    return -area * depth


def _estimate_plateau(args: dict) -> float | None:
    sketch = args.get("sketch")
    height = args.get("height_mm") or args.get("depth_mm")
    area = _sketch_area(sketch)
    if area is None or not isinstance(height, (int, float)) or height <= 0:
        return None
    return area * height


def _estimate_through(args: dict) -> float | None:
    sketch = args.get("sketch")
    area = _sketch_area(sketch)
    if area is None:
        return None
    # Through cut without depth — assume a body thickness for magnitude.
    body_thickness_mm = args.get("body_thickness_hint_mm", 50.0)
    return -area * float(body_thickness_mm)


_ESTIMATORS = {
    "hole": _estimate_hole,
    "extrude_pocket": _estimate_pocket,
    "extrude_pocket_blended": _estimate_pocket,
    "extrude_plateau": _estimate_plateau,
    "extrude_through": _estimate_through,
}


def _sign_from_post_conditions(
    pcs: list[PostCondition] | None,
) -> str:
    if not pcs:
        return "unknown"
    kinds = {getattr(p, "kind", None) for p in pcs}
    if "volume_decreased" in kinds:
        return "negative"
    if "volume_increased" in kinds:
        return "positive"
    if "volume_changed" in kinds:
        return "changed"
    if kinds == {"body_present"}:
        return "unchanged"
    return "unknown"


def _predict_pass(
    pc: PostCondition,
    delta: float | None,
    body_present: bool,
) -> bool:
    """Predict whether a given post_condition will pass given estimated delta."""
    if pc.kind == "body_present":
        return body_present
    if delta is None:
        # No magnitude estimate — we still know sign-of-intent from family.
        return True  # optimistic — caller knows it's an estimate.
    if pc.kind == "volume_decreased":
        return delta < -pc.min_delta_mm3
    if pc.kind == "volume_increased":
        return delta > pc.min_delta_mm3
    if pc.kind == "volume_changed":
        return abs(delta) > pc.min_delta_mm3
    if pc.kind == "face_count_changed":
        # We can't estimate face count without running — say True if material moved.
        return abs(delta) > 0.0
    return True


@skill(
    name="predict_post_conditions",
    category="inspect",
    level="atomic",
    summary="Estimate the volume delta and predict pass/fail of each declared "
            "post_condition for a target skill, given the current body volume "
            "and proposed args. Closed-form heuristic for hole / pocket / plateau "
            "/ through; otherwise sign derived from post_conditions.",
    selector_kinds=[],
    history_rules={},
    produces_features=["volume_prediction"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.02,
    post_conditions=[PostCondition(kind="body_present")],
)
class PredictPostConditions(SkillBase):
    class Args(BaseModel):
        skill_name: str = Field(min_length=1)
        current_body_volume_mm3: float = Field(ge=0.0)
        target_args: dict = Field(default_factory=dict)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        notes: list[str] = []
        try:
            spec = registry.get(args.skill_name)
        except KeyError as ex:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={
                    "prediction": {
                        "skill_name": args.skill_name,
                        "volume_delta_estimate_mm3": None,
                        "volume_delta_sign": "unknown",
                        "post_conditions_expected": [],
                        "current_volume_mm3": args.current_body_volume_mm3,
                        "predicted_volume_mm3": None,
                        "notes": [],
                        "error": f"unknown skill: {ex}",
                    },
                },
            )

        # Magnitude estimate (best-effort).
        delta: float | None = None
        estimator = _ESTIMATORS.get(args.skill_name)
        if estimator is not None:
            try:
                delta = estimator(args.target_args)
                if delta is not None:
                    notes.append(
                        f"Estimated ΔV from {args.skill_name} heuristic: "
                        f"{delta:.2f} mm³."
                    )
                else:
                    notes.append(
                        f"{args.skill_name} estimator could not derive magnitude "
                        f"from target_args (missing required field?)."
                    )
            except Exception as ex:
                notes.append(f"estimator raised: {type(ex).__name__}: {ex}")

        sign = _sign_from_post_conditions(spec.post_conditions)
        if delta is None and sign in ("negative", "positive"):
            notes.append(
                f"No magnitude available; sign={sign} from declared "
                f"post_conditions."
            )
        if delta is not None:
            if delta < -1e-9:
                sign = "negative"
            elif delta > 1e-9:
                sign = "positive"
            else:
                sign = "unchanged"

        predicted_volume = (
            args.current_body_volume_mm3 + delta
            if delta is not None
            else None
        )
        body_present = predicted_volume is None or predicted_volume > 0.0

        pcs_out: list[dict[str, Any]] = []
        for pc in spec.post_conditions or []:
            pcs_out.append({
                "kind": pc.kind,
                "min_delta_mm3": pc.min_delta_mm3,
                "allow_no_change": pc.allow_no_change,
                "predicted_pass": _predict_pass(pc, delta, body_present),
            })

        report = {
            "skill_name": args.skill_name,
            "volume_delta_estimate_mm3": (
                round(delta, 4) if delta is not None else None
            ),
            "volume_delta_sign": sign,
            "post_conditions_expected": pcs_out,
            "current_volume_mm3": round(float(args.current_body_volume_mm3), 4),
            "predicted_volume_mm3": (
                round(predicted_volume, 4)
                if predicted_volume is not None else None
            ),
            "notes": notes,
            "error": None,
        }

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"prediction": report},
        )
