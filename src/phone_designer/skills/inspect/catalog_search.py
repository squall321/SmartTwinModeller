"""catalog_search — atomic, read-only.

Search a typed catalog YAML for specs whose names contain a substring (case
insensitive) and rank them by closeness. Useful when an LLM agent has a
fuzzy designation ("M3 ish", "608 bearing", "USB_C") and wants to discover
the exact spec name to feed into ``catalog_lookup`` / a generator.

Ranking score:
    score = len(query) / len(spec)   (1.0 = exact match length, lower = more
    extra characters around the match). Substring match is required.

extras["matches"] = [
    {"spec": "USB_C_Receptacle_v2", "score": 0.21}, ...
]
extras["query"] = {"family": ..., "name_glob_hint": "...", "query": "..."}
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._catalog import list_specs, load_typed
from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _search_in_catalog(family: str, name: str, query: str) -> list[dict[str, Any]]:
    try:
        typed = load_typed(family, name)
    except FileNotFoundError:
        return []
    except Exception:
        return []
    q = query.lower()
    out: list[dict[str, Any]] = []
    for spec in typed:
        if q in spec.lower():
            score = round(len(query) / max(len(spec), 1), 4)
            out.append({
                "spec":    spec,
                "catalog": f"{family}/{name}",
                "score":   score,
            })
    return out


@skill(
    name="catalog_search",
    category="inspect",
    level="atomic",
    summary="Find specs in a catalog family whose names contain the query "
            "substring (case insensitive). Returns ranked matches across "
            "every YAML in the family. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["catalog_search_result"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.03,
    post_conditions=[PostCondition(kind="body_present")],
)
class CatalogSearch(SkillBase):
    class Args(BaseModel):
        family: str = Field(min_length=1,
                            description="catalogs/<family>/ — e.g. 'standards'")
        query: str = Field(min_length=1,
                           description="substring to match against spec names")
        max_results: int = Field(default=10, ge=1, le=200)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        matches: list[dict[str, Any]] = []
        for stem in list_specs(args.family):
            matches.extend(_search_in_catalog(args.family, stem, args.query))

        # Highest score first, then alphabetical for stability.
        matches.sort(key=lambda m: (-m["score"], m["spec"]))
        matches = matches[: args.max_results]

        # Pure-data skill — allow body=None invocations (see catalog_lookup).
        out_body = body if body is not None else _CatalogSentinel()

        return SkillResult(
            body=out_body,
            history=EntityHistoryMap(),
            extras={
                "matches": matches,
                "query": {
                    "family": args.family,
                    "query":  args.query,
                },
            },
        )


class _CatalogSentinel:
    """No-op body sentinel — lets pure-data catalog skills run with body=None."""
    __slots__ = ()
