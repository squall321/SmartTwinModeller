"""suggest_selector_from_phrase — atomic, read-only.

LLM 자연어 phrase ("the top face", "all cylindrical holes", "side edges in X")
로부터 가장 적절한 selector JSON 트리를 휴리스틱으로 제안.

heuristic v1:
    - keywords matched against canonical patterns:
        "top" → face_named name="top"
        "bottom" → face_named name="bottom"
        "side"/"side face" → face_named name="side"
        "cylindrical"/"cylinder"/"round hole" → faces_by_area (approx,
            since we don't have a dedicated faces_by_type) OR best-effort
            tagged with hint
        "vertical edges"/"z edges" → axis_aligned_edges axis=Z
        "horizontal" + "x"/"y" → axis_aligned_edges axis=X/Y
        "tag"/"tagged"/"<NAME>" (UPPERCASE token) → tagged tag=<NAME>
        "large face"/"largest face" → faces_by_area min=<estimated>
        "small face" → faces_by_area max=<estimated>
        "convex edges" → edges_convex_only
        "concave edges" → edges_concave_only
        "facing up"/"upward" → faces_by_normal direction=(0,0,1)
        "facing down" → faces_by_normal direction=(0,0,-1)
    - confidence scored by # of distinctive keywords matched / len(phrase tokens).
    - top_k suggestions returned, sorted by confidence desc.

body is echoed unchanged. post_condition=body_present.

result.extras["selector_suggestions"] schema:
    [
      {"selector": {"kind": ..., ...},
       "confidence": float in [0, 1],
       "reasoning": str},
      ...
    ]
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


_STOPWORDS = frozenset({
    "the", "a", "an", "all", "every", "any", "of", "in", "on", "at",
    "with", "is", "are", "be", "this", "that", "it", "and", "or",
    "for", "to", "from", "by", "as", "i", "we", "you", "your",
    "그", "이", "저", "을", "를", "은", "는", "에", "에서", "의",
})


def _normalize(text: str) -> str:
    return text.lower().strip()


def _tokens(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*|[가-힯]+", text)
    return [t for t in raw if t.lower() not in _STOPWORDS]


def _has_any(text: str, words: list[str]) -> bool:
    nt = _normalize(text)
    return any(w in nt for w in words)


def _extract_uppercase_tag(phrase: str) -> str | None:
    """Find a likely tag token: uppercase word ≥3 chars (not common english)."""
    candidates = re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", phrase)
    if not candidates:
        return None
    # filter out common all-caps english nouns
    common = {"THE", "ALL", "AND", "FACE", "EDGE", "TOP", "BOTTOM"}
    for c in candidates:
        if c not in common:
            return c
    return None


def _build_suggestions(phrase: str) -> list[dict[str, Any]]:
    """Return list of {selector, confidence, reasoning}."""
    suggestions: list[dict[str, Any]] = []
    nphrase = _normalize(phrase)

    # 1. face_named heuristics
    if _has_any(nphrase, ["top face", "top surface", "upper face", " top "]) or nphrase.endswith("top") or nphrase.startswith("top"):
        suggestions.append({
            "selector": {"kind": "face_named", "name": "top"},
            "confidence": 0.92,
            "reasoning": "'top' keyword maps to face_named name='top' (stability rank 4).",
        })
    if _has_any(nphrase, ["bottom face", "bottom surface", "lower face", "bottom"]):
        suggestions.append({
            "selector": {"kind": "face_named", "name": "bottom"},
            "confidence": 0.92,
            "reasoning": "'bottom' keyword maps to face_named name='bottom'.",
        })
    if _has_any(nphrase, ["side face", "side surface", "lateral"]):
        suggestions.append({
            "selector": {"kind": "face_named", "name": "side"},
            "confidence": 0.85,
            "reasoning": "'side' keyword maps to face_named name='side'.",
        })

    # 2. tagged
    up = _extract_uppercase_tag(phrase)
    if up is not None:
        suggestions.append({
            "selector": {"kind": "tagged", "tag": up},
            "confidence": 0.95,
            "reasoning": f"Uppercase token '{up}' looks like a tag — tagged selector (stability rank 5, highest).",
        })
    if _has_any(nphrase, ["tagged", "tag "]):
        # Generic tagged hint without a specific name; caller fills in.
        suggestions.append({
            "selector": {"kind": "tagged", "tag": "TAG_NAME"},
            "confidence": 0.4,
            "reasoning": "'tagged' keyword present but no specific tag — placeholder tag (replace TAG_NAME).",
        })

    # 3. faces_by_normal — facing direction
    if _has_any(nphrase, ["facing up", "upward", "pointing up", "+z", "plus z"]):
        suggestions.append({
            "selector": {
                "kind": "faces_by_normal",
                "direction": [0.0, 0.0, 1.0],
                "tol_deg": 5.0,
            },
            "confidence": 0.8,
            "reasoning": "'facing up' → faces_by_normal direction=(0,0,1).",
        })
    if _has_any(nphrase, ["facing down", "downward", "pointing down", "-z", "minus z"]):
        suggestions.append({
            "selector": {
                "kind": "faces_by_normal",
                "direction": [0.0, 0.0, -1.0],
                "tol_deg": 5.0,
            },
            "confidence": 0.8,
            "reasoning": "'facing down' → faces_by_normal direction=(0,0,-1).",
        })
    if _has_any(nphrase, ["facing +x", "facing right"]):
        suggestions.append({
            "selector": {
                "kind": "faces_by_normal",
                "direction": [1.0, 0.0, 0.0],
                "tol_deg": 5.0,
            },
            "confidence": 0.75,
            "reasoning": "'facing +X / right' → faces_by_normal direction=(1,0,0).",
        })

    # 4. faces_by_area — large/small/cylindrical(closest analog)
    if _has_any(nphrase, ["large face", "largest face", "big face"]):
        suggestions.append({
            "selector": {"kind": "faces_by_area", "min": 100.0},
            "confidence": 0.55,
            "reasoning": "'large/largest face' → faces_by_area with min area; tune the threshold.",
        })
    if _has_any(nphrase, ["small face", "smallest face", "tiny face"]):
        suggestions.append({
            "selector": {"kind": "faces_by_area", "max": 10.0},
            "confidence": 0.55,
            "reasoning": "'small/smallest face' → faces_by_area with max area; tune the threshold.",
        })
    if _has_any(nphrase, ["cylindrical", "cylinder", "round hole", "circular hole", "cylindrical hole"]):
        # No native faces_by_type selector — closest analog is faces_by_area
        # (cylindrical inner faces tend to be small/medium) plus an advisory.
        suggestions.append({
            "selector": {"kind": "faces_by_area", "max": 50.0},
            "confidence": 0.35,
            "reasoning": "No faces_by_type selector exists; closest analog is "
                        "faces_by_area to pick small inner faces. Consider "
                        "using tagged selector after tagging the cylindrical "
                        "faces, or use inspect_geometry to filter by surface_type='cylinder'.",
        })

    # 5. axis_aligned_edges
    if _has_any(nphrase, ["vertical edge", "z edge", "z-axis edge", "vertical edges"]):
        suggestions.append({
            "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
            "confidence": 0.85,
            "reasoning": "'vertical / Z' edges → axis_aligned_edges axis=Z.",
        })
    if _has_any(nphrase, ["x edge", "x-axis edge", " x edges"]):
        suggestions.append({
            "selector": {"kind": "axis_aligned_edges", "axis": "X"},
            "confidence": 0.8,
            "reasoning": "'X edges' → axis_aligned_edges axis=X.",
        })
    if _has_any(nphrase, ["y edge", "y-axis edge", " y edges"]):
        suggestions.append({
            "selector": {"kind": "axis_aligned_edges", "axis": "Y"},
            "confidence": 0.8,
            "reasoning": "'Y edges' → axis_aligned_edges axis=Y.",
        })

    # 6. convex / concave edges
    if _has_any(nphrase, ["convex edge", "outer edge", "sharp edge"]):
        suggestions.append({
            "selector": {"kind": "edges_convex_only"},
            "confidence": 0.7,
            "reasoning": "'convex/outer/sharp edges' → edges_convex_only.",
        })
    if _has_any(nphrase, ["concave edge", "inner edge", "fillet edge"]):
        suggestions.append({
            "selector": {"kind": "edges_concave_only"},
            "confidence": 0.7,
            "reasoning": "'concave/inner edges' → edges_concave_only.",
        })

    # 7. fall-back when nothing matched
    if not suggestions:
        suggestions.append({
            "selector": {"kind": "face_named", "name": "top"},
            "confidence": 0.1,
            "reasoning": "No distinctive keyword matched. Default suggestion: face_named name='top'.",
        })

    # Sort by confidence desc, stable by kind name.
    suggestions.sort(
        key=lambda d: (-float(d["confidence"]), d["selector"].get("kind", "")),
    )
    return suggestions


@skill(
    name="suggest_selector_from_phrase",
    category="inspect",
    level="atomic",
    summary="Translate a natural-language phrase (e.g., 'the top face', "
            "'all cylindrical holes', 'vertical edges') into the most likely "
            "selector JSON tree(s). Heuristic; returns top_k ranked suggestions "
            "with confidence and reasoning. No body mutation.",
    selector_kinds=[],
    history_rules={},
    produces_features=["selector_suggestions"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.02,
    post_conditions=[PostCondition(kind="body_present")],
)
class SuggestSelectorFromPhrase(SkillBase):
    class Args(BaseModel):
        phrase: str = Field(min_length=1)
        top_k: int = Field(default=3, ge=1, le=20)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        all_suggestions = _build_suggestions(args.phrase)
        top = all_suggestions[: args.top_k]
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "selector_suggestions": top,
                "phrase": args.phrase,
                "total_candidates": len(all_suggestions),
            },
        )
