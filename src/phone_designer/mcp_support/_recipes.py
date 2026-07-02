"""_recipes — recipes corpus loader + find_recipe few-shot search (track 2-5).

The ``recipes/`` directory at the repo root holds 50+ committed YAML recipes —
small, EXECUTED-and-PINNED few-shot examples of generate_from_spec specs for the
idioms that bite a cold LLM (composite arc profiles, the sketch_revolve Z-lock,
the sweep G1 tangent law + its structured refusals as NEGATIVE recipes,
face-selector pocket/boss/fillet idioms, boolean/transform verbs, …).

Schema of one recipe file (see also recipes/README.md)::

    name: sketch_sweep_bent_pipe          # == file stem
    intent_en: ...                        # natural-language intent (English)
    intent_kr: ...                        # natural-language intent (Korean)
    tags: [sweep, pipe, bent, ...]
    spec: [{op: <skill>, args: {...}}, ...]   # runs via generate_from_spec
    expected:                             # measured by executing the recipe
      is_solid: true
      volume_mm3: [min, max]              # actual volume ±2%
      bbox_mm: [[dx,dy,dz]min, [dx,dy,dz]max]   # optional, extents ±1%+0.05
      notes: ...
    # NEGATIVE recipes instead pin the structured refusal:
    # expected: {ok: false, failing_op: <skill>, error_contains: "fm...."}

``tests/test_recipes_execute.py`` re-executes EVERY recipe through
GenerateFromSpec and asserts the expected invariants — the anti-rot pin. Keep
that green when adding recipes (author the spec, run it, record the ACTUAL
volume as the range).

``find_recipe(query, top_k)`` reuses the tokenizer + unigram/bigram scorer from
``find_skill_by_intent`` (the standing v1 heuristic — the embedding upgrade was
explicitly REJECTED in the roadmap) over name + intent_en + intent_kr + tags.

MCP wiring (mcp_server.py, maintainer-owned) exposes this as ``cad_find_recipe``
and optionally as a FastMCP resource ``recipe://{name}``.
"""
from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any

__all__ = [
    "RECIPES_DIR",
    "load_recipes",
    "recipe_by_name",
    "find_recipe",
    "clear_recipe_cache",
]


def _repo_root() -> Path:
    """repo root — 3 levels up (mcp_support → phone_designer → src → repo)."""
    return Path(__file__).resolve().parents[3]


RECIPES_DIR = _repo_root() / "recipes"

_REQUIRED_KEYS = ("name", "intent_en", "intent_kr", "tags", "spec", "expected")

_cache_lock = threading.Lock()
_cache: dict[str, list[dict[str, Any]]] = {}


def _validate_recipe(doc: Any, path: Path) -> dict[str, Any]:
    """Schema-check one loaded YAML doc. Raises ValueError naming the FILE —
    a malformed committed recipe must fail loudly, never be skipped silently."""
    if not isinstance(doc, dict):
        raise ValueError(f"recipe {path}: top level must be a mapping, "
                         f"got {type(doc).__name__}")
    missing = [k for k in _REQUIRED_KEYS if k not in doc]
    if missing:
        raise ValueError(f"recipe {path}: missing required keys {missing}")
    if doc["name"] != path.stem:
        raise ValueError(f"recipe {path}: name '{doc['name']}' != file stem "
                         f"'{path.stem}'")
    tags = doc["tags"]
    if (not isinstance(tags, list) or not tags
            or not all(isinstance(t, str) and t for t in tags)):
        raise ValueError(f"recipe {path}: tags must be a non-empty list of "
                         f"non-empty strings")
    spec = doc["spec"]
    if not isinstance(spec, list) or not spec:
        raise ValueError(f"recipe {path}: spec must be a non-empty list")
    for i, step in enumerate(spec):
        if not isinstance(step, dict) or not step.get("op") \
                or not isinstance(step.get("args", {}), dict):
            raise ValueError(f"recipe {path}: spec step {i} must be "
                             f"{{op: <skill>, args: {{...}}}}")
    exp = doc["expected"]
    if not isinstance(exp, dict):
        raise ValueError(f"recipe {path}: expected must be a mapping")
    negative = "error_contains" in exp
    if negative:
        if exp.get("ok") is not False or not exp.get("failing_op"):
            raise ValueError(f"recipe {path}: a NEGATIVE recipe needs "
                             f"expected.ok=false + expected.failing_op")
    else:
        if not isinstance(exp.get("is_solid"), bool):
            raise ValueError(f"recipe {path}: expected.is_solid (bool) is "
                             f"required for a positive recipe")
        vol = exp.get("volume_mm3")
        if (not isinstance(vol, list) or len(vol) != 2
                or not all(isinstance(v, (int, float)) and math.isfinite(v)
                           for v in vol)
                or vol[0] > vol[1] or vol[0] < 0):
            raise ValueError(f"recipe {path}: expected.volume_mm3 must be a "
                             f"finite [min, max] range with min <= max")
        bbox = exp.get("bbox_mm")
        if bbox is not None:
            ok_shape = (isinstance(bbox, list) and len(bbox) == 2
                        and all(isinstance(b, list) and len(b) == 3
                                for b in bbox))
            if not ok_shape:
                raise ValueError(f"recipe {path}: expected.bbox_mm must be "
                                 f"[[dx,dy,dz]min, [dx,dy,dz]max]")
    return doc


