"""find_similar_skills — atomic, read-only.

For LLM planning: given a registered skill_name, find the other skills closest
to it by (a) category overlap and (b) keyword overlap on summary + name.

Similarity score (0..1):
    base = jaccard(tokens_A, tokens_B)
    + 0.30  if categories share top-level (e.g., 'modify/pocket' vs 'modify/boss')
    + 0.20  if categories match exactly
    + 0.10  if both same `level` (atomic vs macro)
clamped to 1.0.

result.extras["similar"] schema:
    [{"skill_name": str, "similarity": float, "summary": str,
      "category": str, "level": str, "shared_tokens": [str, ...]}, ...]

post_condition=body_present.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import registry, skill
from phone_designer.skills._spec import SkillBase, SkillResult


_STOPWORDS = frozenset({
    "a", "an", "and", "the", "of", "to", "for", "in", "on", "at", "with",
    "is", "are", "be", "this", "that", "it", "by", "or", "as",
    "skill", "atomic", "macro", "body", "use", "uses",
    "그", "이", "저", "을", "를", "은", "는", "에", "에서",
})


def _tokens(text: str) -> set[str]:
    text = text.lower()
    raw = re.findall(r"[a-z0-9_]+|[가-힯]+", text)
    out: set[str] = set()
    for t in raw:
        if t in _STOPWORDS:
            continue
        if len(t) == 1 and t.isascii():
            continue
        out.add(t)
    return out


def _category_bonus(a: str, b: str) -> float:
    if a == b:
        return 0.20 + 0.30  # exact + top-level
    top_a = a.split("/", 1)[0]
    top_b = b.split("/", 1)[0]
    if top_a and top_a == top_b:
        return 0.30
    return 0.0


@skill(
    name="find_similar_skills",
    category="inspect",
    level="atomic",
    summary="Given a registered skill name, return the top_k other skills "
            "with the highest similarity by keyword overlap of name+summary "
            "and category proximity. Helpful when the LLM needs alternative "
            "verbs (e.g., extrude_pocket → extrude_through, swept_pocket_*).",
    selector_kinds=[],
    history_rules={},
    produces_features=["similar_skills"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class FindSimilarSkills(SkillBase):
    class Args(BaseModel):
        skill_name: str = Field(min_length=1)
        top_k: int = Field(default=5, ge=1, le=50)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        try:
            ref = registry.get(args.skill_name)
        except KeyError as ex:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={
                    "similar": [],
                    "skill_name": args.skill_name,
                    "error": f"unknown skill: {ex}",
                    "total_compared": 0,
                },
            )

        ref_haystack = f"{ref.name} {ref.summary}"
        ref_tokens = _tokens(ref_haystack)

        scored: list[tuple[float, set[str], Any]] = []
        for spec in registry.all():
            if spec.name == ref.name:
                continue
            spec_tokens = _tokens(f"{spec.name} {spec.summary}")
            union = ref_tokens | spec_tokens
            shared = ref_tokens & spec_tokens
            jacc = (len(shared) / len(union)) if union else 0.0
            score = jacc + _category_bonus(ref.category, spec.category)
            if ref.level == spec.level:
                score += 0.10
            score = min(1.0, score)
            if score > 0.0:
                scored.append((score, shared, spec))

        scored.sort(key=lambda x: (-x[0], -len(x[1]), x[2].name))

        similar = []
        for score, shared, spec in scored[: args.top_k]:
            similar.append({
                "skill_name": spec.name,
                "similarity": round(float(score), 4),
                "summary": spec.summary,
                "category": spec.category,
                "level": spec.level,
                "shared_tokens": sorted(shared),
            })

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "similar": similar,
                "skill_name": args.skill_name,
                "error": None,
                "total_compared": len(registry.all()) - 1,
            },
        )
