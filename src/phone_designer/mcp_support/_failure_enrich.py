"""_failure_enrich — machine-actionable failures + spec preflight (loop v1).

Two entry points, wired by mcp_server.py into the LLM self-correction loop:

  enrich_failures(step_report, *, body=None) -> list[dict]
      Post-hoc: take a generate/execute step report (e.g.
      extras["generated"]["steps"] from create/generate_from_spec) and, for
      every entry with status != 'pass' AND a truthy 'error', ADD
      machine-actionable fields. The ORIGINAL 'error' value is NEVER touched
      (byte-identical — house rule: raw errors must never be masked by
      enrichment; append, don't replace). Added fields:
        likely_cause          unknown_op | args_invalid | selector_zero_match
                              | fm_refusal:<fm.token> | occt_failure
                              (occt_failure is the honest CATCH-ALL bucket for
                              anything the text heuristics can't classify)
        suggested_fix         one actionable sentence
        related_skills        registered skill names to run next
        selector_match_count  only when the entry carries an 'args' dict that
                              contains a selector AND body is given; None when
                              the resolver could not run (honest "don't know")
        selector_suggestions  only when selector_match_count == 0 — output of
                              the suggest_selector_from_phrase skill on a
                              phrase derived from the failing selector
      Enrichment NEVER raises: garbage entries pass through unchanged, and a
      non-list input returns [].

  preflight(spec, *, body=None) -> dict
      Pre-hoc, VALIDATION ONLY — no geometry is executed. Per step:
        {op, known, args_valid, arg_errors, selector_match_count, warnings}
      plus likely_cause (unknown_op / args_invalid) when applicable and
      selector_suggestions on a 0-match. Top-level {'ok': all known+valid,
      'n_steps', 'steps'}. A 0-match selector is a WARNING, not an invalidity:
      selectors are resolved against the body AS GIVEN, but earlier spec steps
      may legitimately create/mutate the body the selector will actually see.
      Fast by design: registry lookup + pydantic validation + (optional)
      selector resolution on the given body only.

All returned structures are strict-JSON-safe (json.dumps(..., allow_nan=False)
passes): added values are scrubbed of non-finite floats.

Honest limits:
  * likely_cause is a CLOSED five-bucket text classification; e.g. a
    post-condition zero-delta SKIP message lands in occt_failure.
  * selector detection looks at TOP-LEVEL args values only (a dict whose
    'kind' is a registered selector kind); the FIRST selector found is the
    one counted.
  * combo selectors (and/or/not/first_n/largest_n) have no declared
    face/edge domain — we try the face resolver then the edge resolver and
    report the first that resolves; if both raise, count is None.
  * edges_convex_only / edges_concave_only are not implemented in
    skills/_resolvers.resolve_edges — their count is honestly None.
"""
from __future__ import annotations

import math
import re
from typing import Any

__all__ = ["enrich_failures", "preflight"]


# ── classification ───────────────────────────────────────────────────────────

# fm.<token> structured-refusal tokens (house rule: ValueError with fm.*).
# Exactly one dot after 'fm', then word chars — a trailing sentence period is
# NOT swallowed.
_FM_RE = re.compile(r"\bfm\.[A-Za-z0-9_]+")

_UNKNOWN_PATTERNS = ("unknown skill", "not registered", "unknown op")
_ZERO_MATCH_PATTERNS = (
    "matched 0", "selector matched 0", "no faces matched", "no edges matched",
)
_ARGS_INVALID_PATTERNS = (
    "validation error", "pydantic", "field required", "input should be",
    "value_error", "type_error", "extra inputs are not permitted",
)

