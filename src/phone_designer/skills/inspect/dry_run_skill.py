"""dry_run — atomic, read-only.

Plan / what-if helper for LLM workflows. Given a `target_skill_name` and
`target_args`, predict (without executing) what the target skill would do:

  (a) Resolve every selector found in `target_args` against the current body
      and report how many entities each one matched. This catches the
      "selector matched 0" failure mode before it surfaces during real
      execution.

  (b) Load the target skill's @skill metadata (post_conditions,
      history_rules, etc.) so the LLM can read what the skill promises.

  (c) Predict whether the body would change:
        volume_increased / volume_decreased / volume_unchanged / unknown
      derived from the target's declared `post_conditions`. Likewise
      which roles (target_face, pocket_walls, ...) the skill marks as
      generated/consumed/inherited via `history_rules`.

The body is **not** mutated — the original `body` is echoed back. All
predictions land in `extras["dry_run"]`. Errors (unknown skill, selector
parse failure, resolver NotImplementedError) become diagnostic strings
in extras["dry_run"]["error"] instead of raised exceptions so the LLM can
inspect the failure and adjust its plan.

result.extras["dry_run"] schema:
    {
      "target_skill_name": str,
      "matched_selectors": [
        {"arg_path": "face_selector",
         "selector": {...},
         "kind": "faces"|"edges",
         "matched_count": int,
         "error": str | None},
        ...
      ],
      "predicted_volume_change": "increased"|"decreased"|"unchanged"|"unknown",
      "predicted_face_change": "changed"|"unchanged"|"unknown",
      "preconditions_passed": [],          # reserved — populated when we
                                            # wire individual pc.* checks
      "post_conditions": [{"kind": ..., "min_delta_mm3": ...}, ...],
      "history_rules": {role: rule_value, ...},
      "selector_kinds": [...],             # echoed from target spec
      "error": str | None,                 # spec lookup failure only
    }

post_condition: body_present — input body echoed back, never None for the
LLM workflow expectations.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import registry, skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


def _looks_like_selector(v: Any) -> bool:
    """Heuristic: a dict with a string "kind" key matching a known selector."""
    if not isinstance(v, dict):
        return False
    kind = v.get("kind")
    if not isinstance(kind, str):
        return False
    # Known selector kinds. Use the registry from _selectors module's
    # _KIND_TO_CLASS via a defensive import — falls back to a fixed
    # whitelist if the import fails.
    try:
        from phone_designer.skills._selectors import _KIND_TO_CLASS
        return kind in _KIND_TO_CLASS
    except Exception:
        return kind in {
            "tagged", "face_named", "axis_aligned_edges", "edges_on_face",
            "edges_by_length", "edges_by_position", "faces_by_normal",
            "faces_by_area", "edges_convex_only", "edges_concave_only",
            "and", "or", "not", "first_n", "largest_n",
        }


def _find_selectors(args: Any, prefix: str = "") -> list[tuple[str, dict]]:
    """Walk a nested dict/list and yield (arg_path, selector_dict) tuples
    for every dict that looks like a selector."""
    out: list[tuple[str, dict]] = []
    if _looks_like_selector(args):
        out.append((prefix or "<root>", args))
        # Don't recurse into selector internals — the resolver handles
        # nested selector_refs itself.
        return out
    if isinstance(args, dict):
        for k, v in args.items():
            sub_prefix = f"{prefix}.{k}" if prefix else str(k)
            out.extend(_find_selectors(v, sub_prefix))
    elif isinstance(args, list):
        for i, v in enumerate(args):
            sub_prefix = f"{prefix}[{i}]"
            out.extend(_find_selectors(v, sub_prefix))
    return out


def _infer_selector_kind(name: str, selector_dict: dict) -> str:
    """Decide faces vs edges from arg name + selector kind."""
    sk = selector_dict.get("kind", "")
    # Edge-only selector kinds
    if sk in {
        "axis_aligned_edges", "edges_on_face", "edges_by_length",
        "edges_by_position", "edges_convex_only", "edges_concave_only",
    }:
        return "edges"
    # Face-only selector kinds
    if sk in {"faces_by_normal", "faces_by_area", "face_named"}:
        return "faces"
    # Ambiguous (tagged, and/or/not/first_n/largest_n) → fall back to name
    lname = name.lower()
    if "edge" in lname:
        return "edges"
    if "face" in lname:
        return "faces"
    # Default to faces — modify_pocket et al. use face_selector dominantly.
    return "faces"


def _predict_from_post_conditions(
    post_conditions: list[PostCondition] | None,
) -> tuple[str, str]:
    """Map declared post_conditions to (volume_change, face_change)
    predictions."""
    vol = "unknown"
    face = "unknown"
    if not post_conditions:
        return vol, face
    for pc in post_conditions:
        kind = getattr(pc, "kind", None)
        if kind == "volume_decreased":
            vol = "decreased"
        elif kind == "volume_increased":
            vol = "increased"
        elif kind == "volume_changed":
            if vol == "unknown":
                vol = "changed"
        elif kind == "face_count_changed":
            face = "changed"
        elif kind == "body_present":
            # Read-only / inspection skill → body unchanged.
            if vol == "unknown":
                vol = "unchanged"
            if face == "unknown":
                face = "unchanged"
    return vol, face


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


@skill(
    name="dry_run",
    category="inspect",
    level="atomic",
    summary="Predict the effect of a target skill on the current body without "
            "executing it. Resolves every selector in target_args, loads the "
            "target's @skill metadata, and reports predicted volume/face change "
            "based on the target's declared post_conditions and history_rules.",
    selector_kinds=[],
    history_rules={},
    preconditions=[],
    produces_features=["dry_run_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.02,
    post_conditions=[PostCondition(kind="body_present")],
)
class DryRun(SkillBase):
    class Args(BaseModel):
        target_skill_name: str
        target_args: dict

    def _apply(self, body: Any, args: Args) -> SkillResult:
        report: dict[str, Any] = {
            "target_skill_name": args.target_skill_name,
            "matched_selectors": [],
            "predicted_volume_change": "unknown",
            "predicted_face_change": "unknown",
            "preconditions_passed": [],
            "post_conditions": [],
            "history_rules": {},
            "selector_kinds": [],
            "error": None,
        }

        # (b) load target spec
        try:
            spec = registry.get(args.target_skill_name)
        except KeyError as ex:
            report["error"] = f"unknown skill: {ex}"
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={"dry_run": report},
            )

        # echo metadata
        report["selector_kinds"] = list(getattr(spec, "selector_kinds", []) or [])
        report["history_rules"] = {
            role: (rule.value if hasattr(rule, "value") else str(rule))
            for role, rule in (getattr(spec, "history_rules", {}) or {}).items()
        }
        report["post_conditions"] = [
            {
                "kind": getattr(pc, "kind", "unknown"),
                "min_delta_mm3": getattr(pc, "min_delta_mm3", None),
                "allow_no_change": getattr(pc, "allow_no_change", False),
            }
            for pc in (getattr(spec, "post_conditions", None) or [])
        ]

        # (c) predict body change
        vol, face = _predict_from_post_conditions(
            getattr(spec, "post_conditions", None)
        )
        report["predicted_volume_change"] = vol
        report["predicted_face_change"] = face

        # (a) resolve every selector in target_args
        from phone_designer.skills._resolvers import (
            resolve_edges, resolve_faces,
        )

        shape = _occt_shape(body) if body is not None else None
        for arg_path, sel_dict in _find_selectors(args.target_args):
            entry: dict[str, Any] = {
                "arg_path": arg_path,
                "selector": sel_dict,
                "kind": _infer_selector_kind(arg_path, sel_dict),
                "matched_count": 0,
                "error": None,
            }

            if shape is None:
                entry["error"] = "no body to resolve against"
                report["matched_selectors"].append(entry)
                continue

            try:
                sel: SelectorBase = selector_from_dict(sel_dict)
            except Exception as ex:
                entry["error"] = (
                    f"selector parse failed: {type(ex).__name__}: {ex}"
                )
                report["matched_selectors"].append(entry)
                continue

            try:
                if entry["kind"] == "edges":
                    entities = resolve_edges(shape, sel)
                else:
                    entities = resolve_faces(shape, sel, body=body)
                entry["matched_count"] = len(entities)
            except NotImplementedError as ex:
                entry["error"] = f"resolver not implemented: {ex}"
            except Exception as ex:
                entry["error"] = f"resolve failed: {type(ex).__name__}: {ex}"

            report["matched_selectors"].append(entry)

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"dry_run": report},
        )
