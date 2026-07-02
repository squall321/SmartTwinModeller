"""Parameter table + ``{"$expr": ...}`` resolution — Plan schema v2.

A v2 plan carries ``parameters: {name: {value, unit, description}}`` and any
step arg (nested at any depth inside dicts/lists) may be an EXPRESSION NODE::

    {"$expr": "housing_length/2 - wall"}

resolved against the parameter table before execution. ``value`` itself may be
a str expression over *other* parameter names (derived parameter), which is
what makes cycle detection necessary (``a = "b+1"``, ``b = "a+1"``).

Whitelist policy — REUSED from ``manufacturing/string_eval.py`` (the project's
existing simpleeval wrapper): arithmetic operators + ``ALLOWED_FUNCTIONS``
(min/max/abs/round), no eval/exec, no attribute access. Why this module drives
``SimpleEval`` itself instead of calling ``string_eval.safe_eval``: that
wrapper deliberately swallows every evaluation failure into ``None`` (its
ProcessRule callers use None-as-fallback semantics). Schema v2 requires
STRUCTURED refusals — ``fm.expr_error`` with the raw cause preserved — for
undefined names / cycles / syntax errors; masking them as None would violate
the "raw errors are never masked" house rule. Same policy, honest errors.

All refusals raise :class:`ExprError` (a ValueError whose message starts with
``fm.expr_error:``) so callers/tests can match the structured token.
"""
from __future__ import annotations

import ast
import math
from typing import Any, TYPE_CHECKING

from simpleeval import SimpleEval

from phone_designer.manufacturing.string_eval import ALLOWED_FUNCTIONS

if TYPE_CHECKING:  # pragma: no cover
    from phone_designer.plan.model import Plan


#: The reserved key that marks a dict node as an expression.
EXPR_KEY = "$expr"


class ExprError(ValueError):
    """Structured expression refusal — message ALWAYS starts 'fm.expr_error: '."""

    def __init__(self, detail: str):
        super().__init__(f"fm.expr_error: {detail}")


def _expr_names(expr: str, *, context: str) -> set[str]:
    """Free identifiers referenced by ``expr`` (whitelisted functions excluded).

    Uses ``ast`` so dependency extraction is exact — this is what lets the
    resolver do DFS cycle detection instead of eval-retry guessing.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExprError(
            f"{context}: invalid expression {expr!r} — SyntaxError: {exc}"
        ) from exc
    return {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } - set(ALLOWED_FUNCTIONS)


def _check_numeric(value: Any, expr: str, context: str) -> Any:
    if isinstance(value, bool):
        # comparisons ("wall > 2") are legal simpleeval output and some skill
        # args are booleans — pass through as-is.
        return value
    if not isinstance(value, (int, float)):
        raise ExprError(
            f"{context}: expression {expr!r} evaluated to non-numeric "
            f"{type(value).__name__}")
    if not math.isfinite(value):
        # strict-JSON-safe outputs: inf/nan may never leave the resolver.
        raise ExprError(
            f"{context}: expression {expr!r} evaluated to non-finite {value!r} "
            f"(strict-JSON-safe outputs forbid inf/nan)")
    return value


def eval_expr(expr: str, table: dict[str, float], *, context: str = "expr") -> Any:
    """Evaluate one expression against a FULLY-RESOLVED name table.

    Raises ExprError (fm.expr_error) for: non-str/empty expr, syntax error,
    undefined name, evaluation failure (raw cause appended, never masked),
    non-numeric or non-finite result.
    """
    if not isinstance(expr, str) or not expr.strip():
        raise ExprError(
            f"{context}: {EXPR_KEY} value must be a non-empty string "
            f"expression, got {expr!r}")
    missing = sorted(n for n in _expr_names(expr, context=context)
                     if n not in table)
    if missing:
        raise ExprError(
            f"{context}: undefined parameter name(s) {missing} in expression "
            f"{expr!r} (defined: {sorted(table)})")
    evaluator = SimpleEval(functions=dict(ALLOWED_FUNCTIONS))
    evaluator.names = dict(table)
    try:
        out = evaluator.eval(expr)
    except Exception as exc:  # raw cause preserved — never masked
        raise ExprError(
            f"{context}: expression {expr!r} failed to evaluate — "
            f"{type(exc).__name__}: {exc}") from exc
    return _check_numeric(out, expr, context)


def resolve_parameter_table(
    parameters: dict[str, Any] | None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Parameter table (+ optional overrides) → fully-resolved ``{name: float}``.

    ``parameters`` values may be ParameterDef instances, ``{"value": ...}``
    dicts, or bare numbers (MCP-caller shorthand). ``value`` may be a str
    expression over other parameter names — resolved by DFS with cycle
    detection. An override PINS a parameter (including a derived one) to a
    literal number; overriding a name not in the table refuses.
    """
    raw: dict[str, Any] = {}
    for name, p in (parameters or {}).items():
        if hasattr(p, "value"):                     # ParameterDef
            raw[name] = p.value
        elif isinstance(p, dict):
            if "value" not in p:
                raise ExprError(f"parameter '{name}': missing 'value'")
            raw[name] = p["value"]
        else:                                       # bare number shorthand
            raw[name] = p

    for name, v in (overrides or {}).items():
        if name not in raw:
            raise ExprError(
                f"override for undefined parameter '{name}' "
                f"(defined: {sorted(raw)})")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ExprError(
                f"override '{name}' must be a number, got {type(v).__name__}")
        if not math.isfinite(v):
            raise ExprError(f"override '{name}' must be finite, got {v!r}")
        raw[name] = float(v)

    resolved: dict[str, float] = {}
    stack: list[str] = []      # DFS visiting stack → cycle detection

    def _resolve(name: str) -> float:
        if name in resolved:
            return resolved[name]
        if name in stack:
            cycle = stack[stack.index(name):] + [name]
            raise ExprError("parameter cycle detected: " + " -> ".join(cycle))
        if name not in raw:
            raise ExprError(
                f"undefined parameter '{name}' (defined: {sorted(raw)})")
        value = raw[name]
        stack.append(name)
        try:
            if isinstance(value, str):
                deps = _expr_names(value, context=f"parameter '{name}'")
                sub = {d: _resolve(d) for d in deps}
                out = eval_expr(value, sub, context=f"parameter '{name}'")
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ExprError(
                    f"parameter '{name}': value must be a number or an "
                    f"expression string, got {type(value).__name__}")
            elif not math.isfinite(value):
                raise ExprError(
                    f"parameter '{name}': non-finite value {value!r} "
                    f"(strict-JSON-safe)")
            else:
                out = value
        finally:
            stack.pop()
        resolved[name] = float(out)
        return resolved[name]

    for name in raw:
        _resolve(name)
    return resolved


