"""feature_change_classify — atomic, read-only. (phase-1, 2026-06-14)

Given two feature catalogs ALREADY in a shared frame (post-registration),
classify each feature as moved / resized / moved+resized / unchanged, plus
removed (present in A only) and added (present in B only).

This is the diff PAYLOAD on top of ``register_bodies``: where
``feature_fidelity_diff`` answers "how faithful is the regen" with a single
match_ratio, this answers "what *changed* between two real parts" with signed
per-dimension deltas and centroid shifts.

Pairing reuses ``feature_fidelity_diff._greedy_pair`` per kind (imported, NOT
forked) so the spatial + primary-dim gates stay identical to the rest of the
RE stack. An optional ``transform_b`` (4x4) is applied to B's positional
fields FIRST so a caller can pass a raw (un-registered) B catalog together
with the registration transform from ``register_bodies``.

Labels (per matched pair):
    - ``unchanged``     : no dim crosses ``dim_tol_pct`` AND centroid shift
                          <= ``move_tol_mm``
    - ``moved``         : centroid shift > ``move_tol_mm``, dims unchanged
    - ``resized``       : some dim crosses ``dim_tol_pct``, centroid steady
    - ``moved+resized`` : both
Unmatched-in-B  -> ``removed`` (carries A's dims, b_entry=None)
Unmatched-in-A  -> ``added``   (carries B's dims, a_entry=None)

extras schema::

    {"feature_changes": {
        "by_kind": {<kind>: [ {
            "change": str,
            "a_index": int | None,
            "b_index": int | None,
            "a_entry": dict | None,
            "b_entry": dict | None,
            "dim_deltas": [ {"name": str, "a": float, "b": float,
                             "delta": float, "pct": float | None}, ... ],
            "centroid_shift_mm": float | None,
        }, ... ], ... },
        "summary_counts": {"unchanged": N, "moved": N, "resized": N,
                            "moved+resized": N, "removed": N, "added": N},
        "transform_applied": bool,
    }}

Body is unchanged (post ``body_present``).
"""
from __future__ import annotations

import copy
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
# Reused (NOT forked) per task spec.
from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
    _KINDS,
    _greedy_pair,
    _xyz_of,
)


# Positional 3-vector keys that a rigid transform must move. Mirrors the
# coordinate key chain used by feature_fidelity_diff._xyz_of so a transformed
# B catalog pairs in the SAME frame the diff reads.
_POS_VECTOR_KEYS = (
    "entry_origin", "axis_origin", "centroid", "center", "position", "origin",
    "plane_origin", "pocket_entry_origin",
)
# Direction unit-vectors — rotated (R only) but NEVER translated.
_DIR_VECTOR_KEYS = ("axis_dir", "plane_normal", "axis_direction")


def _flatten_4x4(matrix: Any) -> tuple[np.ndarray, np.ndarray] | None:
    """Accept a 4x4 nested list or flat 12/16 affine -> (R (3,3), t (3,))."""
    if matrix is None:
        return None
    flat: list[float] = []
    for row in matrix:
        if isinstance(row, (list, tuple)):
            flat.extend(float(x) for x in row)
        else:
            flat.append(float(row))
    if len(flat) not in (12, 16):
        raise ValueError(
            "feature_change_classify: transform_b must be a 4x4 row-major "
            f"matrix or flat 12/16-float affine, got {len(flat)} values")
    r = flat[:12]
    R = np.array([[r[0], r[1], r[2]],
                  [r[4], r[5], r[6]],
                  [r[8], r[9], r[10]]], dtype=float)
    t = np.array([r[3], r[7], r[11]], dtype=float)
    return R, t


