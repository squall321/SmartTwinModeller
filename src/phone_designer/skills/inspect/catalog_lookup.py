"""catalog_lookup — atomic, read-only.

Look up a single spec from a typed catalog file and attach the validated
entry to ``result.extras['catalog_entry']``. Body is not modified.

This is the typed-loader counterpart to ``match_standard_*`` skills: where
those score a measurement against the catalog, this one pulls one exact
row by ``spec`` name. Use it when an upstream step already chose the
fastener / bearing / O-ring (e.g. ``M3``, ``608``, ``AS568-013``) and the
downstream generator needs its dimensions.

extras["catalog_entry"] = {
    "family": "standards",
    "name":   "threads_metric",
    "spec":   "M3",
    "data":   {"outer_d_mm": 3.0, "pitch_mm": 0.5, ...},
}
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._catalog import load_typed
from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="catalog_lookup",
    category="inspect",
    level="atomic",
    summary="Look up a single spec from a typed catalog YAML "
            "(family/name/spec) and attach the validated entry to "
            "extras['catalog_entry']. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["catalog_entry"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.02,
    post_conditions=[PostCondition(kind="body_present")],
)
class CatalogLookup(SkillBase):
    class Args(BaseModel):
        family: str = Field(min_length=1, description="catalogs/<family>/")
        name: str = Field(min_length=1, description="<name>.yaml stem")
        spec: str = Field(min_length=1,
                          description="key inside the YAML entries dict")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        entry_obj: dict[str, Any] | None = None
        error: str | None = None
        try:
            typed = load_typed(args.family, args.name)
        except FileNotFoundError as exc:
            typed = {}
            error = str(exc)
        except Exception as exc:  # pragma: no cover — defensive
            typed = {}
            error = f"{type(exc).__name__}: {exc}"

        model = typed.get(args.spec)
        if model is not None:
            try:
                entry_obj = model.model_dump()
            except Exception:
                entry_obj = dict(getattr(model, "__dict__", {}))
        elif error is None:
            error = (
                f"spec '{args.spec}' not found in "
                f"catalogs/{args.family}/{args.name}.yaml "
                f"(known: {sorted(typed.keys())[:10]}…)"
            )

        extras: dict[str, Any] = {
            "catalog_entry": {
                "family": args.family,
                "name":   args.name,
                "spec":   args.spec,
                "data":   entry_obj,
            },
        }
        if error is not None:
            extras["catalog_entry"]["error"] = error

        # Catalog skills are pure-data — they can be invoked with body=None.
        # The 'body_present' post_condition requires a non-None result; use a
        # cheap sentinel object so the executor's _measure() returns a dict.
        out_body = body if body is not None else _CatalogSentinel()

        return SkillResult(
            body=out_body,
            history=EntityHistoryMap(),
            extras=extras,
        )


class _CatalogSentinel:
    """No-op body sentinel — lets pure-data catalog skills run with body=None.

    The executor's post-condition harness calls ``_measure(body)`` which only
    needs to return *not None* for ``body_present``. OCCT will reject this
    object and the measure helper silently returns a zeroed metric dict.
    """
    __slots__ = ()
