"""_pillar_tools — thin JSON-safe wrappers over the finished-but-invisible
pillars (Phase-1, track 1-4).

Three call-and-return functions the MCP wiring layer (mcp_server.py) exposes
as tools. Each one:

  * loads its OWN body/bodies from STEP path(s) via ImportStep (path in,
    dict out — no session state, no body cache coupling);
  * chains the EXISTING macros exactly the way their own tests do
    (compare_parts; identify_key_dimensions -> generate_variant_family;
    identify_key_dimensions -> cost_min_variant_search) — nothing reinvented;
  * returns a STRICT-JSON-safe dict (``json.dumps(..., allow_nan=False)`` is
    asserted before returning — inf/nan are converted to None, never emitted);
  * optionally writes a report JSON artifact under ``out_dir`` and returns its
    path;
  * RAISES on failure (FileNotFoundError / ValueError with fm.* tokens /
    the macro's own errors) — the wiring layer wraps in its ``_err`` envelope.
    Errors are never masked here.

The cost_min_variant_search STRICT viability gate (standing project ruling:
never crown a marginal candidate) is asserted to PASS THROUGH:
``_assert_strict_viability_gate`` re-checks the winner against the variant
table and raises loudly if a non-'viable'-tier winner ever appears.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

__all__ = [
    "compare_parts_tool",
    "variants_tool",
    "cheapest_variant_tool",
]

_MAX_VARIANTS = 16  # mirrors cost_min_variant_search's default hard cap


# ──────────────────────────────────────────────────────────────────────────────
# helpers


def _ensure_skills() -> None:
    """Register every skill module (detectors resolve through the registry)."""
    from phone_designer.plan.executor import _import_all_skills
    _import_all_skills()


def _json_safe(obj: Any) -> Any:
    """Recursively convert ``obj`` into a strict-JSON-safe structure.

    Floats that are inf/nan become None (house rule: never emit non-finite
    numbers); numpy scalars unwrap via ``.item()``; tuples/sets become lists;
    Paths become strings; anything else unknown falls back to ``str()``.
    """
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        return float(obj) if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    item = getattr(obj, "item", None)  # numpy scalar
    if callable(item):
        try:
            return _json_safe(item())
        except Exception:
            pass
    return str(obj)


def _assert_json_safe(out: dict) -> dict:
    """Guarantee the contract: the returned dict serialises under strict JSON."""
    json.dumps(out, allow_nan=False)  # raises on inf/nan/unserialisable
    return out


def _load_body(part_path: str):
    """STEP path -> body via the existing ImportStep skill (raises on a
    missing file / unreadable STEP — errors surface raw)."""
    from phone_designer.skills.create.import_step import ImportStep
    return ImportStep().apply(None, {"path": str(part_path)}).body


def _catalog_of(body) -> dict:
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    return ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]


def _write_report(out_dir: str | None, name: str, payload: dict) -> str | None:
    """Write ``payload`` as pretty JSON under ``out_dir`` (created if needed).
    Returns the file path, or None when out_dir is None."""
    if out_dir is None:
        return None
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(
        json.dumps(payload, allow_nan=False, indent=2), encoding="utf-8")
    return str(p)


def _top_driver(key_dims: list[dict], part_path: str) -> tuple[str, float]:
    """The rank-0 key dimension (identify_key_dimensions already sorts
    envelope > bore > pitch > wall > pocket) -> (driver_name, base_value)."""
    if not key_dims:
        raise ValueError(
            "fm.no_key_dimensions: identify_key_dimensions found no driving "
            f"dimensions on '{part_path}' — the feature catalog is empty or "
            "carries no resolvable envelope/bore/pitch/wall/pocket handles, so "
            "no variant driver can be chosen honestly.")
    top = key_dims[0]
    base = top.get("value_mm")
    if not isinstance(base, (int, float)) or base <= 0.0:
        raise ValueError(
            "fm.no_key_dimensions: the top-ranked key dimension "
            f"'{top.get('name')}' has a non-positive value ({base!r}) — "
            "cannot sweep it.")
    return str(top["name"]), float(base)


def _assert_strict_viability_gate(search: dict) -> None:
    """Re-assert cost_min_variant_search's central ruling on the OUTPUT:
    a winner, when present, must sit in the strict 'viable' tier and its
    variant record must agree. Raises RuntimeError on any violation —
    a marginal/unproven candidate is NEVER crowned, not even by a wrapper bug.
    """
    winner = search.get("winner")
    if winner is None:
        return
    if winner.get("viability_tier") != "viable":
        raise RuntimeError(
            "fm.viability_gate_violation: cost_min_variant_search returned a "
            f"winner with viability_tier={winner.get('viability_tier')!r} — "
            "the strict 'viable' gate did not hold; refusing to report it.")
    match = [v for v in (search.get("variants") or [])
             if v.get("value") == winner.get("value")]
    for v in match:
        if not (v.get("viable") is True and v.get("viability_tier") == "viable"):
            raise RuntimeError(
                "fm.viability_gate_violation: winner value "
                f"{winner.get('value')} maps to a variant record with "
                f"viable={v.get('viable')!r} / "
                f"viability_tier={v.get('viability_tier')!r} — refusing.")


# ──────────────────────────────────────────────────────────────────────────────
# 1) COMPARE — wrap reverse_engineer/compare_parts


def compare_parts_tool(part_a_path: str, part_b_path: str, out_dir: str) -> dict:
    """Compare two STEP parts end-to-end (registration, per-feature changes,
    geometric deviation, mass delta, PMI diff, similarity + classification).

    Thin wrapper over reverse_engineer/compare_parts: the macro loads its OWN
    bodies from the two paths (body=None is fine — it returns the loaded A).
    Writes ``out_dir/compare_report.json`` and returns the JSON-safe summary
    plus the artifact path. Raises FileNotFoundError when either path is
    missing (the macro's own refusal, unmasked).
    """
    _ensure_skills()
    from phone_designer.skills.reverse_engineer.compare_parts import CompareParts

    res = CompareParts().apply(None, {
        "part_a_path": str(part_a_path),
        "part_b_path": str(part_b_path),
    })
    comparison = _json_safe(res.extras["part_comparison"])

    gd = comparison.get("geometry_deviation") or {}
    md = comparison.get("mass_delta") or {}
    out: dict[str, Any] = {
        "ok": True,
        "tool": "compare_parts",
        "part_a_path": str(part_a_path),
        "part_b_path": str(part_b_path),
        "summary": {
            "classification": comparison.get("classification"),
            "similarity_score": comparison.get("similarity_score"),
            "scale_factor": comparison.get("scale_factor"),
            "hausdorff_mm": gd.get("hausdorff_mm") if isinstance(gd, dict) else None,
            "volume_delta_pct": md.get("volume_delta_pct") if isinstance(md, dict) else None,
            "alignment_used": gd.get("alignment_used") if isinstance(gd, dict) else None,
        },
        "part_comparison": comparison,
        "grade": "estimate",  # compare_parts is result_grade='estimate'
        "artifacts": {},
    }
    report = _write_report(out_dir, "compare_report.json", out["part_comparison"])
    if report is not None:
        out["artifacts"]["report_json"] = report
    return _assert_json_safe(out)


# ──────────────────────────────────────────────────────────────────────────────
# 2) VARIANTS — identify_key_dimensions -> generate_variant_family


def variants_tool(part_path: str, n_variants: int = 4,
                  out_dir: str | None = None) -> dict:
    """Generate a validated parametric variant family from ONE STEP part.

    Chain (exactly as the pillar's own tests do): import_step ->
    extract_feature_catalog -> identify_key_dimensions (pick the rank-0
    driver, typically the envelope length) -> generate_variant_family over
    ``n_variants`` values from 1.0x to 2.0x of the base value. Returns the
    key-dimension menu + the per-variant list (value / valid / bbox / volume /
    fidelity-vs-baseline / plan_path). Fidelity is RELATIVE to the 1.0x
    rebuild (the base recon is imperfect — the macro's honest caveat).
    """
    if not isinstance(n_variants, int) or n_variants < 2:
        raise ValueError(
            f"fm.invalid_args: n_variants must be an int >= 2 (got {n_variants!r}) "
            "— a family of one is not a sweep.")
    if n_variants > _MAX_VARIANTS:
        raise ValueError(
            f"fm.invalid_args: n_variants={n_variants} exceeds the hard cap "
            f"{_MAX_VARIANTS} (each variant is a full rebuild — never silently "
            "truncated).")

    _ensure_skills()
    from phone_designer.skills.reverse_engineer.generate_variant_family import (
        GenerateVariantFamily,
    )
    from phone_designer.skills.reverse_engineer.identify_key_dimensions import (
        identify_key_dimensions,
    )

    body = _load_body(part_path)
    catalog = _catalog_of(body)
    key_dims = identify_key_dimensions(catalog)
    driver, base = _top_driver(key_dims, part_path)

    # 1.0x .. 2.0x inclusive — values[0] IS the fidelity baseline.
    values = [base * (1.0 + i / (n_variants - 1)) for i in range(n_variants)]

    family = GenerateVariantFamily().apply(body, {
        "catalog": catalog,
        "driver": driver,
        "values": values,
    }).extras["variant_family"]
    family = _json_safe(family)

    out: dict[str, Any] = {
        "ok": True,
        "tool": "variants",
        "part_path": str(part_path),
        "key_dimensions": _json_safe(key_dims),
        "driver": family.get("driver"),
        "driver_role": family.get("driver_role"),
        "baseline_value": family.get("baseline_value"),
        "values": [round(v, 6) for v in values],
        "fidelity_metric": family.get("fidelity_metric"),
        "fidelity_note": (
            "fidelity_vs_baseline is measured against the values[0] REBUILD, "
            "not the original STEP — read it relative, never absolute."),
        "variants": family.get("variants") or [],
        "summary": family.get("summary") or {},
        "artifacts": {},
    }
    report = _write_report(out_dir, "variants_report.json",
                           {k: v for k, v in out.items() if k != "artifacts"})
    if report is not None:
        out["artifacts"]["report_json"] = report
    return _assert_json_safe(out)


# ──────────────────────────────────────────────────────────────────────────────
# 3) CHEAPEST VARIANT — identify_key_dimensions -> cost_min_variant_search


def cheapest_variant_tool(part_path: str, process: str,
                          material: str = "aluminum", lot_size: int = 1000,
                          out_dir: str | None = None,
                          max_iterations: int | None = None) -> dict:
    """Find the cheapest GENUINELY-viable variant of a STEP part.

    Chain: import_step -> extract_feature_catalog -> identify_key_dimensions
    (rank-0 driver) -> cost_min_variant_search sweeping DOWN from 1.0x to
    0.5x of the base value over ``max_iterations`` (default 4) values, priced
    at ``lot_size`` in ``material`` restricted to ``process``.

    The macro's STRICT viability gate passes through untouched: winner is
    None (with a distinct overall_flag) unless a variant reaches the strict
    'viable' tier — marginal/unproven variants are recorded but cannot win.
    ``_assert_strict_viability_gate`` re-checks that invariant on the output
    and raises if it were ever violated. grade='estimate' end to end.
    """
    n = 4 if max_iterations is None else max_iterations
    if not isinstance(n, int) or n < 2 or n > _MAX_VARIANTS:
        raise ValueError(
            f"fm.invalid_args: max_iterations must be an int in "
            f"[2, {_MAX_VARIANTS}] (got {max_iterations!r}).")

    # Fail a process typo FAST — before the (expensive) body load. The macro's
    # own Args validator re-checks this; _ALL_KEYS is its single source.
    from phone_designer.skills.reverse_engineer.cost_min_variant_search import (
        _ALL_KEYS,
    )
    if process not in _ALL_KEYS:
        raise ValueError(
            f"fm.invalid_args: unknown process key '{process}'; known "
            f"costable keys: {_ALL_KEYS}")

    _ensure_skills()
    from phone_designer.skills.reverse_engineer.cost_min_variant_search import (
        CostMinVariantSearch,
    )
    from phone_designer.skills.reverse_engineer.identify_key_dimensions import (
        identify_key_dimensions,
    )

    body = _load_body(part_path)
    catalog = _catalog_of(body)
    key_dims = identify_key_dimensions(catalog)
    driver, base = _top_driver(key_dims, part_path)

    # Sweep DOWN 1.0x -> 0.5x: values[0] (the original size) anchors both the
    # fidelity baseline and the savings_vs_baseline comparison.
    values = [base * (1.0 - 0.5 * i / (n - 1)) for i in range(n)]

    search = CostMinVariantSearch().apply(body, {
        "catalog": catalog,
        "driver": driver,
        "values": values,
        "lot_size": int(lot_size),
        "material": str(material),
        "processes": [str(process)],   # unknown keys raise in the macro's Args
    }).extras["cost_variant_search"]
    search = _json_safe(search)

    _assert_strict_viability_gate(search)

    out: dict[str, Any] = {
        "ok": True,
        "tool": "cheapest_variant",
        "part_path": str(part_path),
        "driver": search.get("driver"),
        "driver_role": search.get("driver_role"),
        "process": str(process),
        "material": str(material),
        "lot_size": int(lot_size),
        # gate fields — surfaced top-level so a caller cannot miss them.
        "overall_flag": search.get("overall_flag"),
        "winner": search.get("winner"),
        "closest_viable": search.get("closest_viable"),
        "savings_vs_baseline": search.get("savings_vs_baseline"),
        "grade": search.get("grade"),          # 'estimate'
        "cost_variant_search": search,          # full pass-through, gate intact
        "artifacts": {},
    }
    report = _write_report(out_dir, "cheapest_variant_report.json", search)
    if report is not None:
        out["artifacts"]["report_json"] = report
    return _assert_json_safe(out)