def _apply_transform_to_entry(entry: dict, R: np.ndarray, t: np.ndarray) -> dict:
    """Deep-copy ``entry`` and rigid-transform its positional vectors
    (R @ p + t) and rotate its direction vectors (R @ d). Scalar dims are
    untouched — a rigid motion preserves sizes."""
    out = copy.deepcopy(entry)
    for key in _POS_VECTOR_KEYS:
        v = out.get(key)
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            try:
                p = np.array([float(v[0]), float(v[1]), float(v[2])])
            except Exception:
                continue
            p2 = R @ p + t
            out[key] = [float(p2[0]), float(p2[1]), float(p2[2])]
    for key in _DIR_VECTOR_KEYS:
        v = out.get(key)
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            try:
                d = np.array([float(v[0]), float(v[1]), float(v[2])])
            except Exception:
                continue
            d2 = R @ d
            out[key] = [float(d2[0]), float(d2[1]), float(d2[2])]
    return out


def _transform_catalog(catalog: dict, R: np.ndarray, t: np.ndarray) -> dict:
    """Return a deep copy of ``catalog`` with every feature entry's positional
    / direction vectors transformed. Only the per-kind feature lists are
    touched; scalar metadata (bbox, thickness) is copied as-is."""
    out = copy.deepcopy(catalog or {})
    for kind in _KINDS:
        lst = out.get(kind)
        if not isinstance(lst, list):
            continue
        out[kind] = [
            _apply_transform_to_entry(e, R, t) if isinstance(e, dict) else e
            for e in lst
        ]
    return out


def _centroid(entry: dict) -> np.ndarray | None:
    xyz = _xyz_of(entry) if isinstance(entry, dict) else None
    return None if xyz is None else np.array(xyz, dtype=float)


def _dim_deltas(a: dict, b: dict) -> list[dict[str, Any]]:
    """Signed per-dimension deltas over every shared ``*_mm`` scalar key.

    pct is None when the A value is ~0 (avoids div-by-zero blow-up); the
    absolute delta still carries the signal.
    """
    deltas: list[dict[str, Any]] = []
    for key in sorted(a.keys()):
        if not key.endswith("_mm") or key not in b:
            continue
        try:
            av = float(a[key])
            bv = float(b[key])
        except Exception:
            continue
        delta = bv - av
        pct = (delta / av * 100.0) if abs(av) > 1e-9 else None
        deltas.append({
            "name": key,
            "a": round(av, 6),
            "b": round(bv, 6),
            "delta": round(delta, 6),
            "pct": (round(pct, 4) if pct is not None else None),
        })
    return deltas


def _classify_pair(
    a: dict, b: dict, dim_tol_pct: float, move_tol_mm: float
) -> tuple[str, list[dict[str, Any]], float | None]:
    """-> (label, dim_deltas, centroid_shift_mm)."""
    deltas = _dim_deltas(a, b)
    resized = False
    for d in deltas:
        pct = d["pct"]
        if pct is not None:
            if abs(pct) > dim_tol_pct:
                resized = True
        else:
            # No baseline % — fall back to absolute delta vs a small floor so
            # a 0->2mm appearance still registers as a resize.
            if abs(d["delta"]) > 1e-6:
                resized = True

    ca = _centroid(a)
    cb = _centroid(b)
    shift = None
    moved = False
    if ca is not None and cb is not None:
        shift = float(np.linalg.norm(cb - ca))
        moved = shift > move_tol_mm

    if moved and resized:
        label = "moved+resized"
    elif moved:
        label = "moved"
    elif resized:
        label = "resized"
    else:
        label = "unchanged"
    return label, deltas, (round(shift, 6) if shift is not None else None)


def _self_dims(entry: dict) -> list[dict[str, Any]]:
    """For removed/added entries (one-sided): carry the present-side dims
    with the absent side as None."""
    out: list[dict[str, Any]] = []
    for key in sorted(entry.keys()):
        if not key.endswith("_mm"):
            continue
        try:
            v = float(entry[key])
        except Exception:
            continue
        out.append({"name": key, "a": None, "b": None, "delta": None, "pct": None,
                    "value": round(v, 6)})
    return out


