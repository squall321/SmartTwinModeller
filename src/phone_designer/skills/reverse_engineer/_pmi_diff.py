"""_pmi_diff — pure helper for compare_parts (phase-2, 2026-06-14).

Pair the PMI / GD&T annotations of two parts (the ``extras['pmi']`` dicts that
``read_step_pmi`` returns for A and B) and report what was ``added`` /
``removed`` / ``changed`` per annotation category.

Pairing key per annotation:

  - dimensions:            (type, value-bucket, sorted datum letters)
  - geometric_tolerances:  (type, value-bucket, sorted datum letters)
  - datums:                (datum letter / name)

The value-bucket quantises the mm magnitude (default 0.5 mm) so a 10.00 mm and
a 10.02 mm callout of the same type+datums pair as the SAME annotation and are
reported as ``changed`` (with the signed value delta) rather than one
``removed`` + one ``added``. A change in datum letters or annotation type makes
them distinct annotations (removed + added) — that is a genuine design change,
not a tweak.

Honesty contract: when BOTH sides expose ZERO XCAF GDT entities (the common
case for STEP bodies that only carry a ``.pmi.json`` sidecar, or no PMI at
all) the diff falls back to the sidecar payload if present and always carries a
``note`` describing what source it used, so a caller never mistakes "no XCAF
GDT in either file" for "annotations are identical".

Return shape (consumed by compare_parts)::

    {
      "dimensions":           {"added": [...], "removed": [...], "changed": [...]},
      "geometric_tolerances": {"added": [...], "removed": [...], "changed": [...]},
      "datums":               {"added": [...], "removed": [...], "changed": [...]},
      "summary_counts": {"added": N, "removed": N, "changed": N, "unchanged": N},
      "source": "xcaf" | "sidecar" | "none",
      "notes": [str, ...],
    }
"""
from __future__ import annotations

from typing import Any

# Categories diffed, in report order. Each is a list of dict annotations on
# both the A and B pmi payloads.
_PMI_CATEGORIES = ("dimensions", "geometric_tolerances", "datums")

#: Default mm quantisation for the value bucket of a pairing key.
DEFAULT_VALUE_BUCKET_MM = 0.5


def _as_float(v: Any) -> float | None:
    try:
        if isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _value_of(ann: dict) -> float | None:
    """Primary mm magnitude of an annotation (dimension value or tolerance
    zone). Tries the common keys in order; None when no numeric value."""
    for key in ("value_mm", "value", "diameter_mm"):
        v = _as_float(ann.get(key))
        if v is not None:
            return v
    vals = ann.get("values_mm")
    if isinstance(vals, list) and vals:
        v = _as_float(vals[0])
        if v is not None:
            return v
    return None


def _datum_letters(ann: dict) -> tuple[str, ...]:
    """Sorted, upper-cased datum reference letters of an annotation."""
    refs = ann.get("datum_refs")
    if not isinstance(refs, list):
        return ()
    out = sorted(str(r).strip().upper() for r in refs if str(r).strip())
    return tuple(out)


def _value_bucket(value: float | None, bucket_mm: float) -> Any:
    """Quantise a value to the nearest ``bucket_mm`` (None passes through)."""
    if value is None or bucket_mm <= 0.0:
        return None
    return round(value / bucket_mm)


def _ann_key(category: str, ann: dict, bucket_mm: float) -> tuple:
    """Pairing key for an annotation in a category."""
    if category == "datums":
        # A datum is identified by its letter / name only.
        name = ann.get("name") or ann.get("label") or ""
        return ("datum", str(name).strip().upper())
    typ = str(ann.get("type") or "").strip().lower()
    return (typ, _value_bucket(_value_of(ann), bucket_mm), _datum_letters(ann))


def _annotation_view(category: str, ann: dict) -> dict[str, Any]:
    """Compact, JSON-clean view of an annotation for the diff report."""
    if category == "datums":
        return {
            "name": ann.get("name") or ann.get("label"),
            "label": ann.get("label"),
            "precedence_position": ann.get("precedence_position"),
        }
    return {
        "type": ann.get("type"),
        "value_mm": _value_of(ann),
        "datum_refs": list(ann.get("datum_refs") or []),
        "label": ann.get("label"),
    }