_FIX = {
    "unknown_op": (
        "The op name is not a registered skill — run find_skill_by_intent "
        "(or find_similar_skills) with the intended verb to discover the "
        "correct skill name, then re-issue the step.",
        ["find_skill_by_intent", "find_similar_skills"],
    ),
    "args_invalid": (
        "Args failed the skill's pydantic schema — run skill_schema_introspect "
        "on this skill and fix the offending field to satisfy the declared "
        "constraints.",
        ["skill_schema_introspect", "explain_skill_failure"],
    ),
    "selector_zero_match": (
        "The selector resolved to 0 faces/edges on the current body — use "
        "suggest_selector_from_phrase or selector_preview to find a selector "
        "that actually matches, then re-run the step.",
        ["suggest_selector_from_phrase", "selector_preview",
         "explain_skill_failure"],
    ),
    "occt_failure": (
        "The operation failed at execution time (OCCT/runtime) — run "
        "explain_skill_failure on the message, and consider shape_heal + "
        "dry_run before retrying.",
        ["explain_skill_failure", "shape_heal", "dry_run"],
    ),
}


def _classify(text: str) -> tuple[str, str, list[str]]:
    """error text -> (likely_cause, suggested_fix, related_skills)."""
    m = text.lower()
    if any(p in m for p in _UNKNOWN_PATTERNS):
        fix, rel = _FIX["unknown_op"]
        return "unknown_op", fix, list(rel)
    fm = _FM_RE.search(text)
    if fm:
        token = fm.group(0)
        return (
            f"fm_refusal:{token}",
            f"The skill refused with structured failure mode {token} — "
            "adjust the args to satisfy that constraint; run "
            "explain_skill_failure on the message for a step-by-step "
            "diagnosis.",
            ["explain_skill_failure", "dry_run"],
        )
    if any(p in m for p in _ZERO_MATCH_PATTERNS):
        fix, rel = _FIX["selector_zero_match"]
        return "selector_zero_match", fix, list(rel)
    if any(p in m for p in _ARGS_INVALID_PATTERNS):
        fix, rel = _FIX["args_invalid"]
        return "args_invalid", fix, list(rel)
    fix, rel = _FIX["occt_failure"]
    return "occt_failure", fix, list(rel)


# ── selector helpers (all best-effort; failures -> None, never raise) ────────

_EDGE_KINDS = frozenset({
    "axis_aligned_edges", "edges_on_face", "edges_by_length",
    "edges_by_position", "edges_convex_only", "edges_concave_only",
})
_FACE_KINDS = frozenset({"face_named", "faces_by_normal", "faces_by_area",
                         "tagged"})

# Literal fallback so selector DETECTION still works even if the import of
# _selectors fails (enrichment must never raise).
_FALLBACK_KINDS = frozenset(_EDGE_KINDS | _FACE_KINDS | {
    "and", "or", "not", "first_n", "largest_n",
})


def _selector_kinds() -> frozenset:
    try:
        from phone_designer.skills._selectors import _KIND_TO_CLASS
        return frozenset(_KIND_TO_CLASS)
    except Exception:  # noqa: BLE001
        return _FALLBACK_KINDS


def _find_selectors(args: dict) -> list[tuple[str, dict]]:
    """Top-level args values that look like selector dicts.

    A SketchSpec also carries 'kind' (circle/rectangle/...) — those kinds are
    not selector kinds, so sketches are correctly NOT picked up here.
    """
    kinds = _selector_kinds()
    out: list[tuple[str, dict]] = []
    for k, v in args.items():
        if isinstance(v, dict) and v.get("kind") in kinds:
            out.append((str(k), v))
    return out


def _selector_match_count(body: Any, sel_dict: dict) -> int | None:
    """Resolve a selector dict on `body`; None = could not resolve (honest)."""
    try:
        from phone_designer.skills._resolvers import resolve_edges, resolve_faces
        from phone_designer.skills._selectors import selector_from_dict

        sel = selector_from_dict(dict(sel_dict))
        shape = body.wrapped if hasattr(body, "wrapped") else body
        kind = sel.kind
        if kind in _EDGE_KINDS:
            return len(resolve_edges(shape, sel))
        if kind in _FACE_KINDS:
            return len(resolve_faces(shape, sel, body=body))
        # combo — domain unknown; first resolver that succeeds wins.
        for fn in (lambda: resolve_faces(shape, sel, body=body),
                   lambda: resolve_edges(shape, sel)):
            try:
                return len(fn())
            except Exception:  # noqa: BLE001
                continue
        return None
    except Exception:  # noqa: BLE001
        return None