def resolve_args(obj: Any, table: dict[str, float], *, _path: str = "args") -> Any:
    """Deep-walk ``obj`` replacing every ``{"$expr": <str>}`` node.

    An expr node must be EXACTLY ``{"$expr": <str>}`` — sibling keys refuse
    (fm.expr_error) rather than being silently dropped. Non-expr dicts/lists
    are walked recursively; scalars pass through untouched.
    """
    if isinstance(obj, dict):
        if EXPR_KEY in obj:
            extra = sorted(set(obj) - {EXPR_KEY})
            if extra:
                raise ExprError(
                    f"{_path}: an {EXPR_KEY} node must be exactly "
                    f"{{'{EXPR_KEY}': <str>}} — refusing to silently drop "
                    f"sibling keys {extra}")
            return eval_expr(obj[EXPR_KEY], table, context=_path)
        return {k: resolve_args(v, table, _path=f"{_path}.{k}")
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        out = [resolve_args(v, table, _path=f"{_path}[{i}]")
               for i, v in enumerate(obj)]
        return tuple(out) if isinstance(obj, tuple) else out
    return obj


def resolve_plan(
    plan: "Plan",
    overrides: dict[str, Any] | None = None,
) -> tuple["Plan", dict[str, float]]:
    """Deep-copy ``plan`` with every ``$expr`` arg node replaced by its value.

    Returns ``(resolved_plan, resolved_table)``. The returned plan's own
    parameter table is REWRITTEN to the resolved literal values (units and
    descriptions kept) so saving the variant plan is self-contained and
    reproducible. Overrides against a plan WITHOUT a parameter table refuse
    (fm.expr_error) — there is nothing to override.
    """
    from phone_designer.plan.model import ParameterDef

    if overrides and not plan.parameters:
        raise ExprError(
            "parameter overrides given but the plan has no parameter table "
            "(v1 plan, or 'parameters' omitted)")
    table = resolve_parameter_table(plan.parameters, overrides)
    out = plan.model_copy(deep=True)
    for step in out.steps:
        step.args = resolve_args(step.args, table, _path=f"step '{step.id}'")
    if plan.parameters:
        out.parameters = {
            name: ParameterDef(value=table[name], unit=p.unit,
                               description=p.description)
            for name, p in plan.parameters.items()
        }
    return out, table
