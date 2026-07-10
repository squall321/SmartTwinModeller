"""ONE canonicaliser for the project's TWO process-name vocabularies.

The codebase historically grew two spellings for the same manufacturing
processes, so callers had to know which one each skill wanted:

* COST vocabulary — ``estimate_cost.process`` / ``recommend_process``
  candidate keys:
      cnc_3axis, cnc_5axis, injection_mold_pa, sheet_laser_brake,
      sheet_turret_brake, sheet_progressive_die
      (+ advisory keys die_cast_al, 3d_printing; catalog code
      sheet_metal_stamp)
* DFM vocabulary — ``dfm_verdict`` / ``repair_dfm`` /
  ``emit_quality_report`` process names:
      cnc_milling, injection_molding, die_casting, sheet_metal, 3d_printing

This module is the single alias table. Entry points that ACCEPT process
names run their input through it so BOTH spellings work; every skill's
OUTPUT keeps its existing canonical names (byte-stability for existing
consumers and tests — no output renames).

Rules (deliberate, not incidental):

* cnc_3axis and cnc_5axis are ONE DFM family — DFM ships a single
  ``cnc_milling`` rule set (5-axis differences are handled inside
  dfm_verdict as the undercut-recovery flag) — so both map to
  ``cnc_milling`` for DFM purposes.
* ``cnc_milling`` as a SINGLE cost process maps to ``cnc_3axis`` (the
  family default). ``cnc_5axis`` must be named explicitly: its 2.5x cost
  factor is a deliberate caller choice, never implied.
* ``cnc_milling`` in a cost-CANDIDATE list expands to the whole family
  ``[cnc_3axis, cnc_5axis]`` — a candidate list means "consider the
  family".
* Names with NO single unambiguous cost target (``sheet_metal`` -> three
  cost models, ``die_casting``/``3d_printing`` -> no cost model) are NOT
  force-mapped by :func:`to_cost_process`; they pass through unchanged so
  each skill's existing honest unknown-handling still fires.
* Unknown names ALWAYS pass through unchanged (original string, original
  case) — the accepting skill's own refusal stays byte-identical and
  reachable.

Drift guards: tests/skills/test_process_aliases.py asserts these tables
against dfm_verdict._PROCESS_TO_CODE and recommend_process._CANDIDATES.
"""
from __future__ import annotations

from collections.abc import Iterable

#: canonical DFM process names (dfm_verdict's known set).
DFM_CANONICAL: frozenset[str] = frozenset({
    "cnc_milling",
    "injection_molding",
    "die_casting",
    "sheet_metal",
    "3d_printing",
})

#: canonical cost-side process names (estimate_cost models + recommend_process
#: candidate keys + the sheet_metal_stamp catalog code).
COST_CANONICAL: frozenset[str] = frozenset({
    "cnc_3axis",
    "cnc_5axis",
    "injection_mold_pa",
    "sheet_laser_brake",
    "sheet_turret_brake",
    "sheet_progressive_die",
    "sheet_metal_stamp",
    "die_cast_al",
    "3d_printing",
})

#: cost spelling -> its DFM family name.
_COST_TO_DFM: dict[str, str] = {
    "cnc_3axis": "cnc_milling",
    "cnc_5axis": "cnc_milling",
    "injection_mold_pa": "injection_molding",
    "die_cast_al": "die_casting",
    "sheet_metal_stamp": "sheet_metal",
    "sheet_laser_brake": "sheet_metal",
    "sheet_turret_brake": "sheet_metal",
    "sheet_progressive_die": "sheet_metal",
}

#: DFM spelling -> the single-process cost default (ONLY where unambiguous).
_DFM_TO_COST: dict[str, str] = {
    "cnc_milling": "cnc_3axis",          # family default; 5-axis is explicit
    "injection_molding": "injection_mold_pa",
}

#: DFM family name -> every cost candidate key in the family (for candidate
#: LISTS, where "the family" is the honest expansion).
_DFM_TO_COST_FAMILY: dict[str, tuple[str, ...]] = {
    "cnc_milling": ("cnc_3axis", "cnc_5axis"),
    "injection_molding": ("injection_mold_pa",),
    "sheet_metal": ("sheet_laser_brake", "sheet_turret_brake",
                    "sheet_progressive_die"),
    "die_casting": ("die_cast_al",),
    "3d_printing": ("3d_printing",),
}


def _norm(name: object) -> object:
    """Lower/strip a string for table lookup; non-strings pass through."""
    return name.strip().lower() if isinstance(name, str) else name


def to_dfm_process(name: str) -> str:
    """Canonical DFM spelling for *name*.

    Cost spellings fold into their DFM family (cnc_3axis/cnc_5axis ->
    cnc_milling, ...); canonical DFM names are returned normalised; unknown
    names are returned EXACTLY as given (so downstream unknown-handling and
    refusal texts stay byte-identical).
    """
    n = _norm(name)
    if n in DFM_CANONICAL:
        return n  # type: ignore[return-value]
    return _COST_TO_DFM.get(n, name)  # type: ignore[arg-type]


def to_cost_process(name: str) -> str:
    """Canonical single cost-process spelling for *name*.

    DFM spellings with ONE unambiguous cost model map to it (cnc_milling ->
    cnc_3axis, injection_molding -> injection_mold_pa); canonical cost names
    are returned normalised; everything else (ambiguous families like
    sheet_metal, unpriced die_casting, unknowns) is returned EXACTLY as
    given so the caller's existing honest fallback/refusal fires unchanged.
    """
    n = _norm(name)
    if n in COST_CANONICAL:
        return n  # type: ignore[return-value]
    return _DFM_TO_COST.get(n, name)  # type: ignore[arg-type]


def canon_dfm_processes(
    names: Iterable[str],
) -> tuple[list[str], dict[str, str]]:
    """Canonicalise a DFM process LIST (order-preserving, deduplicated).

    Returns ``(canonical_list, aliases)`` where *aliases* maps only the
    entries that CHANGED spelling (original -> canonical). Canonical input
    round-trips bit-identically: ``canon_dfm_processes(['cnc_milling'])
    == (['cnc_milling'], {})``.
    """
    out: list[str] = []
    aliases: dict[str, str] = {}
    for name in names:
        canon = to_dfm_process(name)
        if canon != name:
            aliases[name] = canon
        if canon not in out:
            out.append(canon)
    return out, aliases


def expand_cost_candidates(
    names: Iterable[str],
) -> tuple[list[str], dict[str, list[str]]]:
    """Expand a cost-CANDIDATE list (order-preserving, deduplicated).

    DFM family names expand to every cost candidate key in the family
    (``cnc_milling`` -> ``[cnc_3axis, cnc_5axis]``); canonical cost keys pass
    through; unknown names pass through EXACTLY as given (the caller's own
    validation refuses them, unchanged). Returns ``(expanded, aliases)``
    where *aliases* records only the entries that changed.
    """
    out: list[str] = []
    aliases: dict[str, list[str]] = {}
    for name in names:
        n = _norm(name)
        if n in COST_CANONICAL:
            targets: list[str] = [n]  # type: ignore[list-item]
        elif n in _DFM_TO_COST_FAMILY:
            targets = list(_DFM_TO_COST_FAMILY[n])  # type: ignore[index]
        else:
            targets = [name]  # unknown -> untouched
        if targets != [name]:
            aliases[name] = list(targets)
        for t in targets:
            if t not in out:
                out.append(t)
    return out, aliases