def _phrase_from_selector(sel: dict) -> str:
    """Derive a natural-language phrase for suggest_selector_from_phrase.

    The suggester's Args are {phrase: str (min_length=1), top_k: int} — it
    consumes a PHRASE, not a selector, so we translate the failing selector
    back into the keyword vocabulary its heuristics understand.
    """
    kind = sel.get("kind")
    if kind == "face_named":
        return f"the {sel.get('name') or 'top'} face"
    if kind == "tagged":
        tag = str(sel.get("tag") or "").strip()
        return f"tagged {tag}" if tag else "tagged face"
    if kind == "axis_aligned_edges":
        axis = str(sel.get("axis") or "Z").upper()
        return {"Z": "vertical edges", "X": "x edges",
                "Y": "y edges"}.get(axis, "vertical edges")
    if kind == "faces_by_normal":
        d = sel.get("direction") or (0.0, 0.0, 1.0)
        try:
            dz = float(d[2])
        except Exception:  # noqa: BLE001
            dz = 1.0
        return "faces facing up" if dz >= 0 else "faces facing down"
    if kind == "faces_by_area":
        return "largest face" if sel.get("min") is not None else "small face"
    if kind == "edges_convex_only":
        return "convex edges"
    if kind == "edges_concave_only":
        return "concave edges"
    return str(kind or "face").replace("_", " ")


def _selector_suggestions(body: Any, sel_dict: dict) -> list | None:
    """Best-effort suggestions via the suggest_selector_from_phrase skill.

    Failures are swallowed silently (returns None). The skill echoes the body
    (post_condition body_present), so body must be non-None — callers only
    reach this path when body was given.
    """
    try:
        from phone_designer.skills.inspect.suggest_selector_from_phrase import (
            SuggestSelectorFromPhrase,
        )
        phrase = _phrase_from_selector(sel_dict)
        res = SuggestSelectorFromPhrase().apply(
            body, {"phrase": phrase, "top_k": 3})
        sugg = res.extras.get("selector_suggestions")
        return list(sugg) if sugg else None
    except Exception:  # noqa: BLE001
        return None


# ── strict-JSON scrubbing (added fields only — 'error' is never touched) ─────


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


# ── public API ───────────────────────────────────────────────────────────────


def enrich_failures(step_report: list[dict], *, body: Any = None) -> list[dict]:
    """Add machine-actionable fields to failed step-report entries.

    Returns a NEW list of shallow-copied dicts; input entries are not
    mutated. Non-dict entries pass through as-is; a non-list input returns
    []. Never raises.
    """
    if not isinstance(step_report, list):
        return []
    out: list[dict] = []
    for entry in step_report:
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        enriched = dict(entry)   # shallow copy — 'error' object untouched
        try:
            _enrich_one(enriched, body=body)
        except Exception:  # noqa: BLE001
            # enrichment must NEVER raise — the entry passes through with
            # whatever fields were added before the fault.
            pass
        out.append(enriched)
    return out


def _enrich_one(entry: dict, *, body: Any) -> None:
    status = entry.get("status")
    error = entry.get("error")
    if status == "pass" or not error:
        return
    text = error if isinstance(error, str) else str(error)
    likely, fix, related = _classify(text)
    entry["likely_cause"] = likely
    entry["suggested_fix"] = fix
    entry["related_skills"] = related

    args = entry.get("args")
    if body is None or not isinstance(args, dict):
        return
    selectors = _find_selectors(args)
    if not selectors:
        return
    _key, sel = selectors[0]
    count = _selector_match_count(body, sel)
    entry["selector_match_count"] = count
    if count == 0:
        sugg = _selector_suggestions(body, sel)
        if sugg is not None:
            entry["selector_suggestions"] = _json_safe(sugg)


