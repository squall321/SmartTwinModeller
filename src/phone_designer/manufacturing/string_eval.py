"""simpleeval 기반 안전한 표현식 평가.

ProcessRule 의 값이 `"args.radius_mm * 0.5"` 같은 표현식일 때 평가.
화이트리스트: 산술 연산자 + min/max/abs/round 만. eval()/exec() 금지.

[[lat.md/manufacturing#string-evaluator]] 의 spec.
"""
from __future__ import annotations

from typing import Any

from simpleeval import SimpleEval


ALLOWED_FUNCTIONS = {
    "min": min, "max": max, "abs": abs, "round": round,
}


def safe_eval(expr: Any, *, args: Any | None = None) -> Any:
    """expression (str) 또는 numeric 값 → 평가 결과.

    Args:
        expr: str expression 또는 이미 numeric. str 만 평가.
        args: expression 에서 `args.<field>` 로 참조할 수 있는 객체.

    Returns:
        평가 결과 (보통 float / int / bool).
    """
    if not isinstance(expr, str):
        return expr

    evaluator = SimpleEval(functions=ALLOWED_FUNCTIONS)
    if args is not None:
        evaluator.names = {"args": args}

    try:
        return evaluator.eval(expr)
    except Exception:
        # 평가 실패 시 None — caller 가 적절한 fallback
        return None
