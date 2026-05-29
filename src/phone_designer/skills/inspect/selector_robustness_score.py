"""selector_robustness_score — atomic, read-only.

LLM 가 plan 의 selector 를 만들 때 "이 selector 가 robust 한가" 를 0..1 점수로
받는다. 1.0 = 정확히 1 개 매칭 (이상적). 0.0 = zero match 또는 너무 많이 매칭
(애매한 selector — 후속 skill 이 어느 것 잡을지 unstable). 점수는 단조롭게:

    matched == 0           → 0.0   (아예 못 잡음)
    matched == 1           → 1.0   (이상적)
    matched == 2           → 0.6
    matched == 3           → 0.45
    matched == 4           → 0.35
    matched == 5           → 0.25
    matched >= 6           → 0.10 (사실상 ambiguous)

또한 stability_rank (selectors._STABILITY_RANK) 가 낮은 selector kind 는 약간
감점한다 (예: edges_by_position 는 좌표 변경에 약함).

v2 (planned, not implemented): small perturbations — body 의 face 위치를 ±EPS
이동시킨 가상 shape 에서 같은 selector 가 같은 갯수 잡는지. 본 v1 은 placeholder
로 `alternatives_suggested=[]` 만 채운다.

extras["selector_score"] = {
    "score":                  float,    # 0..1
    "matched":                int,
    "kind":                   str,
    "stability_rank":         int,
    "alternatives_suggested": [],       # v2 reserved
    "error":                  str | None,
}
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import (
    SelectorBase,
    _STABILITY_RANK,
    selector_from_dict,
)
from phone_designer.skills._spec import SkillBase, SkillResult


# matched count → base score. 1 = ideal.
_COUNT_SCORE = {
    0: 0.0,
    1: 1.0,
    2: 0.6,
    3: 0.45,
    4: 0.35,
    5: 0.25,
}


def _count_to_score(n: int) -> float:
    if n in _COUNT_SCORE:
        return _COUNT_SCORE[n]
    if n < 0:
        return 0.0
    return 0.10  # >= 6 매칭 — ambiguous


def _stability_weight(rank: int) -> float:
    """stability_rank (1..5) → multiplier (0.6 .. 1.0).

    rank 5 (tagged):                weight 1.0
    rank 4 (face_named):            weight 0.95
    rank 3 (axis_aligned, normal):  weight 0.90
    rank 2 (length, area):          weight 0.80
    rank 1 (edges_by_position):     weight 0.65
    """
    table = {5: 1.0, 4: 0.95, 3: 0.9, 2: 0.8, 1: 0.65}
    return table.get(rank, 0.75)


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(d: Any) -> SelectorBase:
    if isinstance(d, SelectorBase):
        return d
    if isinstance(d, dict):
        return selector_from_dict(d)
    raise TypeError(f"unsupported selector type: {type(d).__name__}")


def _try_resolve_both_kinds(shape, sel: SelectorBase, body) -> tuple[int, str | None]:
    """Resolver 가 face/edge 어떤 kind 인지 모르므로 둘 다 시도. 성공한 쪽의
    카운트 + 인지된 kind 반환. 둘 다 NotImplemented 면 error 메시지."""
    from phone_designer.skills._resolvers import resolve_edges, resolve_faces

    # face 우선 (대부분의 unstable selector 가 face 기반)
    face_n: int | None = None
    edge_n: int | None = None
    last_err: str | None = None

    try:
        face_n = len(resolve_faces(shape, sel, body=body))
    except NotImplementedError:
        face_n = None
    except Exception as ex:
        last_err = f"face resolve failed: {type(ex).__name__}: {ex}"
        face_n = None

    try:
        edge_n = len(resolve_edges(shape, sel))
    except NotImplementedError:
        edge_n = None
    except Exception as ex:
        last_err = f"edge resolve failed: {type(ex).__name__}: {ex}"
        edge_n = None

    # 결정 로직: 둘 다 잡으면 큰 쪽 (정보량 많은 쪽), 한쪽만 잡으면 그쪽.
    if face_n is not None and edge_n is not None:
        if face_n >= edge_n:
            return face_n, last_err
        return edge_n, last_err
    if face_n is not None:
        return face_n, last_err
    if edge_n is not None:
        return edge_n, last_err
    return 0, last_err or "no resolver matched (NotImplementedError)"


@skill(
    name="selector_robustness_score",
    category="inspect",
    level="atomic",
    summary="Score a selector on a 0..1 scale based on how unambiguously it "
            "matches the current body. 1.0 = exactly one entity matched "
            "(ideal). 0.0 = zero matches. Multi-match is penalized. The "
            "selector's intrinsic stability rank (tagged > face_named > "
            "geometric) further weights the score.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "axis_aligned_edges", "edges_on_face", "edges_by_length",
        "edges_by_position", "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["selector_robustness_score"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class SelectorRobustnessScore(SkillBase):
    class Args(BaseModel):
        selector_dict: dict

    def _apply(self, body: Any, args: Args) -> SkillResult:
        extras: dict[str, Any] = {
            "selector_score": {
                "score": 0.0,
                "matched": 0,
                "kind": "unknown",
                "stability_rank": 0,
                "alternatives_suggested": [],
                "error": None,
            },
        }

        # Parse selector
        try:
            sel = _coerce_selector(args.selector_dict)
        except Exception as ex:
            extras["selector_score"]["error"] = (
                f"selector parse failed: {type(ex).__name__}: {ex}"
            )
            return SkillResult(
                body=body, history=EntityHistoryMap(), extras=extras,
            )

        kind = sel.kind
        rank = _STABILITY_RANK.get(kind, 1)
        extras["selector_score"]["kind"] = kind
        extras["selector_score"]["stability_rank"] = rank

        shape = _occt_shape(body)
        matched, err = _try_resolve_both_kinds(shape, sel, body)
        extras["selector_score"]["matched"] = matched
        if err:
            extras["selector_score"]["error"] = err

        # Score: base from count, weighted by stability.
        base = _count_to_score(matched)
        weight = _stability_weight(rank)
        score = round(base * weight, 4)
        extras["selector_score"]["score"] = score

        return SkillResult(
            body=body, history=EntityHistoryMap(), extras=extras,
        )