def _diff_category(
    category: str, a_list: list, b_list: list, bucket_mm: float
) -> dict[str, list]:
    """Pair annotations within one category by key; report add/remove/change.

    Two annotations with the SAME key but a different exact value are
    ``changed`` (carries signed value delta). Keys present only on A are
    ``removed``; only on B are ``added``. Multiple annotations sharing one key
    are paired positionally; surplus on either side spills to removed/added.
    """
    a_by_key: dict[tuple, list[dict]] = {}
    b_by_key: dict[tuple, list[dict]] = {}
    for ann in a_list:
        if isinstance(ann, dict):
            a_by_key.setdefault(_ann_key(category, ann, bucket_mm), []).append(ann)
    for ann in b_list:
        if isinstance(ann, dict):
            b_by_key.setdefault(_ann_key(category, ann, bucket_mm), []).append(ann)

    added: list[dict] = []
    removed: list[dict] = []
    changed: list[dict] = []

    for key in set(a_by_key) | set(b_by_key):
        a_anns = a_by_key.get(key, [])
        b_anns = b_by_key.get(key, [])
        n = min(len(a_anns), len(b_anns))
        for i in range(n):
            av = _annotation_view(category, a_anns[i])
            bv = _annotation_view(category, b_anns[i])
            if category == "datums":
                # Same letter, same bucket -> unchanged datum (no value delta).
                continue
            a_val = av.get("value_mm")
            b_val = bv.get("value_mm")
            delta = None
            if a_val is not None and b_val is not None:
                delta = round(b_val - a_val, 6)
            # Within-bucket exact-equal -> unchanged; otherwise changed.
            if delta is not None and abs(delta) > 1e-9:
                changed.append({"a": av, "b": bv, "value_delta_mm": delta})
        for a_ann in a_anns[n:]:
            removed.append(_annotation_view(category, a_ann))
        for b_ann in b_anns[n:]:
            added.append(_annotation_view(category, b_ann))

    return {"added": added, "removed": removed, "changed": changed}


def _flatten_sidecar(sidecar: Any) -> dict[str, list]:
    """Coerce a .pmi.json sidecar into the same {category: [ann, ...]} shape
    read_step_pmi exposes for XCAF. Unknown shapes degrade to empty lists.
    """
    out: dict[str, list] = {c: [] for c in _PMI_CATEGORIES}
    if not isinstance(sidecar, dict):
        return out
    for c in _PMI_CATEGORIES:
        v = sidecar.get(c)
        if isinstance(v, list):
            out[c] = [e for e in v if isinstance(e, dict)]
    return out


def _xcaf_counts(pmi: dict | None) -> int:
    if not isinstance(pmi, dict):
        return 0
    counts = pmi.get("counts")
    if isinstance(counts, dict):
        return int(sum(int(v) for v in counts.values() if isinstance(v, (int, float))))
    total = 0
    for c in _PMI_CATEGORIES:
        v = pmi.get(c)
        if isinstance(v, list):
            total += len(v)
    return total


def pmi_diff(
    pmi_a: dict | None,
    pmi_b: dict | None,
    value_bucket_mm: float = DEFAULT_VALUE_BUCKET_MM,
) -> dict[str, Any]:
    """Diff two ``read_step_pmi`` payloads. See module docstring for shape."""
    pmi_a = pmi_a or {}
    pmi_b = pmi_b or {}
    notes: list[str] = []

    a_xcaf = _xcaf_counts(pmi_a)
    b_xcaf = _xcaf_counts(pmi_b)

    # Source selection: prefer real XCAF GDT on EITHER side; else fall back to
    # the sidecar payload if either side carries one; else honest 'none'.
    if a_xcaf > 0 or b_xcaf > 0:
        source = "xcaf"
        a_cat = {c: (pmi_a.get(c) or []) for c in _PMI_CATEGORIES}
        b_cat = {c: (pmi_b.get(c) or []) for c in _PMI_CATEGORIES}
        if a_xcaf == 0:
            notes.append("A carries no XCAF GDT; only B's annotations diffed.")
        if b_xcaf == 0:
            notes.append("B carries no XCAF GDT; only A's annotations diffed.")
    else:
        side_a = _flatten_sidecar(pmi_a.get("sidecar"))
        side_b = _flatten_sidecar(pmi_b.get("sidecar"))
        has_sidecar = any(side_a[c] for c in _PMI_CATEGORIES) or any(
            side_b[c] for c in _PMI_CATEGORIES
        )
        if has_sidecar:
            source = "sidecar"
            a_cat, b_cat = side_a, side_b
            notes.append(
                "Neither STEP body carries XCAF GDT entities; PMI diff computed "
                "from the .pmi.json sidecar(s) instead."
            )
        else:
            source = "none"
            a_cat = {c: [] for c in _PMI_CATEGORIES}
            b_cat = {c: [] for c in _PMI_CATEGORIES}
            notes.append(
                "Neither part exposes PMI / GD&T (no XCAF GDT and no .pmi.json "
                "sidecar). PMI diff is empty — this is not a claim that the "
                "annotations are identical, only that none were found."
            )

    by_category: dict[str, dict[str, list]] = {}
    counts = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
    for c in _PMI_CATEGORIES:
        d = _diff_category(c, a_cat[c], b_cat[c], value_bucket_mm)
        by_category[c] = d
        counts["added"] += len(d["added"])
        counts["removed"] += len(d["removed"])
        counts["changed"] += len(d["changed"])
        # unchanged = paired keys that produced no add/remove/change record.
        paired = min(len(a_cat[c]), len(b_cat[c]))
        counts["unchanged"] += max(
            0, paired - len(d["changed"])
        ) if c != "datums" else 0

    result: dict[str, Any] = dict(by_category)
    result["summary_counts"] = counts
    result["source"] = source
    result["notes"] = notes
    return result
