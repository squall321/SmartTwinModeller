"""find_skill_by_intent — atomic, read-only.

LLM 가 자연어 의도("나는 카메라 링을 추가하고 싶다", "내부 캐비티 파야 함")
로부터 가장 적합한 skill 을 찾도록 도와주는 search helper. embedding 없이
v1 휴리스틱:
    1. 쿼리 토큰 + bigram 추출
    2. 각 등록 skill 의 (name + category + summary) 에 대해 같은 추출
    3. unigram overlap + bigram overlap 으로 score 계산
    4. top_k 반환

Body 는 read-only 로 그대로 통과 (None 이어도 OK — registry 검색이지 형상 검사 아님).
하지만 post_condition=body_present 이므로 body=None 이면 fail. 호출자는 어떤
body 든 한 개 넘겨야 한다 (placeholder 가능).

result.extras schema:
    {"suggestions": [
        {"skill_name": str, "summary": str, "score": float,
         "category": str, "level": str,
         "unigram_overlap": int, "bigram_overlap": int},
        ...
    ],
     "query": str,
     "query_tokens": [str, ...],
     "total_searched": int}
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import registry, skill, skill
from phone_designer.skills._spec import SkillBase, SkillResult


# Common English/Korean stopwords — short list, conservative.
_STOPWORDS = frozenset({
    "a", "an", "and", "the", "of", "to", "for", "in", "on", "at", "with",
    "is", "are", "be", "this", "that", "it", "by", "as", "or", "i", "we",
    "my", "you", "your", "from", "into", "make", "want", "need", "would",
    "should", "can", "could", "do", "does", "did", "have", "has", "had",
    "그", "이", "저", "을", "를", "은", "는", "이", "가", "에", "에서",
    "와", "과", "도", "만", "도록", "하다", "하는", "하고",
})


def _tokenize(text: str) -> list[str]:
    """소문자 + 영숫자/한글 추출. stopword 제거. 1 글자 영숫자 제거."""
    # 영숫자, 한글, underscore 만 유지
    text = text.lower()
    raw = re.findall(r"[a-z0-9_]+|[가-힯]+", text)
    out = []
    for tok in raw:
        if tok in _STOPWORDS:
            continue
        if len(tok) == 1 and tok.isascii():
            continue
        out.append(tok)
    return out


def _bigrams(tokens: list[str]) -> set[tuple[str, str]]:
    return {(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)}


def _score(
    q_tokens: set[str],
    q_bigrams: set[tuple[str, str]],
    s_tokens: set[str],
    s_bigrams: set[tuple[str, str]],
) -> tuple[float, int, int]:
    """Return (score, unigram_overlap, bigram_overlap)."""
    uni = len(q_tokens & s_tokens)
    bi = len(q_bigrams & s_bigrams)
    # unigram 가중 1.0, bigram 가중 2.0 — phrase 매칭 중시.
    # 정규화: 쿼리 토큰 크기로 나눠 0..1+ 범위 (bigram 보너스로 1 초과 가능).
    denom = max(1, len(q_tokens))
    score = (uni * 1.0 + bi * 2.0) / denom
    return (score, uni, bi)


@skill(
    name="find_skill_by_intent",
    category="inspect",
    level="atomic",
    summary="Search the skill registry for the closest matches to a "
            "natural-language intent query, ranked by token + bigram overlap. "
            "v1 heuristic (no embeddings). Returns top_k suggestions with "
            "skill_name, summary, score, category.",
    selector_kinds=[],
    history_rules={},
    produces_features=["skill_suggestions"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class FindSkillByIntent(SkillBase):
    class Args(BaseModel):
        natural_language_query: str = Field(min_length=1)
        top_k: int = Field(default=5, ge=1, le=50)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        q_tokens_list = _tokenize(args.natural_language_query)
        q_tokens = set(q_tokens_list)
        q_bigrams = _bigrams(q_tokens_list)

        scored: list[tuple[float, int, int, Any]] = []
        all_specs = registry.all()
        for spec in all_specs:
            # Search name + category + summary
            haystack = f"{spec.name} {spec.category} {spec.summary}"
            s_tokens_list = _tokenize(haystack)
            s_tokens = set(s_tokens_list)
            s_bigrams = _bigrams(s_tokens_list)
            score, uni, bi = _score(q_tokens, q_bigrams, s_tokens, s_bigrams)
            if score > 0.0:
                scored.append((score, uni, bi, spec))

        # Sort by score desc, tie-break by unigram desc, then name asc.
        scored.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3].name))

        suggestions = []
        for score, uni, bi, spec in scored[: args.top_k]:
            suggestions.append({
                "skill_name": spec.name,
                "summary": spec.summary,
                "score": round(float(score), 4),
                "category": spec.category,
                "level": spec.level,
                "unigram_overlap": uni,
                "bigram_overlap": bi,
            })

        extras = {
            "suggestions": suggestions,
            "query": args.natural_language_query,
            "query_tokens": q_tokens_list,
            "total_searched": len(all_specs),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