def preflight(spec: list[dict], *, body: Any = None) -> dict:
    """Validate a spec WITHOUT executing any geometry.

    Raises ValueError (fm.args_invalid) only for a non-list `spec`;
    malformed individual steps are reported per-step, never raised.
    """
    if not isinstance(spec, list):
        raise ValueError(
            "fm.args_invalid: preflight spec must be a list of "
            "{op, args} steps, got " + type(spec).__name__)

    # Import the whole skill library ONCE so registry.get() sees every skill
    # (same idiom as create/generate_from_spec's _import_all_skills).
    from phone_designer.skills import export_manifest  # noqa: F401
    from phone_designer.skills._registry import registry

    steps_out: list[dict] = []
    ok = True
    for i, item in enumerate(spec):
        row: dict[str, Any] = {
            "op": None,
            "known": False,
            "args_valid": False,
            "arg_errors": [],
            "selector_match_count": None,
            "warnings": [],
        }
        steps_out.append(row)

        if not isinstance(item, dict):
            row["likely_cause"] = "args_invalid"
            row["warnings"].append(
                f"step {i}: not an object (got {type(item).__name__})")
            ok = False
            continue

        op = item.get("op") or item.get("skill")
        if not isinstance(op, str) or not op:
            row["likely_cause"] = "args_invalid"
            row["warnings"].append(f"step {i}: missing 'op'/'skill'")
            ok = False
            continue
        row["op"] = op

        try:
            sk = registry.get(op)
        except Exception:  # noqa: BLE001
            sk = None
        if sk is None:
            row["likely_cause"] = "unknown_op"
            row["warnings"].append(
                f"unknown skill '{op}' — not in the registry; use "
                "find_skill_by_intent / find_similar_skills")
            ok = False
            continue
        row["known"] = True

        sargs = item.get("args") if item.get("args") is not None else {}
        if not isinstance(sargs, dict):
            row["likely_cause"] = "args_invalid"
            row["arg_errors"] = [
                f"'args' must be an object, got {type(sargs).__name__}"]
            ok = False
            continue

        errors = _validate_args(sk, sargs)
        row["arg_errors"] = errors
        row["args_valid"] = not errors
        if errors:
            row["likely_cause"] = "args_invalid"
            ok = False

        # selector resolution — VALIDATION-ONLY advisory against the body AS
        # GIVEN (earlier steps may legitimately change what the selector will
        # see at execution time, so a 0-match is a warning, not a failure).
        if body is not None:
            selectors = _find_selectors(sargs)
            if selectors:
                key, sel = selectors[0]
                count = _selector_match_count(body, sel)
                row["selector_match_count"] = count
                if count == 0:
                    row["warnings"].append(
                        f"selector in '{key}' matches 0 entities on the "
                        "provided body (note: earlier steps may change the "
                        "body before this step runs)")
                    sugg = _selector_suggestions(body, sel)
                    if sugg is not None:
                        row["selector_suggestions"] = _json_safe(sugg)

    return {"ok": ok, "n_steps": len(steps_out), "steps": steps_out}


def _validate_args(sk_spec: Any, sargs: dict) -> list[str]:
    """Pydantic-validate `sargs` against the skill's Args; return error strings
    (empty list == valid). Never raises."""
    try:
        model = sk_spec.args_model
        model.model_validate(sargs)
        return []
    except Exception as exc:  # noqa: BLE001
        errs: list[str] = []
        errors_fn = getattr(exc, "errors", None)
        if callable(errors_fn):
            try:
                for e in errors_fn():
                    loc = ".".join(str(p) for p in e.get("loc", ()))
                    msg = str(e.get("msg", ""))
                    errs.append(f"{loc}: {msg}" if loc else msg)
            except Exception:  # noqa: BLE001
                pass
        if not errs:
            errs = [f"{type(exc).__name__}: {exc}"]
        return errs