def load_recipes(recipes_dir: Path | str | None = None,
                 force_reload: bool = False) -> list[dict[str, Any]]:
    """Load (and cache) every ``*.yaml`` recipe, sorted by name.

    The cache is per-directory and thread-safe; ``force_reload=True`` re-reads
    from disk (tests / recipe authoring). A malformed file raises ValueError
    with its path — errors are never masked into an empty corpus.
    """
    import yaml

    directory = Path(recipes_dir) if recipes_dir is not None else RECIPES_DIR
    key = str(directory.resolve())
    with _cache_lock:
        if not force_reload and key in _cache:
            return _cache[key]
        if not directory.is_dir():
            raise FileNotFoundError(f"recipes directory not found: {directory}")
        recipes: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.yaml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            recipes.append(_validate_recipe(doc, path))
        _cache[key] = recipes
        return recipes


def clear_recipe_cache() -> None:
    with _cache_lock:
        _cache.clear()


def recipe_by_name(name: str,
                   recipes_dir: Path | str | None = None) -> dict[str, Any]:
    """Exact lookup (the ``recipe://{name}`` resource path). KeyError if absent."""
    for r in load_recipes(recipes_dir):
        if r["name"] == name:
            return r
    raise KeyError(f"no recipe named '{name}' "
                   f"(have {len(load_recipes(recipes_dir))} recipes)")


def find_recipe(query: str, top_k: int = 5,
                recipes_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """Rank recipes against a natural-language query (EN or KR).

    Reuses the EXACT tokenizer + unigram/bigram scorer of find_skill_by_intent
    over ``name + intent_en + intent_kr + tags``. Returns up to ``top_k``
    entries::

        [{name, intent, intent_en, intent_kr, tags, score,
          unigram_overlap, bigram_overlap, spec, expected}, ...]

    ``spec`` is the ready-to-run generate_from_spec step list (the few-shot
    payload) and ``expected`` carries the pinned invariants (including the
    fm.* refusal token for NEGATIVE recipes). Zero-overlap recipes are never
    returned — an unmatched query yields fewer (possibly zero) results, not
    noise. Output is strict-JSON-safe (finite floats only).
    """
    from phone_designer.skills.inspect.find_skill_by_intent import (
        _bigrams,
        _score,
        _tokenize,
    )

    if not isinstance(query, str) or not query.strip():
        raise ValueError("find_recipe: query must be a non-empty string")
    top_k = max(1, min(int(top_k), 50))

    q_tokens_list = _tokenize(query)
    q_tokens = set(q_tokens_list)
    q_bigrams = _bigrams(q_tokens_list)

    # The reused tokenizer is ASCII-oriented and DROPS Hangul, so a Korean
    # query ('기어', '구부러진 관') would score 0 against every recipe even
    # though intent_kr matches. Fallback: score Hangul chunks by substring
    # containment in intent_kr + tags (agglutination-tolerant via a 2-char
    # prefix retry: query '기어를' still hits intent '기어').
    import re as _re
    kr_chunks = _re.findall(r"[가-힣]{2,}", query)

    scored: list[tuple[float, int, int, dict[str, Any]]] = []
    for rec in load_recipes(recipes_dir):
        haystack = " ".join([
            rec["name"].replace("_", " "),
            rec["intent_en"],
            rec["intent_kr"],
            " ".join(rec["tags"]),
        ])
        s_tokens_list = _tokenize(haystack)
        score, uni, bi = _score(q_tokens, q_bigrams,
                                set(s_tokens_list), _bigrams(s_tokens_list))
        if kr_chunks:
            kr_hay = rec["intent_kr"] + " " + " ".join(rec["tags"])
            kr_hits = sum(
                1 for c in kr_chunks
                if c in kr_hay or (len(c) > 2 and c[:2] in kr_hay))
            if kr_hits:
                score += float(kr_hits)
                uni += kr_hits
        if score > 0.0:
            scored.append((score, uni, bi, rec))

    scored.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3]["name"]))

    out: list[dict[str, Any]] = []
    for score, uni, bi, rec in scored[:top_k]:
        out.append({
            "name": rec["name"],
            "intent": rec["intent_en"],
            "intent_en": rec["intent_en"],
            "intent_kr": rec["intent_kr"],
            "tags": list(rec["tags"]),
            "score": round(float(score), 4),
            "unigram_overlap": uni,
            "bigram_overlap": bi,
            "spec": rec["spec"],
            "expected": rec["expected"],
        })
    return out