@skill(
    name="feature_change_classify",
    category="reverse_engineer",
    level="atomic",
    summary="Classify per-feature changes between two catalogs in a shared "
            "frame: moved / resized / moved+resized / unchanged, plus "
            "removed (A-only) and added (B-only). Reuses "
            "feature_fidelity_diff._greedy_pair for matching; optional "
            "transform_b applies a registration 4x4 to B first. Signed "
            "per-dim deltas + centroid shifts. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["feature_changes"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class FeatureChangeClassify(SkillBase):
    class Args(BaseModel):
        catalog_a: dict = Field(
            default_factory=dict,
            description="Reference catalog A (the baseline).",
        )
        catalog_b: dict = Field(
            default_factory=dict,
            description="Comparison catalog B. Assumed in A's frame UNLESS "
                        "transform_b is given.",
        )
        transform_b: list | None = Field(
            default=None,
            description="Optional rigid 4x4 (or flat 12/16 affine) mapping B "
                        "into A's frame, applied to B's positional/direction "
                        "vectors before pairing. Pass the transform_4x4 from "
                        "register_bodies to compare an un-registered B.",
        )
        dim_tol_pct: float = Field(
            default=1.0, ge=0.0,
            description="A shared *_mm dimension counts as 'resized' when its "
                        "|delta| exceeds this percent of A's value.",
        )
        move_tol_mm: float = Field(
            default=0.5, ge=0.0,
            description="A pair counts as 'moved' when its centroid shift "
                        "exceeds this many mm.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        cat_a = args.catalog_a or {}
        cat_b = args.catalog_b or {}

        transform_applied = False
        rt = _flatten_4x4(args.transform_b)
        if rt is not None:
            R, t = rt
            cat_b = _transform_catalog(cat_b, R, t)
            transform_applied = True

        by_kind: dict[str, list[dict[str, Any]]] = {}
        counts = {
            "unchanged": 0, "moved": 0, "resized": 0, "moved+resized": 0,
            "removed": 0, "added": 0,
        }

        for kind in _KINDS:
            a_list = cat_a.get(kind) or []
            b_list = cat_b.get(kind) or []
            if not a_list and not b_list:
                continue
            pairs, unmatched_a, unmatched_b = _greedy_pair(a_list, b_list, kind)
            records: list[dict[str, Any]] = []

            for ai, bi, _cost in pairs:
                a = a_list[ai] if isinstance(a_list[ai], dict) else None
                b = b_list[bi] if isinstance(b_list[bi], dict) else None
                if not (a and b):
                    continue
                label, deltas, shift = _classify_pair(
                    a, b, args.dim_tol_pct, args.move_tol_mm)
                counts[label] += 1
                records.append({
                    "change": label,
                    "a_index": ai,
                    "b_index": bi,
                    "a_entry": a,
                    "b_entry": b,
                    "dim_deltas": deltas,
                    "centroid_shift_mm": shift,
                })

            for ai in unmatched_a:
                a = a_list[ai] if isinstance(a_list[ai], dict) else None
                counts["removed"] += 1
                records.append({
                    "change": "removed",
                    "a_index": ai,
                    "b_index": None,
                    "a_entry": a,
                    "b_entry": None,
                    "dim_deltas": _self_dims(a) if a else [],
                    "centroid_shift_mm": None,
                })

            for bi in unmatched_b:
                b = b_list[bi] if isinstance(b_list[bi], dict) else None
                counts["added"] += 1
                records.append({
                    "change": "added",
                    "a_index": None,
                    "b_index": bi,
                    "a_entry": None,
                    "b_entry": b,
                    "dim_deltas": _self_dims(b) if b else [],
                    "centroid_shift_mm": None,
                })

            if records:
                by_kind[kind] = records

        report = {
            "by_kind": by_kind,
            "summary_counts": counts,
            "transform_applied": transform_applied,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"feature_changes": report},
        )
